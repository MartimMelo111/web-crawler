import pytest

from webcrawler.testing.builders import random_host, random_int, random_string
from webcrawler.tools.parser import extract_links


class Page:
    """A random page URL at ``https://<host>/<directory>/<name>``."""

    def __init__(self) -> None:
        self.host = random_host()
        self.directory = random_string()
        self.url = f"https://{self.host}/{self.directory}/{random_string()}"

    def at(self, path: str) -> str:
        return f"https://{self.host}{path}"


@pytest.fixture
def page() -> Page:
    return Page()


class TestLinkResolution:
    """Links are resolved to absolute, normalised, unique URLs."""

    def test_extracts_absolute_and_relative_links_in_document_order(self, page: Page) -> None:
        external = f"https://{random_host()}/"
        relative, root_relative = random_string(), f"/{random_string()}"
        other_host, other_path = random_host(), f"/{random_string()}"
        html = f"""
            <a href="{external}">{random_string()}</a>
            <a href="{relative}">{random_string()}</a>
            <a href="{root_relative}">{random_string()}</a>
            <a href="//{other_host}{other_path}">{random_string()}</a>
        """
        assert extract_links(html, page.url) == [
            external,
            page.at(f"/{page.directory}/{relative}"),
            page.at(root_relative),
            f"https://{other_host}{other_path}",
        ]

    def test_deduplicates_after_normalisation(self, page: Page) -> None:
        path = f"/{random_string()}"
        html = f"""
            <a href="{path}">1</a>
            <a href="{path}#{random_string()}">2</a>
            <a href="HTTPS://{page.host.upper()}{path}">3</a>
        """
        assert extract_links(html, page.url) == [page.at(path)]

    def test_honours_base_href(self, page: Page) -> None:
        base, name = random_string(), random_string()
        html = f'<head><base href="/{base}/"></head><body><a href="{name}">x</a></body>'
        assert extract_links(html, page.url) == [page.at(f"/{base}/{name}")]

    def test_empty_base_href_is_ignored(self, page: Page) -> None:
        name = random_string()
        html = f'<head><base href=""></head><body><a href="{name}">x</a></body>'
        assert extract_links(html, page.url) == [page.at(f"/{page.directory}/{name}")]


class TestLinkFiltering:
    """Which elements and hrefs count as links."""

    def test_ignores_non_http_and_empty_links(self, page: Page) -> None:
        kept = f"/{random_string()}"
        html = f"""
            <a href="mailto:{random_string()}@{page.host}">mail</a>
            <a href="javascript:{random_string()}()">js</a>
            <a href="tel:{random_int()}">tel</a>
            <a href="">empty</a>
            <a>no href</a>
            <a href="{kept}">kept</a>
        """
        assert extract_links(html, page.url) == [page.at(kept)]

    def test_includes_image_map_areas_but_not_resources(self, page: Page) -> None:
        area = f"/{random_string()}"
        html = f"""
            <map><area href="{area}" alt=""></map>
            <img src="/{random_string()}.png"><script src="/{random_string()}.js"></script>
            <link rel="stylesheet" href="/{random_string()}.css">
        """
        assert extract_links(html, page.url) == [page.at(area)]

    def test_accepts_bytes_and_tolerates_malformed_html(self, page: Page) -> None:
        one, two = f"/{random_string()}", f"/{random_string()}"
        html = f"<html><body><div><a href='{one}'>one<a href={two}>two</div".encode()
        assert extract_links(html, page.url) == [page.at(one), page.at(two)]

    def test_page_without_links(self, page: Page) -> None:
        assert extract_links(f"<p>{random_string()}</p>", page.url) == []
