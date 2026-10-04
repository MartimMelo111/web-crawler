import argparse
import io
import json
import runpy
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

import pytest

from webcrawler.cli import RESULTS_DIR, build_parser, crawl, main, results_path
from webcrawler.core.crawler import CrawlStats, PageCallback, PageResult
from webcrawler.output.reporters import TextReporter
from webcrawler.testing.builders import (
    random_host,
    random_int,
    random_path,
    random_paths,
    random_string,
    random_url,
)
from webcrawler.testing.mock_site import MockSite

MockCrawl = Callable[[argparse.Namespace, PageCallback], Awaitable[CrawlStats]]


@pytest.fixture(autouse=True)
def in_empty_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test in its own empty folder, so ``main()`` never writes into the repo."""
    monkeypatch.chdir(tmp_path)


def mock_crawl(stats: CrawlStats, *pages: PageResult) -> MockCrawl:
    """Replace ``cli.crawl`` so ``main`` can be tested without any HTTP."""

    async def run(args: argparse.Namespace, on_page: PageCallback) -> CrawlStats:
        for page in pages:
            on_page(page)
        return stats

    return run


def mock_crawl_raising(exc: BaseException, *pages: PageResult) -> MockCrawl:
    """Like ``mock_crawl``, but raises ``exc`` after reporting ``pages``."""

    async def run(args: argparse.Namespace, on_page: PageCallback) -> CrawlStats:
        for page in pages:
            on_page(page)
        raise exc

    return run


class TestCrawlWiring:
    """``crawl()`` builds the client, robots policy and crawler from the arguments."""

    async def test_wires_everything_together(self, site: MockSite) -> None:
        allowed, private = random_paths(2)
        external = random_url()
        site.add(
            site.url("/robots.txt"),
            headers={},
            body=f"User-agent: *\nDisallow: {private}\n".encode(),
        )
        site.page(site.root, allowed, private, external)
        site.page(site.url(allowed))
        out = io.StringIO()

        args = build_parser().parse_args([site.root, "--concurrency", str(random_int(1, 20))])
        stats = await crawl(args, TextReporter(out), transport=site.transport)

        assert stats.pages == 2
        assert stats.skipped == 1
        assert site.url(private) not in site.requests
        assert out.getvalue().splitlines()[:4] == [
            site.root,
            f"  -> {site.url(allowed)}",
            f"  -> {site.url(private)}",
            f"  -> {external}",
        ]

    async def test_ignore_robots_skips_robots_fetch(self, site: MockSite) -> None:
        site.page(site.root)
        args = build_parser().parse_args([site.root, "--ignore-robots"])
        await crawl(args, lambda page: None, transport=site.transport)
        assert site.requests == [site.root]


class TestArguments:
    """Command line parsing and validation."""

    @pytest.mark.parametrize(
        "build_argv",
        [
            lambda url: [random_string()],
            lambda url: [f"ftp://{random_host()}{random_path()}"],
            lambda url: [url, "--concurrency", str(-random_int(0, 100))],
            lambda url: [url, "--max-pages", str(-random_int(0, 100))],
            lambda url: [url, "--timeout", str(-random_int(0, 100))],
            lambda url: [url, "--retries", str(-random_int(1, 100))],
            lambda url: [url, "--format", random_string()],
        ],
        ids=[
            "not-a-url",
            "ftp",
            "concurrency",
            "max-pages",
            "timeout",
            "retries",
            "format",
        ],
    )
    def test_invalid_values_exit_with_usage_error(
        self,
        build_argv: Callable[[str], list[str]],
    ) -> None:
        with pytest.raises(SystemExit) as excinfo:
            main(build_argv(random_url()))
        assert excinfo.value.code == 2

    def test_valid_options_are_parsed(self) -> None:
        concurrency, max_pages, retries = (random_int(1, 100) for _ in range(3))
        timeout = random_int(1, 1000) / 10
        args = build_parser().parse_args(
            [random_url(), "-c", str(concurrency), "-n", str(max_pages),
             "-t", str(timeout), "-r", str(retries), "-f", "jsonl", "-vv"]
        )  # fmt: skip
        assert (args.concurrency, args.max_pages) == (concurrency, max_pages)
        assert (args.timeout, args.retries) == (timeout, retries)
        assert (args.format, args.verbose) == ("jsonl", 2)


class TestMain:
    """``main()``: exit codes, the summary line, the output stream and interruptions."""

    @pytest.mark.parametrize(
        ("any_pages", "any_errors", "exit_code"),
        [(True, False, 0), (True, True, 0), (False, False, 1), (False, True, 1)],
    )
    def test_exit_code_and_summary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        any_pages: bool,
        any_errors: bool,
        exit_code: int,
    ) -> None:
        stats = CrawlStats(
            pages=random_int() if any_pages else 0,
            redirects=random_int(),
            skipped=random_int(),
            errors=random_int() if any_errors else 0,
            elapsed=random_int() / 100,
        )
        monkeypatch.setattr("webcrawler.cli.crawl", mock_crawl(stats))

        assert main([random_url()]) == exit_code
        assert (
            f"crawled {stats.pages} pages, {stats.redirects} redirects, {stats.skipped} skipped, "
            f"{stats.errors} errors in {stats.elapsed:.2f}s" in capsys.readouterr().err
        )

    def test_writes_results_to_any_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = io.StringIO()  # not a TextIOWrapper, so the encoding fallback is skipped
        monkeypatch.setattr(sys, "stdout", out)
        page = PageResult(random_url(), 200, (random_url(),))
        monkeypatch.setattr("webcrawler.cli.crawl", mock_crawl(CrawlStats(pages=1), page))

        assert main([page.url, "-f", "jsonl"]) == 0
        assert json.loads(out.getvalue())["url"] == page.url

    def test_ctrl_c_exits_130(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("webcrawler.cli.crawl", mock_crawl_raising(KeyboardInterrupt()))
        assert main([random_url()]) == 130
        assert "interrupted" in capsys.readouterr().err

    def test_broken_pipe_exits_quietly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("webcrawler.cli.crawl", mock_crawl_raising(BrokenPipeError()))
        assert main([random_url()]) == 0

    def test_python_dash_m_runs_the_cli(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["webcrawler", "--help"])
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("webcrawler", run_name="__main__")
        assert excinfo.value.code == 0
        assert "usage: webcrawler" in capsys.readouterr().out


def random_pages() -> list[PageResult]:
    return [
        PageResult(random_url(), 200, tuple(random_url() for _ in range(random_int(0, 5))))
        for _ in range(random_int(1, 5))
    ]


def read_json_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def as_record(page: PageResult) -> dict[str, object]:
    return {"url": page.url, "status": page.status, "redirect": False, "links": list(page.links)}


def saved_file() -> Path:
    """The one results file a run wrote."""
    (path,) = RESULTS_DIR.iterdir()
    return path


def random_time() -> datetime:
    return datetime(
        random_int(2000, 2100),
        random_int(1, 12),
        random_int(1, 28),
        random_int(0, 23),
        random_int(0, 59),
        random_int(0, 59),
    )


class TestResultsFile:
    """Results go to the terminal and, as JSON Lines, to a file in results/."""

    def test_terminal_gets_text_and_file_gets_json(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pages = random_pages()
        monkeypatch.setattr("webcrawler.cli.crawl", mock_crawl(CrawlStats(pages=1), *pages))

        assert main([random_url()]) == 0

        out, err = capsys.readouterr()
        assert [line for line in out.splitlines() if not line.startswith("  -> ")] == [
            page.url for page in pages
        ]
        assert read_json_lines(saved_file()) == [as_record(page) for page in pages]
        assert f"results written to {saved_file()}" in err

    def test_file_is_named_after_the_start_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        host, path = random_host(), random_path(2)
        monkeypatch.setattr("webcrawler.cli.crawl", mock_crawl(CrawlStats(pages=1)))

        main([f"https://{host}{path}"])

        assert saved_file().name.startswith(f"{host}{path.replace('/', '_')}_")
        assert saved_file().suffix == ".jsonl"

    def test_pages_found_before_ctrl_c_are_kept(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        pages = random_pages()
        monkeypatch.setattr("webcrawler.cli.crawl", mock_crawl_raising(KeyboardInterrupt(), *pages))

        assert main([random_url()]) == 130
        assert read_json_lines(saved_file()) == [as_record(page) for page in pages]
        assert f"results so far are in {saved_file()}" in capsys.readouterr().err

    def test_unwritable_results_folder_is_an_error_before_crawling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        crawled = False

        async def never_called(args: argparse.Namespace, on_page: PageCallback) -> CrawlStats:
            nonlocal crawled
            crawled = True
            return CrawlStats()

        monkeypatch.setattr("webcrawler.cli.crawl", never_called)
        RESULTS_DIR.write_text(random_string())  # a file where the folder should be

        with pytest.raises(SystemExit) as excinfo:
            main([random_url()])
        assert excinfo.value.code == 2
        assert not crawled


class TestResultsPath:
    """Turning the start URL and the time into a file name."""

    def test_name_is_the_url_without_scheme_then_date_and_time(self) -> None:
        host, now = random_host(), random_time()
        first, second = random_string(), random_string()
        path = results_path(f"https://{host}/{first}/{second}", now)
        assert path == RESULTS_DIR / f"{host}_{first}_{second}_{now:%Y-%m-%d_%H-%M-%S}.jsonl"

    def test_site_root_is_just_the_host(self) -> None:
        host, now = random_host(), random_time()
        assert results_path(f"https://{host}/", now).name == f"{host}_{now:%Y-%m-%d_%H-%M-%S}.jsonl"

    def test_url_is_normalised_first(self) -> None:
        host, path, now = random_host(), random_path(), random_time()
        assert results_path(f"HTTPS://{host.upper()}{path}#{random_string()}", now) == (
            results_path(f"https://{host}{path}", now)
        )

    def test_characters_not_allowed_in_file_names_become_underscores(self) -> None:
        port = random_int(1024, 65535)
        name = results_path(f"http://[::1]:{port}/?{random_string()}=1&x", random_time()).name
        assert not set(name) & set(r'<>:"/\|?*[]=& ')
        assert name.startswith(f"1_{port}_")

    def test_long_urls_are_shortened(self) -> None:
        path = "/" + "-".join(random_string() for _ in range(30))  # far over 100 characters
        name = results_path(f"https://{random_host()}{path}", random_time()).stem
        url_part = name.rsplit("_", 2)[0]  # drop "_<date>_<time>"
        assert len(url_part) <= 100
        assert url_part[-1] not in "_.-"
