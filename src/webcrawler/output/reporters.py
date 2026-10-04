"""Reporters that write crawl results as they arrive."""

import json
from collections.abc import Callable
from typing import TextIO

from webcrawler.core.crawler import PageResult


class TextReporter:
    """Human-readable output: the page URL followed by its links, indented.

    Each page is written with a single ``write`` call so output from
    concurrent workers can never interleave.
    """

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __call__(self, page: PageResult) -> None:
        header = f"{page.url} [redirect {page.status}]" if page.redirect else page.url
        lines = [header, *(f"  -> {link}" for link in page.links)]
        self._stream.write("\n".join(lines) + "\n")


class JsonLinesReporter:
    """Machine-readable output: one JSON object per page (JSON Lines)."""

    def __init__(self, stream: TextIO) -> None:
        self._stream = stream

    def __call__(self, page: PageResult) -> None:
        record = {
            "url": page.url,
            "status": page.status,
            "redirect": page.redirect,
            "links": list(page.links),
        }
        self._stream.write(json.dumps(record, ensure_ascii=False) + "\n")


class CombinedReporter:
    """Sends each page to several reporters, e.g. the terminal and a file."""

    def __init__(self, *reporters: Callable[[PageResult], None]) -> None:
        self._reporters = reporters

    def __call__(self, page: PageResult) -> None:
        for reporter in self._reporters:
            reporter(page)


REPORTERS: dict[str, type[TextReporter | JsonLinesReporter]] = {
    "text": TextReporter,
    "jsonl": JsonLinesReporter,
}
