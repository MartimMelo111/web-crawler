"""Fast, single-domain asynchronous web crawler."""

from webcrawler.core.crawler import Crawler, CrawlStats, PageResult
from webcrawler.services.fetcher import Fetcher, FetchResult, create_client
from webcrawler.tools.parser import extract_links
from webcrawler.tools.urls import DomainScope, normalize_url

__all__ = [
    "CrawlStats",
    "Crawler",
    "DomainScope",
    "FetchResult",
    "Fetcher",
    "PageResult",
    "create_client",
    "extract_links",
    "normalize_url",
]
