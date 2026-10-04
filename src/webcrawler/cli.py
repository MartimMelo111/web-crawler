"""Command line entry point."""

import argparse
import asyncio
import contextlib
import io
import logging
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import httpx

from webcrawler.core.crawler import Crawler, CrawlStats, PageCallback
from webcrawler.output.reporters import REPORTERS, CombinedReporter, JsonLinesReporter
from webcrawler.services.fetcher import DEFAULT_USER_AGENT, Fetcher, create_client
from webcrawler.services.robots import RobotsPolicy
from webcrawler.tools.urls import normalize_url

logger = logging.getLogger("webcrawler")

RESULTS_DIR = Path("results")


MAX_NAME_LENGTH = 100  # keeps paths well under Windows' 260-character limit


def results_path(start_url: str, now: datetime) -> Path:
    """``results/<start URL>_<date>_<time>.jsonl``, so every run keeps its own file.

    The URL becomes a safe file name: the scheme is dropped and every run of
    characters other than letters, digits, ``.`` and ``-`` becomes ``_``, so
    ``https://www.rdt.com/coe/innovation`` becomes ``www.rdt.com_coe_innovation``.
    """
    normalized = normalize_url(start_url) or start_url
    without_scheme = normalized.split("://", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9.-]+", "_", without_scheme)[:MAX_NAME_LENGTH].strip("_.-")
    return RESULTS_DIR / f"{name}_{now:%Y-%m-%d_%H-%M-%S}.jsonl"


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be >= 1, got {number}")
    return number


def _non_negative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"must be >= 0, got {number}")
    return number


def _positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError(f"must be > 0, got {number}")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="webcrawler",
        description=(
            "Crawl every page on a single domain, printing each page's URL "
            "followed by all the URLs found on it."
        ),
    )
    parser.add_argument("url", help="base URL to start crawling from, e.g. https://example.com")
    parser.add_argument(
        "-c",
        "--concurrency",
        type=_positive_int,
        default=20,
        help="maximum number of requests in flight at once (default: %(default)s)",
    )
    parser.add_argument(
        "-n",
        "--max-pages",
        type=_positive_int,
        default=None,
        help="stop after fetching this many URLs (default: no limit)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=_positive_float,
        default=10.0,
        help="per-request timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "-r",
        "--retries",
        type=_non_negative_int,
        default=2,
        help="retries for network errors and 429/5xx responses (default: %(default)s)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=sorted(REPORTERS),
        default="text",
        help="output format (default: %(default)s)",
    )
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="User-Agent header")
    parser.add_argument("--ignore-robots", action="store_true", help="do not honour robots.txt")
    parser.add_argument("--no-http2", action="store_true", help="disable HTTP/2")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="-v info, -vv debug")
    return parser


async def crawl(
    args: argparse.Namespace,
    on_page: PageCallback,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CrawlStats:
    """Build the client, robots policy and crawler from parsed arguments and connect them.

    ``transport`` lets tests substitute an in-memory HTTP transport.
    """
    client = create_client(
        user_agent=args.user_agent,
        timeout=args.timeout,
        max_connections=args.concurrency,
        http2=not args.no_http2,
        transport=transport,
    )
    async with client:
        robots = None
        if not args.ignore_robots:
            robots = await RobotsPolicy.load(client, args.url, args.user_agent)
        crawler = Crawler(
            args.url,
            Fetcher(client, retries=args.retries),
            on_page=on_page,
            concurrency=args.concurrency,
            max_pages=args.max_pages,
            robots=robots,
        )
        return await crawler.run()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if normalize_url(args.url) is None:
        parser.error(f"not a valid http(s) URL: {args.url!r}")

    logging.basicConfig(
        level=(logging.WARNING, logging.INFO, logging.DEBUG)[min(args.verbose, 2)],
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Discovered URLs can contain any Unicode; don't crash on a legacy console codepage.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="backslashreplace")

    output_path = results_path(args.url, datetime.now())
    with contextlib.ExitStack() as files:
        # Opened before crawling, so a bad path fails at once instead of after the crawl.
        # Pages are written as they finish, so an interrupted crawl keeps what it found.
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output = files.enter_context(output_path.open("w", encoding="utf-8"))
        except OSError as exc:
            parser.error(f"cannot write to {str(output_path)!r}: {exc.strerror}")
        reporter = CombinedReporter(
            REPORTERS[args.format](sys.stdout),  # the terminal
            JsonLinesReporter(output),  # the results file
        )
        try:
            stats = asyncio.run(crawl(args, reporter))
        except KeyboardInterrupt:
            print(f"interrupted; results so far are in {output_path}", file=sys.stderr)
            return 130
        except BrokenPipeError:  # e.g. `webcrawler URL | head`
            return 0

    print(
        f"crawled {stats.pages} pages, {stats.redirects} redirects, "
        f"{stats.skipped} skipped, {stats.errors} errors in {stats.elapsed:.2f}s",
        file=sys.stderr,
    )
    print(f"results written to {output_path}", file=sys.stderr)
    # Nothing crawled (errors, blocked by robots.txt, redirected off-site) is a failure.
    return 0 if stats.pages > 0 else 1
