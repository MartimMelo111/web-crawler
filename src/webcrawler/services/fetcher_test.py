from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from webcrawler.services.fetcher import Fetcher, FetchResult, create_client
from webcrawler.testing.builders import (
    random_bytes,
    random_int,
    random_path,
    random_string,
    random_url,
)
from webcrawler.testing.mock_site import MockSite, no_sleep


async def fetch(site: MockSite, url: str, **kwargs: int) -> FetchResult:
    async with site.client() as client:
        return await Fetcher(client, sleep=no_sleep, **kwargs).fetch(url)


class Recorder:
    """Stands in for ``asyncio.sleep`` and records each requested delay."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def http_date() -> str:
    return format_datetime(datetime.now(UTC) + timedelta(seconds=random_int()), usegmt=True)


class TestResponses:
    """How each kind of response becomes a ``FetchResult``."""

    async def test_html_page_returns_body(self, site: MockSite) -> None:
        body = random_bytes()
        site.add(site.root, body=body)
        assert await fetch(site, site.root) == FetchResult(site.root, 200, body=body)

    async def test_xhtml_is_treated_as_html(self, site: MockSite) -> None:
        body = random_bytes()
        site.add(site.root, headers={"content-type": "application/xhtml+xml"}, body=body)
        assert (await fetch(site, site.root)).body == body

    @pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "text/plain", ""])
    async def test_non_html_has_no_body(self, site: MockSite, content_type: str) -> None:
        site.add(site.root, headers={"content-type": content_type}, body=random_bytes())
        assert await fetch(site, site.root) == FetchResult(site.root, 200)

    async def test_redirect_is_reported_with_absolute_target(self, site: MockSite) -> None:
        target = random_path()
        site.redirect(site.root, f"{target}#{random_string()}")
        result = await fetch(site, site.root)
        assert result == FetchResult(site.root, 301, redirect_to=site.url(target))


class TestRetries:
    """Which failures are retried, and how many times."""

    async def test_client_error_is_not_retried(self, site: MockSite) -> None:
        missing = site.url(random_path())
        assert await fetch(site, missing) == FetchResult(missing, 404, error="HTTP 404")
        assert len(site.requests) == 1

    async def test_server_error_is_retried_then_reported(self, site: MockSite) -> None:
        retries = random_int(1, 5)  # >= 1, so the retry path always runs
        site.add(site.root, status=503)
        result = await fetch(site, site.root, retries=retries)
        assert result.error == "HTTP 503"
        assert len(site.requests) == retries + 1

    async def test_transient_failure_recovers(self) -> None:
        responses: Iterator[httpx.Response] = iter(
            [httpx.Response(502), httpx.Response(200, headers={"content-type": "text/html"})]
        )
        transport = httpx.MockTransport(lambda request: next(responses))
        async with create_client(transport=transport) as client:
            result = await Fetcher(client, sleep=no_sleep).fetch(random_url())
        assert result.status == 200
        assert result.error is None

    async def test_network_error_is_retried_then_reported(self) -> None:
        url, message, retries = random_url(), random_string(), random_int(1, 5)  # >= 1: retry path
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError(message, request=request)

        async with create_client(transport=httpx.MockTransport(handler)) as client:
            result = await Fetcher(client, retries=retries, sleep=no_sleep).fetch(url)
        assert result == FetchResult(url, error=f"ConnectError: {message}")
        assert calls == retries + 1

    @pytest.mark.parametrize("failure", ["server error", "network error"])
    async def test_zero_retries_makes_a_single_attempt(self, failure: str) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if failure == "network error":
                raise httpx.ConnectError(random_string(), request=request)
            return httpx.Response(503)

        async with create_client(transport=httpx.MockTransport(handler)) as client:
            result = await Fetcher(client, retries=0, sleep=no_sleep).fetch(random_url())
        assert result.error is not None
        assert calls == 1

    def test_negative_retries_rejected(self) -> None:
        with pytest.raises(ValueError, match="retries"):
            Fetcher(create_client(), retries=-random_int())

    async def test_corrupt_compressed_body_is_an_error_and_not_retried(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            headers = {"content-type": "text/html", "content-encoding": "gzip"}
            return httpx.Response(200, headers=headers, content=random_bytes())  # not gzip

        url = random_url()
        async with create_client(transport=httpx.MockTransport(handler)) as client:
            result = await Fetcher(client, retries=random_int(1, 5), sleep=no_sleep).fetch(url)
        assert result.url == url
        assert result.error is not None
        assert result.error.startswith("DecodingError")
        assert calls == 1


class TestRetryDelays:
    """How long to wait between attempts: Retry-After or exponential backoff."""

    async def test_retry_after_header_is_honoured_up_to_a_cap(self, site: MockSite) -> None:
        cap, retries = random_int(1, 30), random_int(1, 5)
        asked_for = cap + random_int(1, 30)
        site.add(site.root, status=429, headers={"retry-after": str(asked_for)})
        sleep = Recorder()

        async with site.client() as client:
            await Fetcher(client, retries=retries, max_retry_after=cap, sleep=sleep).fetch(
                site.root
            )
        assert sleep.delays == [cap] * retries

    async def test_retry_after_below_the_cap_is_used_as_is(self, site: MockSite) -> None:
        asked_for = random_int(1, 30)
        site.add(site.root, status=503, headers={"retry-after": str(asked_for)})
        sleep = Recorder()

        async with site.client() as client:
            await Fetcher(client, retries=1, max_retry_after=31, sleep=sleep).fetch(site.root)
        assert sleep.delays == [asked_for]

    @pytest.mark.parametrize(
        "retry_after",
        [http_date, random_string, lambda: str(-random_int())],
        ids=["http-date", "garbage", "negative"],
    )
    async def test_unusable_retry_after_falls_back_to_backoff(
        self, site: MockSite, retry_after: Callable[[], str]
    ) -> None:
        backoff = float(random_int(1, 10))
        site.add(site.root, status=503, headers={"retry-after": retry_after()})
        sleep = Recorder()

        async with site.client() as client:
            await Fetcher(client, retries=1, backoff=backoff, sleep=sleep).fetch(site.root)
        assert len(sleep.delays) == 1
        assert (
            backoff <= sleep.delays[0] <= 2 * backoff
        )  # backoff * 2**0 plus up to one backoff of jitter

    async def test_backoff_grows_exponentially(self, site: MockSite) -> None:
        backoff, retries = float(random_int(1, 10)), random_int(2, 6)
        site.add(site.root, status=500)
        sleep = Recorder()

        async with site.client() as client:
            await Fetcher(client, retries=retries, backoff=backoff, sleep=sleep).fetch(site.root)
        assert len(sleep.delays) == retries
        for attempt, delay in enumerate(sleep.delays):
            base = backoff * 2**attempt
            assert base <= delay <= base + backoff  # jitter adds at most one backoff


class TestBodyLimit:
    """HTML bodies over ``max_body_bytes`` are abandoned."""

    async def test_oversized_body_is_rejected(self, site: MockSite) -> None:
        limit = random_int(10, 1000)
        site.add(site.root, body=random_bytes(limit + 1))
        result = await fetch(site, site.root, max_body_bytes=limit)
        assert result.body is None
        assert result.error == f"response body exceeds {limit} bytes"

    async def test_body_exactly_at_the_limit_is_accepted(self, site: MockSite) -> None:
        limit = random_int(10, 1000)
        body = random_bytes(limit)
        site.add(site.root, body=body)
        assert (await fetch(site, site.root, max_body_bytes=limit)).body == body

    async def test_oversized_body_without_content_length_is_rejected(self) -> None:
        chunk_size, limit = random_int(10, 100), random_int(100, 1000)
        chunks = limit // chunk_size + 1  # just enough to go over the limit

        async def stream() -> AsyncIterator[bytes]:
            for _ in range(chunks):
                yield random_bytes(chunk_size)

        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, content=stream()
            )
        )
        async with create_client(transport=transport) as client:
            result = await Fetcher(client, max_body_bytes=limit).fetch(random_url())
        assert result.error == f"response body exceeds {limit} bytes"
