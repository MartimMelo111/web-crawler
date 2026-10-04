"""HTTP fetching with retries, redirect handling and resource limits."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from webcrawler.tools.urls import normalize_url

HTML_CONTENT_TYPES = frozenset({"text/html", "application/xhtml+xml"})
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024
DEFAULT_USER_AGENT = "webcrawler/0.1 (+https://github.com/MartimMelo111/web-crawler)"
_ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of fetching one URL.

    Exactly one of these holds:

    * ``error`` is set - the request failed (network error or HTTP error status);
    * ``redirect_to`` is set - the server answered with a redirect;
    * ``body`` is set - the response was an HTML document;
    * none of them is set - the response was successful but not HTML.
    """

    url: str
    status: int | None = None
    body: bytes | None = None
    redirect_to: str | None = None
    error: str | None = None


def create_client(
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: float = 10.0,
    max_connections: int = 20,
    http2: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """Build the ``AsyncClient`` shared by every request of a crawl.

    ``transport`` replaces the network layer (tests use ``httpx.MockTransport``).
    """
    return httpx.AsyncClient(
        transport=transport,
        http2=http2,
        timeout=httpx.Timeout(timeout),
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_connections,
        ),
        headers={"User-Agent": user_agent, "Accept": _ACCEPT},
        # Redirects are handled by the crawler so that the target goes through
        # the same scope and de-duplication rules as any other link.
        follow_redirects=False,
    )


class Fetcher:
    """Fetches pages, retrying transient failures with exponential backoff.

    Bodies are streamed: non-HTML responses are closed as soon as the headers
    arrive, and HTML bodies larger than ``max_body_bytes`` are abandoned, so a
    large binary file or a runaway response cannot exhaust memory.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        retries: int = 2,
        backoff: float = 0.5,
        max_retry_after: float = 30.0,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if retries < 0:
            raise ValueError("retries must be >= 0")
        self._client = client
        self._retries = retries
        self._backoff = backoff
        self._max_retry_after = max_retry_after
        self._max_body_bytes = max_body_bytes
        self._sleep = sleep

    async def fetch(self, url: str) -> FetchResult:
        attempt = 0
        while True:  # every path returns by the last attempt
            last_attempt = attempt == self._retries
            try:
                result, retry_after = await self._fetch_once(url)
            except httpx.TransportError as exc:  # connect/read errors and timeouts
                if last_attempt:
                    return FetchResult(url, error=_describe(exc))
                retry_after = None
            except httpx.RequestError as exc:  # e.g. a corrupt compressed body: not retried
                return FetchResult(url, error=_describe(exc))
            else:
                if result.status not in RETRYABLE_STATUSES or last_attempt:
                    return result
            await self._sleep(self._delay(attempt, retry_after))
            attempt += 1

    async def _fetch_once(self, url: str) -> tuple[FetchResult, float | None]:
        async with self._client.stream("GET", url) as response:
            status = response.status_code

            if response.is_redirect:
                target = normalize_url(response.headers["location"], url)
                return FetchResult(url, status, redirect_to=target), None

            if response.is_error:
                retry_after = None
                if status in RETRYABLE_STATUSES:
                    retry_after = _parse_retry_after(response.headers.get("retry-after"))
                return FetchResult(url, status, error=f"HTTP {status}"), retry_after

            if not _is_html(response.headers.get("content-type")):
                return FetchResult(url, status), None

            body = await self._read_limited(response)
            if body is None:
                error = f"response body exceeds {self._max_body_bytes} bytes"
                return FetchResult(url, status, error=error), None
            return FetchResult(url, status, body=body), None

    async def _read_limited(self, response: httpx.Response) -> bytes | None:
        declared = response.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self._max_body_bytes:
            return None
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():  # decompressed bytes
            size += len(chunk)
            if size > self._max_body_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def _delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self._max_retry_after)
        # Exponential backoff with jitter so concurrent workers don't retry in lockstep.
        return float(self._backoff * 2**attempt + random.uniform(0, self._backoff))


def _is_html(content_type: str | None) -> bool:
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() in HTML_CONTENT_TYPES


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a delay-seconds ``Retry-After`` header (HTTP-date form is ignored)."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _describe(exc: Exception) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
