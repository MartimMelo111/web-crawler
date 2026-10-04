import io
import json

from webcrawler.core.crawler import PageResult
from webcrawler.output.reporters import (
    REPORTERS,
    CombinedReporter,
    JsonLinesReporter,
    TextReporter,
)
from webcrawler.testing.builders import random_int, random_string, random_url


def random_links(count: int | None = None) -> tuple[str, ...]:
    return tuple(random_url() for _ in range(random_int(1, 10) if count is None else count))


class TestTextReporter:
    """Human-readable output."""

    def test_formats_page_and_links(self) -> None:
        page = PageResult(random_url(), 200, random_links())
        out = io.StringIO()
        TextReporter(out)(page)
        assert out.getvalue() == "".join(
            [f"{page.url}\n", *(f"  -> {link}\n" for link in page.links)]
        )

    def test_formats_page_without_links(self) -> None:
        page = PageResult(random_url(), 200, ())
        out = io.StringIO()
        TextReporter(out)(page)
        assert out.getvalue() == f"{page.url}\n"

    def test_formats_redirects(self) -> None:
        status = random_int(300, 308)
        page = PageResult(random_url(), status, random_links(1), redirect=True)
        out = io.StringIO()
        TextReporter(out)(page)
        assert out.getvalue() == f"{page.url} [redirect {status}]\n  -> {page.links[0]}\n"

    def test_writes_each_page_in_one_call(self) -> None:
        writes: list[str] = []

        class Recorder(io.StringIO):
            def write(self, text: str) -> int:
                writes.append(text)
                return len(text)

        TextReporter(Recorder())(PageResult(random_url(), 200, random_links()))
        assert len(writes) == 1  # concurrent pages can never interleave mid-page


class TestJsonLinesReporter:
    """Machine-readable output: one JSON object per line."""

    def test_writes_one_record_per_page(self) -> None:
        pages = [
            PageResult(random_url(), 200, random_links(count)) for count in (random_int(1, 5), 0)
        ]
        out = io.StringIO()
        reporter = JsonLinesReporter(out)
        for page in pages:
            reporter(page)

        records = [json.loads(line) for line in out.getvalue().splitlines()]
        assert records == [
            {"url": page.url, "status": 200, "redirect": False, "links": list(page.links)}
            for page in pages
        ]

    def test_keeps_unicode_readable(self) -> None:
        url = f"{random_url()}{random_string()}ü"
        out = io.StringIO()
        JsonLinesReporter(out)(PageResult(url, 200, ()))
        assert url in out.getvalue()


class TestRegistry:
    """The format names the CLI offers."""

    def test_matches_cli_formats(self) -> None:
        assert sorted(REPORTERS) == ["jsonl", "text"]
        assert REPORTERS["text"] is TextReporter
        assert REPORTERS["jsonl"] is JsonLinesReporter


class TestCombinedReporter:
    """Sending each page to several outputs at once."""

    def test_every_reporter_gets_every_page_in_order(self) -> None:
        received: list[tuple[int, str]] = []
        reporters = [
            (lambda page, n=n: received.append((n, page.url))) for n in range(random_int(2, 4))
        ]
        pages = [PageResult(random_url(), 200, ()) for _ in range(random_int(1, 5))]

        combined = CombinedReporter(*reporters)
        for page in pages:
            combined(page)

        assert received == [(n, page.url) for page in pages for n in range(len(reporters))]
