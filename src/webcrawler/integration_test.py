"""End-to-end test: run the real CLI against a real HTTP server on localhost."""

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from webcrawler.cli import main
from webcrawler.testing.builders import random_bytes, random_string, random_url


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@dataclass
class LocalSite:
    """Random file names served from a temporary directory."""

    base: str
    page: str
    directory: str
    document: str
    missing: str
    external: str


@pytest.fixture
def local_site(tmp_path: Path) -> Iterator[LocalSite]:
    page, directory, document, missing = (random_string() for _ in range(4))
    external = random_url()
    (tmp_path / "index.html").write_text(
        f'<a href="/{page}.html">a</a> <a href="/{directory}">b</a> '
        f'<a href="{external}">c</a> <a href="/{document}.pdf">d</a>'
    )
    (tmp_path / f"{page}.html").write_text('<a href="/">home</a>')
    (tmp_path / f"{document}.pdf").write_bytes(random_bytes())
    (tmp_path / directory).mkdir()
    (tmp_path / directory / "index.html").write_text(f'<a href="../{missing}.html">broken</a>')

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(tmp_path)))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield LocalSite(
            f"http://127.0.0.1:{httpd.server_address[1]}",
            page,
            directory,
            document,
            missing,
            external,
        )
    finally:
        httpd.shutdown()
        httpd.server_close()


def _parse_blocks(output: str) -> dict[str, list[str]]:
    blocks: dict[str, list[str]] = {}
    current: list[str] = []
    for line in output.splitlines():
        if line.startswith("  -> "):
            current.append(line.removeprefix("  -> "))
        else:
            current = blocks.setdefault(line, [])
    return blocks


def _blocks_from_json_lines(path: Path) -> dict[str, list[str]]:
    """The ``-o`` file in the same shape as ``_parse_blocks`` gives for the terminal."""
    blocks: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        key = (
            f"{record['url']} [redirect {record['status']}]"
            if record["redirect"]
            else record["url"]
        )
        blocks[key] = record["links"]
    return blocks


class TestEndToEnd:
    """The real CLI against a real HTTP server on localhost."""

    def test_cli_crawls_local_site(
        self,
        local_site: LocalSite,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
        tmp_path_factory: pytest.TempPathFactory,
    ) -> None:
        site = local_site
        monkeypatch.chdir(tmp_path_factory.mktemp("run"))  # results/ is created here
        exit_code = main([site.base, "--retries", "0"])

        out, err = capsys.readouterr()
        (output_file,) = Path("results").iterdir()
        assert exit_code == 0
        assert _parse_blocks(out) == _blocks_from_json_lines(output_file)  # same results
        assert _parse_blocks(out) == {
            f"{site.base}/": [
                f"{site.base}/{site.page}.html",
                f"{site.base}/{site.directory}",
                site.external,
                f"{site.base}/{site.document}.pdf",
            ],
            f"{site.base}/{site.page}.html": [f"{site.base}/"],
            # A directory without a trailing slash is a real 301 from the server.
            f"{site.base}/{site.directory} [redirect 301]": [f"{site.base}/{site.directory}/"],
            f"{site.base}/{site.directory}/": [f"{site.base}/{site.missing}.html"],
        }
        # The PDF is skipped (not HTML) and the broken link is a 404 error.
        assert "crawled 3 pages, 1 redirects, 1 skipped, 1 errors" in err
