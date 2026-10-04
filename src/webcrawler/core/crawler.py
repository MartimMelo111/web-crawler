"""The crawl engine: a fixed pool of async workers taking URLs from a shared queue."""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from webcrawler.services.fetcher import Fetcher
from webcrawler.services.robots import RobotsPolicy
from webcrawler.tools.parser import extract_links
from webcrawler.tools.urls import DomainScope, normalize_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PageResult:
    """A crawled page and every link found on it (including off-site links)."""

    url: str
    status: int | None
    links: tuple[str, ...]
    redirect: bool = False


@dataclass(slots=True)
class CrawlStats:
    pages: int = 0
    redirects: int = 0
    skipped: int = 0  # non-HTML responses and URLs disallowed by robots.txt
    errors: int = 0
    elapsed: float = 0.0


PageCallback = Callable[[PageResult], None]


class Crawler:
    """Crawls every page reachable from ``start_url`` on the same hostname.

    ``concurrency`` worker tasks pull URLs from an :class:`asyncio.Queue`.
    Shared state (``_seen``, the stats) needs no locks because it is only
    changed between ``await`` points. Each URL is added to ``_seen`` *when it
    is enqueued*, not when it is fetched, so it is fetched at most once.
    """

    def __init__(
        self,
        start_url: str,
        fetcher: Fetcher,
        *,
        on_page: PageCallback,
        concurrency: int = 20,
        max_pages: int | None = None,
        robots: RobotsPolicy | None = None,
    ) -> None:
        normalized = normalize_url(start_url)
        if normalized is None:
            raise ValueError(f"not a valid http(s) URL: {start_url!r}")
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if max_pages is not None and max_pages < 1:
            raise ValueError("max_pages must be >= 1")

        self.start_url = normalized
        self.scope = DomainScope.from_url(normalized)
        self._fetcher = fetcher
        self._on_page = on_page
        self._concurrency = concurrency
        self._max_pages = max_pages
        self._robots = robots
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._seen: set[str] = set()
        self._scheduled = 0
        self._started = False
        self.stats = CrawlStats()

    async def run(self) -> CrawlStats:
        if self._started:
            raise RuntimeError("a Crawler instance can only be run once")
        self._started = True

        started_at = time.perf_counter()
        self._enqueue(self.start_url)
        try:
            # The group waits for every worker before exiting, and cancels them all if
            # one fails or if run() itself is cancelled, so no task outlives the crawl.
            async with asyncio.TaskGroup() as group:
                workers = [group.create_task(self._worker()) for _ in range(self._concurrency)]
                await self._queue.join()
                for worker in workers:  # idle and waiting on get(): stop them
                    worker.cancel()
        finally:
            self.stats.elapsed = time.perf_counter() - started_at
        return self.stats

    def _enqueue(self, url: str) -> None:
        if url in self._seen or url not in self.scope:
            return
        if self._max_pages is not None and self._scheduled >= self._max_pages:
            return
        self._seen.add(url)
        if self._robots is not None and not self._robots.allows(url):
            logger.debug("disallowed by robots.txt: %s", url)
            self.stats.skipped += 1
            return
        self._scheduled += 1
        self._queue.put_nowait(url)

    async def _worker(self) -> None:
        while True:
            url = await self._queue.get()
            try:
                await self._process(url)
            except Exception:
                # A bug triggered by one page must not kill the worker: it would stop
                # taking URLs, and the TaskGroup would abort the whole crawl.
                logger.exception("unexpected error while processing %s", url)
                self.stats.errors += 1
            finally:
                self._queue.task_done()

    async def _process(self, url: str) -> None:
        result = await self._fetcher.fetch(url)

        if result.error is not None:
            logger.warning("%s: %s", url, result.error)
            self.stats.errors += 1
            return

        if result.redirect_to is not None:
            self.stats.redirects += 1
            self._report_and_queue(
                PageResult(url, result.status, (result.redirect_to,), redirect=True)
            )
            return

        if result.body is None:
            logger.debug("skipping non-HTML response: %s", url)
            self.stats.skipped += 1
            return

        links = tuple(extract_links(result.body, url))
        self.stats.pages += 1
        self._report_and_queue(PageResult(url, result.status, links))

    def _report_and_queue(self, page: PageResult) -> None:
        self._on_page(page)
        for link in page.links:
            self._enqueue(link)
