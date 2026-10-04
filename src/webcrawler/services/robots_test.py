import httpx
import pytest

from webcrawler.services.fetcher import create_client
from webcrawler.services.robots import RobotsPolicy
from webcrawler.testing.builders import (
    random_int,
    random_path,
    random_string,
    random_url,
    random_user_agent,
)
from webcrawler.testing.mock_site import MockSite

TEXT = {"content-type": "text/plain"}


async def load(site: MockSite, user_agent: str | None = None) -> RobotsPolicy:
    async with site.client() as client:
        return await RobotsPolicy.load(client, site.root, user_agent or random_user_agent())


class TestLoad:
    """Fetching robots.txt and handling each response status."""

    async def test_is_loaded_from_the_site_root(self, site: MockSite) -> None:
        async with site.client() as client:
            await RobotsPolicy.load(client, site.url(random_path(3)), random_user_agent())
        assert site.requests == [site.url("/robots.txt")]

    async def test_rules_are_applied(self, site: MockSite) -> None:
        private = random_path()
        site.add(
            site.url("/robots.txt"),
            headers=TEXT,
            body=f"User-agent: *\nDisallow: {private}/\n".encode(),
        )

        policy = await load(site)

        assert policy.allows(site.url(random_path()))
        assert not policy.allows(site.url(f"{private}{random_path()}"))

    async def test_only_the_first_max_bytes_are_read(
        self, site: MockSite, caplog: pytest.LogCaptureFixture
    ) -> None:
        early, late = random_path(), random_path()
        head = f"User-agent: *\nDisallow: {early}/\n".encode()
        filler = b"#" * random_int(100, 1000) + b"\n"
        limit = len(head) + len(filler)
        site.add(
            site.url("/robots.txt"),
            headers=TEXT,
            body=head + filler + f"Disallow: {late}/\n".encode(),
        )

        async with site.client() as client:
            policy = await RobotsPolicy.load(
                client, site.root, random_user_agent(), max_bytes=limit
            )

        assert not policy.allows(site.url(f"{early}{random_path()}"))
        assert policy.allows(site.url(f"{late}{random_path()}"))  # beyond the limit: ignored
        assert f"reached the {limit}-byte limit" in caplog.text

    async def test_missing_robots_allows_everything(self, site: MockSite) -> None:
        assert (await load(site)).allows(site.url(random_path()))

    @pytest.mark.parametrize("status", [401, 403])
    async def test_unauthorised_robots_disallows_everything_with_warning(
        self, site: MockSite, status: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        site.add(site.url("/robots.txt"), status=status, headers=TEXT)
        assert not (await load(site)).allows(site.url(random_path()))
        assert f"returned HTTP {status}" in caplog.text

    async def test_network_error_allows_everything(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout(random_string(), request=request)

        async with create_client(transport=httpx.MockTransport(handler)) as client:
            policy = await RobotsPolicy.load(client, random_url(), random_user_agent())
        assert policy.allows(random_url())


class TestRules:
    """Matching rules against user agents."""

    def test_user_agent_specific_rules(self) -> None:
        blocked, other = random_user_agent(), random_user_agent()
        lines = [f"User-agent: {blocked}", "Disallow: /", "", "User-agent: *", "Disallow:"]
        url = random_url()
        assert not RobotsPolicy.from_lines(lines, blocked).allows(url)
        assert RobotsPolicy.from_lines(lines, other).allows(url)
