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
def fake(tmp_path, monkeypatch):
    """api tests fake source"""
    from . import fake_source

    items = tmp_path / "items.json"
    fake_source.write_items(items, {})
    monkeypatch.setenv(fake_source.ITEMS_ENV, str(items))
    fake_source.register()
    yield items
    fake_source.unregister()


@pytest.fixture
def client(state):
    # Zero jitter keeps fast; respx intercepts before socket
    privacy = PrivacyConfig(jitter=(0.0, 0.0), concurrency=1)
    with Client(privacy, state) as c:
        c.retry_backoff = 0  # don't spend real seconds testing retry paths
        yield c


@pytest.fixture(autouse=True)
def _no_real_app_traces(monkeypatch):
    """`uninstall` tests run with --yes: never let them find, or delete local pre-existing app settings"""
    from youpdated import apptraces

    monkeypatch.setattr(apptraces, "app_traces", lambda platform=None: [])
    monkeypatch.setattr(apptraces, "app_locations", lambda platform=None: [])
    monkeypatch.setattr(apptraces, "registry", None)
    monkeypatch.setattr(apptraces, "credentials", None)
    monkeypatch.setattr(apptraces, "_run", _refuse_run)


def _refuse_run(*command):
    raise AssertionError(f"a test ran {command[0]}; patch apptraces._run instead")
