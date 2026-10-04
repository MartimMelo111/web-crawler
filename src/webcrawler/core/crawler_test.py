from collections.abc import Callable

import pytest

from webcrawler.core.crawler import Crawler, PageResult
from webcrawler.services.fetcher import Fetcher
from webcrawler.services.robots import RobotsPolicy
from webcrawler.testing.builders import (
    random_host,
    random_int,
    random_path,
    random_paths,
    random_string,
    random_url,
    random_user_agent,
)
from webcrawler.testing.mock_site import MockSite, no_sleep


async def crawl(
    site: MockSite, start: str | None = None, **kwargs: object
) -> tuple[list[PageResult], Crawler]:
    pages: list[PageResult] = []
    async with site.client() as client:
        crawler = Crawler(
            start or site.root,
            Fetcher(client, retries=0, sleep=no_sleep),
            on_page=pages.append,
            **kwargs,  # type: ignore[arg-type]
        )
        await crawler.run()
    return pages, crawler


def by_url(pages: list[PageResult]) -> dict[str, tuple[str, ...]]:
    return {page.url: page.links for page in pages}


def root_with_children(site: MockSite, count: int) -> list[str]:
    """The root links to ``count`` pages that have no links of their own."""
    paths = random_paths(count)
    site.page(site.root, *paths)
    for path in paths:
        site.page(site.url(path))
    return [site.url(path) for path in paths]


class TestCrawl:
    """Core traversal: every reachable page is fetched exactly once."""

    async def test_crawls_every_reachable_page_once(self, site: MockSite) -> None:
        a, b, c = random_paths(3)
        site.page(site.root, a, b)
        site.page(site.url(a), b, "/", c)
        site.page(site.url(b), a)
        site.page(site.url(c))

        pages, crawler = await crawl(site)

        assert by_url(pages) == {
            site.root: (site.url(a), site.url(b)),
            site.url(a): (site.url(b), site.root, site.url(c)),
            site.url(b): (site.url(a),),
            site.url(c): (),
        }
        assert sorted(site.requests) == sorted(by_url(pages))  # no duplicate fetches
        assert crawler.stats.pages == 4

    async def test_start_url_is_normalised(self, site: MockSite) -> None:
        site.page(site.root)
        pages, _ = await crawl(site, f"HTTPS://{site.host.upper()}#{random_string()}")
        assert [page.url for page in pages] == [site.root]

    async def test_cannot_be_reused(self, site: MockSite) -> None:
        site.page(site.root)
        _, crawler = await crawl(site)
        with pytest.raises(RuntimeError, match="only be run once"):
            await crawler.run()

    @pytest.mark.parametrize(
        ("option", "value", "message"),
        [
            ("start_url", lambda: f"mailto:{random_string()}@{random_host()}", "valid http"),
            ("concurrency", lambda: -random_int(0, 100), "concurrency"),
            ("max_pages", lambda: -random_int(0, 100), "max_pages"),
        ],
        ids=["start_url", "concurrency", "max_pages"],
    )
    def test_invalid_arguments(
        self, site: MockSite, option: str, value: Callable[[], object], message: str
    ) -> None:
        options: dict[str, object] = {"start_url": site.root, option: value()}
        with pytest.raises(ValueError, match=message):
            Crawler(fetcher=Fetcher(site.client()), on_page=print, **options)  # type: ignore[arg-type]


class TestScope:
    """Only the start URL's exact hostname is crawled; other links are only reported."""

    async def test_reports_but_does_not_follow_external_links_or_subdomains(
        self, site: MockSite
    ) -> None:
        off_site = (
            random_url(),
            f"https://{random_string()}.{site.host}/",
            f"https://www.{site.host}/",
        )
        site.page(site.root, *off_site)

        pages, _ = await crawl(site)

        assert pages == [PageResult(site.root, 200, off_site)]
        assert site.requests == [site.root]

    async def test_follows_both_schemes_on_same_host(self, site: MockSite) -> None:
        plain = site.url(random_path(), scheme="http")
        site.page(site.root, plain)
        site.page(plain)
        pages, _ = await crawl(site)
        assert set(by_url(pages)) == {site.root, plain}


class TestRedirects:
    """A redirect is reported as a page linking to its target."""

    async def test_in_scope_redirect_is_followed(self, site: MockSite) -> None:
        old, new = random_paths(2)
        site.page(site.root, old)
        site.redirect(site.url(old), new)
        site.page(site.url(new))

        pages, crawler = await crawl(site)

        assert PageResult(site.url(old), 301, (site.url(new),), redirect=True) in pages
        assert site.url(new) in by_url(pages)
        assert crawler.stats.redirects == 1

    async def test_off_site_redirect_is_not_followed(self, site: MockSite) -> None:
        out, elsewhere = random_path(), random_url()
        site.page(site.root, out)
        site.redirect(site.url(out), elsewhere)
        await crawl(site)
        assert elsewhere not in site.requests


class TestErrors:
    """Failures are counted and never stop the crawl."""

    async def test_errors_and_non_html_are_counted_not_reported(self, site: MockSite) -> None:
        missing, document, ok = random_paths(3)
        site.page(site.root, missing, document, ok)
        site.add(site.url(document), headers={"content-type": "application/pdf"})
        site.page(site.url(ok))

        pages, crawler = await crawl(site)

        assert set(by_url(pages)) == {site.root, site.url(ok)}
        assert crawler.stats.errors == 1
        assert crawler.stats.skipped == 1

    async def test_unexpected_exception_does_not_hang_the_crawl(self, site: MockSite) -> None:
        child = random_path()
        site.page(site.root, child)
        site.page(site.url(child))
        pages: list[PageResult] = []

        def flaky_callback(page: PageResult) -> None:
            if page.url == site.root:
                pages.append(page)
                return
            raise RuntimeError(random_string())

        async with site.client() as client:
            crawler = Crawler(site.root, Fetcher(client), on_page=flaky_callback)
            stats = await crawler.run()

        assert stats.errors == 1
        assert len(pages) == 1


class TestLimits:
    """``max_pages`` and ``concurrency``."""

    async def test_max_pages_limits_fetches(self, site: MockSite) -> None:
        size = random_int(20, 60)
        limit = random_int(1, size)
        root_with_children(site, size)

        await crawl(site, max_pages=limit)

        assert len(site.requests) == limit

    async def test_requests_run_concurrently_up_to_the_limit(self, site: MockSite) -> None:
        concurrency = random_int(2, 10)
        size = concurrency * random_int(2, 4)
        site.delay = 0.01
        root_with_children(site, size)

        await crawl(site, concurrency=concurrency)

        assert site.max_in_flight == concurrency
        assert len(site.requests) == size + 1


class TestRobots:
    """URLs disallowed by robots.txt are never fetched."""

    async def test_disallowed_urls_are_skipped(self, site: MockSite) -> None:
        private, public = random_paths(2)
        blocked = f"{private}{random_path()}"
        site.page(site.root, blocked, public)
        site.page(site.url(public))
        robots = RobotsPolicy.from_lines(
            ["User-agent: *", f"Disallow: {private}/"], random_user_agent()
        )

        _, crawler = await crawl(site, robots=robots)

        assert site.url(blocked) not in site.requests
        assert site.url(public) in site.requests
        assert crawler.stats.skipped == 1
