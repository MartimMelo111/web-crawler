"""Builders for random test data (seeded per test by pytest-randomly)."""

import random
import string

_LETTERS = string.ascii_lowercase
_ALPHANUMERIC = string.ascii_lowercase + string.digits
_TLDS = ("com", "org", "net", "io", "dev", "co.uk")


def random_string(length: int = 10) -> str:
    """Lowercase letters and digits, always starting with a letter."""
    return random.choice(_LETTERS) + "".join(random.choices(_ALPHANUMERIC, k=length - 1))


def random_int(low: int = 1, high: int = 1000) -> int:
    return random.randint(low, high)


def random_host() -> str:
    return f"{random_string()}.{random.choice(_TLDS)}"


def random_port() -> int:
    """A port that is never the default 80 or 443."""
    return random.randint(1024, 65535)


def random_path(segments: int = 1) -> str:
    """An absolute path such as ``/k3j2h1g0fd``."""
    return "/" + "/".join(random_string() for _ in range(segments))


def random_paths(count: int, segments: int = 1) -> list[str]:
    """``count`` distinct random paths."""
    paths: dict[str, None] = {}
    while len(paths) < count:
        paths[random_path(segments)] = None
    return list(paths)


def random_url(host: str | None = None, path: str | None = None, *, scheme: str = "https") -> str:
    """An already-normalised URL, so it compares equal to crawler output."""
    return f"{scheme}://{host or random_host()}{path or random_path()}"


def random_user_agent() -> str:
    return f"{random_string()}bot"


def random_bytes(size: int | None = None) -> bytes:
    return random.randbytes(random_int(1, 200) if size is None else size)
