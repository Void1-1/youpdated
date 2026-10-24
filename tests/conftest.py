from pathlib import Path

import pytest

from youpdated import registry
from youpdated.config import PrivacyConfig
from youpdated.http import Client
from youpdated.sources.base import Source
from youpdated.state import State

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def get_source(name: str) -> Source:
    """``registry.get_source`` for a source the test knows is there"""
    source = registry.get_source(name)
    assert source is not None, f"no source named {name!r}"
    return source


@pytest.fixture
def state():
    with State(None) as s:  # in-memory
        yield s


@pytest.fixture
def client(state):
    # Zero jitter keeps fast; respx intercepts before socket
    privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)
    with Client(privacy, state) as c:
        c.retry_backoff = 0  # don't spend real seconds testing retry paths
        yield c
