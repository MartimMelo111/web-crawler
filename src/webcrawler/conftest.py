import pytest

from webcrawler.testing.mock_site import MockSite


@pytest.fixture
def site() -> MockSite:
    return MockSite()
