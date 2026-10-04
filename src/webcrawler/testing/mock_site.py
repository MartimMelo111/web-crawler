"""An in-memory website for tests, served through ``httpx.MockTransport``."""

import asyncio
from dataclasses import dataclass, field

import httpx

from webcrawler.services.fetcher import create_client
from webcrawler.testing.builders import random_host, random_string

HTML = {"content-type": "text/html; charset=utf-8"}


@dataclass
class MockSite:
    """An in-memory website on a random host.

    Records every request and the peak number of concurrent requests so tests
    can assert on crawler behaviour without touching the network. URLs that
    have no route get a 404.
    """

    host: str = field(default_factory=random_host)
    routes: dict[str, tuple[int, dict[str, str], bytes]] = field(default_factory=dict)
    delay: float = 0.0  # applied to every response
    requests: list[str] = field(default_factory=list)
    in_flight: int = 0
    max_in_flight: int = 0

    @property
    def root(self) -> str:
        return self.url("/")

    def url(self, path: str, *, scheme: str = "https") -> str:
        """Absolute URL of ``path`` on this site."""
        return f"{scheme}://{self.host}{path}"

    def page(self, url: str, *links: str) -> None:
        """Serve an HTML page at ``url`` containing an ``<a>`` for each link."""
        anchors = "".join(f'<a href="{link}">{random_string()}</a>' for link in links)
        self.add(url, body=f"<html><body>{anchors}</body></html>".encode())

    def add(
        self,
        url: str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
    ) -> None:
        self.routes[url] = (status, HTML if headers is None else headers, body)

    def redirect(self, url: str, location: str, status: int = 301) -> None:
        self.add(url, status=status, headers={"location": location})

    async def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if url not in self.routes:
                return httpx.Response(404, headers=HTML)
            status, headers, body = self.routes[url]
            return httpx.Response(status, headers=headers, content=body)
        finally:
            self.in_flight -= 1

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.AsyncClient:
        return create_client(transport=self.transport)


async def no_sleep(_: float) -> None:
    """Drop-in for ``asyncio.sleep`` that makes retry tests instantaneous."""
