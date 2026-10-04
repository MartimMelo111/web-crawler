"""robots.txt support."""

import logging
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)

# Like Google, read at most 500 KiB of robots.txt and ignore the rest, so a huge
# or endless file can't exhaust memory before the crawl even starts.
MAX_ROBOTS_BYTES = 500 * 1024


class RobotsPolicy:
    """Answers whether a URL may be crawled according to the site's robots.txt."""

    def __init__(self, parser: RobotFileParser, user_agent: str) -> None:
        self._parser = parser
        self._user_agent = user_agent

    @classmethod
    def from_lines(cls, lines: list[str], user_agent: str) -> RobotsPolicy:
        parser = RobotFileParser()
        parser.parse(lines)
        return cls(parser, user_agent)

    @classmethod
    def allow_all(cls, user_agent: str = "*") -> RobotsPolicy:
        return cls.from_lines([], user_agent)

    @classmethod
    def disallow_all(cls, user_agent: str = "*") -> RobotsPolicy:
        return cls.from_lines(["User-agent: *", "Disallow: /"], user_agent)

    @classmethod
    async def load(
        cls,
        client: httpx.AsyncClient,
        site_url: str,
        user_agent: str,
        *,
        max_bytes: int = MAX_ROBOTS_BYTES,
    ) -> RobotsPolicy:
        """Fetch and parse ``/robots.txt`` for the site of ``site_url``.

        401/403 disallows everything (as :mod:`urllib.robotparser` does); any
        other 4xx, a server error or a network error means no restrictions.
        Only the first ``max_bytes`` of the file are read.
        """
        robots_url = urljoin(site_url, "/robots.txt")
        try:
            async with client.stream("GET", robots_url, follow_redirects=True) as response:
                status = response.status_code
                body = await _read_up_to(response, max_bytes) if status < 400 else b""
        except httpx.HTTPError as exc:
            logger.warning("could not fetch %s (%s); assuming no restrictions", robots_url, exc)
            return cls.allow_all(user_agent)

        if status in (401, 403):
            logger.warning(
                "%s returned HTTP %d; treating the whole site as disallowed "
                "(the site may be blocking crawlers)",
                robots_url,
                status,
            )
            return cls.disallow_all(user_agent)
        if status >= 400:
            return cls.allow_all(user_agent)
        if len(body) == max_bytes:
            logger.warning("%s reached the %d-byte limit; ignoring the rest", robots_url, max_bytes)
        # RFC 9309: robots.txt is UTF-8. A cut in the middle of a character is replaced.
        return cls.from_lines(body.decode("utf-8", errors="replace").splitlines(), user_agent)

    def allows(self, url: str) -> bool:
        return self._parser.can_fetch(self._user_agent, url)


async def _read_up_to(response: httpx.Response, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` of the body, then stop downloading."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= max_bytes:
            break
    return b"".join(chunks)[:max_bytes]
