"""HTML link extraction."""

from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from webcrawler.tools.urls import normalize_url

# Elements whose href points at another document. Resource references such as
# <img src>, <script src> and <link href> are intentionally excluded: they are
# not pages and following them would waste bandwidth.
_LINK_SELECTOR = "a[href], area[href]"


def extract_links(html: bytes | str, page_url: str) -> list[str]:
    """Return the unique, normalised http(s) links on a page in document order.

    Links to other domains are included; deciding what to *crawl* is the
    crawler's job, while this function reports everything found on the page.
    Relative links are resolved against ``<base href>`` when present, as a
    browser would.
    """
    tree = LexborHTMLParser(html)

    base_url = page_url
    base_node = tree.css_first("base[href]")
    if base_node is not None:
        base_href = base_node.attributes.get("href")
        if base_href:
            base_url = urljoin(page_url, base_href.strip())

    links: dict[str, None] = {}  # insertion-ordered set
    for node in tree.css(_LINK_SELECTOR):
        href = node.attributes.get("href")
        if not href:
            continue
        url = normalize_url(href, base_url)
        if url is not None:
            links[url] = None
    return list(links)
