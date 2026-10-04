"""URL normalisation and crawl-scope rules.

Every URL that enters the crawler passes through :func:`normalize_url`, so two
spellings of the same resource (``HTTP://Example.com:80/#top`` and
``http://example.com/``) are deduplicated to one canonical string.
"""

from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Resolve ``url`` against ``base`` and return its canonical form.

    Returns ``None`` for anything that is not a crawlable http(s) URL
    (``mailto:``, ``javascript:``, malformed hosts or ports, ...).

    Canonicalisation is deliberately conservative: it only applies rewrites
    that are guaranteed by the URL spec not to change the resource
    (case of scheme/host, default port, empty path, ``.``/``..`` segments,
    fragment). Query strings are left untouched because reordering or
    dropping parameters can change what a server returns.
    """
    url = url.strip()
    if base is not None:
        url = urljoin(base, url)
    try:
        parts = urlsplit(url)
        port = parts.port  # raises ValueError for out-of-range / non-numeric ports
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    host = parts.hostname  # already lower-cased by urllib
    if scheme not in ALLOWED_SCHEMES or not host:
        return None

    host = host.rstrip(".")
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    netloc = host if port in (None, _DEFAULT_PORTS[scheme]) else f"{host}:{port}"
    path = _remove_dot_segments(parts.path or "/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _remove_dot_segments(path: str) -> str:
    """Resolve ``.`` and ``..`` in an absolute path (RFC 3986, section 5.2.4).

    ``urljoin`` already does this for relative links, but not for absolute
    ones, so ``https://site.com/a/../b`` would otherwise be a different URL
    from ``https://site.com/b`` and the same page would be fetched twice.
    """
    segments = path.split("/")[1:]  # the path always starts with "/"
    resolved: list[str] = []
    for segment in segments:
        if segment == "..":
            if resolved:
                resolved.pop()
        elif segment != ".":
            resolved.append(segment)
    if segments[-1] in (".", ".."):
        resolved.append("")  # "/a/b/.." points at the directory "/a/"
    return "/" + "/".join(resolved)


def hostname_of(url: str) -> str | None:
    """Return the lower-cased hostname of ``url`` without a trailing dot."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.rstrip(".") if host else None


@dataclass(frozen=True, slots=True)
class DomainScope:
    """Decides which URLs belong to the site being crawled.

    The rule is an exact hostname match: ``example.com`` does not include
    ``www.example.com`` or ``blog.example.com``. Scheme and port are ignored,
    so ``http://`` and ``https://`` pages on the same host are both in scope.
    """

    host: str

    @classmethod
    def from_url(cls, url: str) -> DomainScope:
        host = hostname_of(url)
        if host is None:
            raise ValueError(f"URL has no hostname: {url!r}")
        return cls(host)

    def __contains__(self, url: object) -> bool:
        return isinstance(url, str) and hostname_of(url) == self.host
