from collections.abc import Callable

import pytest

from webcrawler.testing.builders import (
    random_host,
    random_int,
    random_path,
    random_port,
    random_string,
)
from webcrawler.tools.urls import DomainScope, hostname_of, normalize_url


@pytest.fixture
def host() -> str:
    return random_host()


@pytest.fixture
def path() -> str:
    return random_path()


# Each case builds (input, expected) from a random host and path.
NormalisationCase = Callable[[str, str], tuple[str, str | None]]

NORMALISES: dict[str, NormalisationCase] = {
    "adds root path": lambda h, p: (f"https://{h}", f"https://{h}/"),
    "lowercases scheme and host, keeps path case": lambda h, p: (
        f"HTTPS://{h.upper()}{p.upper()}",
        f"https://{h}{p.upper()}",
    ),
    "drops default http port": lambda h, p: (f"http://{h}:80{p}", f"http://{h}{p}"),
    "drops default https port": lambda h, p: (f"https://{h}:443{p}", f"https://{h}{p}"),
    "keeps other ports": lambda h, p: (
        f"https://{h}:{(port := random_port())}{p}",
        f"https://{h}:{port}{p}",
    ),
    "drops fragment": lambda h, p: (f"https://{h}{p}#{random_string()}", f"https://{h}{p}"),
    "keeps query unchanged": lambda h, p: (
        f"https://{h}{p}?{(query := f'{random_string()}=1&{random_string()}=2')}",
        f"https://{h}{p}?{query}",
    ),
    "drops trailing dot on host": lambda h, p: (f"https://{h}.{p}", f"https://{h}{p}"),
    "drops user info": lambda h, p: (
        f"https://{random_string()}:{random_string()}@{h}{p}",
        f"https://{h}{p}",
    ),
    "strips whitespace": lambda h, p: (f"  https://{h}{p}  ", f"https://{h}{p}"),
    "keeps ipv6 brackets": lambda h, p: (
        f"http://[::1]:{(port := random_port())}{p}",
        f"http://[::1]:{port}{p}",
    ),
    "resolves dot segments": lambda h, p: (
        f"https://{h}/{random_string()}/..{p}/./{(name := random_string())}",
        f"https://{h}{p}/{name}",
    ),
    "trailing dot-dot points at the directory": lambda h, p: (
        f"https://{h}{p}/{random_string()}/..",
        f"https://{h}{p}/",
    ),
    "dot-dot cannot go above the root": lambda h, p: (
        f"https://{h}/../..{p}",
        f"https://{h}{p}",
    ),
    "keeps double slashes": lambda h, p: (f"https://{h}{p}/{p}", f"https://{h}{p}/{p}"),
}


REJECTS: dict[str, Callable[[str, str], str]] = {
    "mailto": lambda h, p: f"mailto:{random_string()}@{h}",
    "javascript": lambda h, p: f"javascript:{random_string()}()",
    "tel": lambda h, p: f"tel:+{random_int(10**9, 10**10)}",
    "ftp": lambda h, p: f"ftp://{h}{p}",
    "data": lambda h, p: f"data:text/html,{random_string()}",
    "port out of range": lambda h, p: f"https://{h}:{random_int(65536, 10**6)}{p}",
    "non-numeric port": lambda h, p: f"https://{h}:{random_string()}{p}",
    "no host": lambda h, p: f"https://{p}",
    "empty": lambda h, p: "",
}


class TestNormalizeUrl:
    """Canonical form, rejection of non-crawlable URLs, relative resolution."""

    @pytest.mark.parametrize("case", NORMALISES.values(), ids=NORMALISES.keys())
    def test_normalises(self, case: NormalisationCase, host: str, path: str) -> None:
        url, expected = case(host, path)
        assert normalize_url(url) == expected

    @pytest.mark.parametrize("build", REJECTS.values(), ids=REJECTS.keys())
    def test_rejects_non_crawlable(
        self, build: Callable[[str, str], str], host: str, path: str
    ) -> None:
        assert normalize_url(build(host, path)) is None

    def test_resolves_relative_links(self, host: str) -> None:
        directory, page, name = random_string(), random_string(), random_string()
        other_host = random_host()
        base = f"https://{host}/{directory}/{page}"
        value = random_int()

        assert normalize_url(name, base) == f"https://{host}/{directory}/{name}"
        assert normalize_url(f"../{name}", base) == f"https://{host}/{name}"
        assert normalize_url(f"/{name}", base) == f"https://{host}/{name}"
        assert normalize_url(f"?{name}={value}", base) == f"{base}?{name}={value}"
        assert normalize_url(f"#{name}", base) == base
        assert normalize_url(f"//{other_host}/{name}", base) == f"https://{other_host}/{name}"


class TestHostnameOf:
    """Extracting the hostname used for scope checks."""

    def test_extracts_hostname_and_rejects_malformed(self, host: str, path: str) -> None:
        assert hostname_of(f"https://{host.upper()}.{path}") == host
        assert hostname_of(random_string()) is None
        assert hostname_of(f"http://[{random_string()}") is None  # malformed IPv6 literal


class TestDomainScope:
    def test_same_host_is_in_scope(self, host: str, path: str) -> None:
        scope = DomainScope.from_url(f"https://{host}{path}")
        assert f"https://{host}/" in scope
        assert f"http://{host}{path}" in scope
        assert f"https://{host}:{random_port()}{path}" in scope

    def test_other_hosts_and_subdomains_are_out_of_scope(self, host: str) -> None:
        scope = DomainScope.from_url(f"https://{host}/")
        assert f"https://www.{host}/" not in scope
        assert f"https://{random_string()}.{host}/" not in scope
        assert f"https://{random_host()}/" not in scope
        assert f"https://{random_string()}{host}/" not in scope  # look-alike prefix
        assert f"https://{host}.{random_host()}/" not in scope  # look-alike suffix

    def test_non_string_is_out_of_scope(self, host: str) -> None:
        assert random_int() not in DomainScope.from_url(f"https://{host}/")

    def test_requires_hostname(self, path: str) -> None:
        with pytest.raises(ValueError, match="no hostname"):
            DomainScope.from_url(path)
