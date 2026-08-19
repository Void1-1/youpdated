from __future__ import annotations

import socket
import threading

import pytest

from youpdated.cli import main
from youpdated.config import PrivacyConfig
from youpdated.http import Client, FetchError, ProxyUnavailable, probe_proxy
from youpdated.runner import RunResult

CONFIG = """\
privacy:
  proxy: socks5://127.0.0.1:{port}
  jitter: [0, 0]
  timeout: 2
sources:
  feed:
    - https://example.com/feed.xml
"""

SOCKS5_NO_AUTH = b"\x05\x00"
ATYP_IPV4, ATYP_DOMAIN = 1, 3


@pytest.fixture
def closed_port():
    """An unlistening port"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeSocks:
    """Accepts SOCKS5 greetings and records the CONNECT request, then stops"""

    def __init__(self):
        self.requests: list[bytes] = []
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(8)
        self.port = self._server.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            try:
                conn.recv(64)                         # version/auth greeting
                conn.sendall(SOCKS5_NO_AUTH)
                self.requests.append(conn.recv(512))  # the CONNECT
            except OSError:
                pass
            finally:
                conn.close()

    def close(self):
        self._server.close()
        self._thread.join(timeout=2)


@pytest.fixture
def fake_socks():
    proxy = FakeSocks()
    yield proxy
    proxy.close()


def client_for(port: int) -> Client:
    client = Client(PrivacyConfig(proxy=f"socks5://127.0.0.1:{port}", jitter=(0, 0), timeout=2))
    client.retry_backoff = 0
    return client


# preflight


def test_probe_accepts_a_reachable_proxy(fake_socks):
    probe_proxy(f"socks5://127.0.0.1:{fake_socks.port}", timeout=2)


def test_probe_rejects_a_dead_proxy(closed_port):
    with pytest.raises(ProxyUnavailable, match="cannot reach the proxy"):
        probe_proxy(f"socks5://127.0.0.1:{closed_port}", timeout=2)


def test_probe_rejects_a_url_without_a_host():
    with pytest.raises(ProxyUnavailable, match="no host"):
        probe_proxy("socks5://", timeout=2)


def test_probe_rejects_an_unusable_port():
    with pytest.raises(ProxyUnavailable, match="invalid port"):
        probe_proxy("socks5://127.0.0.1:99999", timeout=2)


def test_probe_falls_back_to_the_scheme_default_port():
    with pytest.raises(ProxyUnavailable, match="127.0.0.1:1080"):
        probe_proxy("socks5://127.0.0.1", timeout=2)


# routing


def test_requests_go_to_the_proxy_and_the_hostname_is_not_resolved_locally(fake_socks):
    """The proxy must receive the hostname"""
    with client_for(fake_socks.port) as client:
        with pytest.raises(FetchError):
            client.get("https://example.com/feed.xml")

    assert fake_socks.requests, "nothing reached the proxy. The request went somewhere else"
    connect = fake_socks.requests[0]
    assert connect[3] == ATYP_DOMAIN, f"expected a domain name, got ATYP {connect[3]}"
    assert connect[5 : 5 + connect[4]] == b"example.com"


def test_a_dead_proxy_raises_instead_of_connecting_directly(closed_port):
    with client_for(closed_port) as client:
        with pytest.raises(FetchError) as caught:
            client.get("https://example.com/feed.xml")
    # Refused by the proxy port, no direct attempt was made.
    assert "example.com" in str(caught.value)
    assert "Connect" in type(caught.value.__cause__).__name__


def test_a_broken_socks_handshake_is_wrapped_and_retried(fake_socks):
    """socksio raises outside the httpx hierarchy; unhandled it escapes the retry path."""
    with client_for(fake_socks.port) as client:
        with pytest.raises(FetchError):
            client.get("https://example.com/feed.xml", retries=2)
    assert len(fake_socks.requests) == 3, "the failure should have been retried, not raised raw"


# the run


def test_check_refuses_to_run_and_records_nothing(tmp_path, closed_port, capsys):
    config = tmp_path / "youpdated.yaml"
    config.write_text(CONFIG.format(port=closed_port), encoding="utf-8")
    state = tmp_path / "state.sqlite3"

    code = main(["check", "-c", str(config), "--state", str(state)])

    assert code == 1
    assert "Proxy error" in capsys.readouterr().out
    assert not state.exists()


def test_check_proceeds_when_the_proxy_answers(tmp_path, fake_socks, monkeypatch, capsys):
    config = tmp_path / "youpdated.yaml"
    config.write_text(CONFIG.format(port=fake_socks.port), encoding="utf-8")
    # Stub the fetch: this asserts the preflight let the run start, not findings
    monkeypatch.setattr("youpdated.cli.run", lambda *a, **k: RunResult())

    code = main(["check", "-c", str(config), "--state", str(tmp_path / "s.sqlite3")])

    assert code == 0
    assert "Proxy error" not in capsys.readouterr().out


def test_test_mode_reports_a_dead_proxy_without_aborting(tmp_path, closed_port, capsys):
    config = tmp_path / "youpdated.yaml"
    config.write_text(CONFIG.format(port=closed_port), encoding="utf-8")

    code = main(["check", "--test", "-c", str(config), "--state", str(tmp_path / "s.sqlite3")])

    assert code == 0
    assert "proxy: unreachable" in capsys.readouterr().out


def test_no_proxy_configured_skips_the_probe(tmp_path, monkeypatch):
    config = tmp_path / "youpdated.yaml"
    config.write_text(
        "privacy:\n  jitter: [0, 0]\nsources:\n  feed:\n    - https://example.com/feed.xml\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("youpdated.cli.run", lambda *a, **k: RunResult())

    def explode(*a, **k):
        raise AssertionError("probe_proxy must not run when no proxy is configured")

    monkeypatch.setattr("youpdated.cli.probe_proxy", explode)
    assert main(["check", "-c", str(config), "--state", str(tmp_path / "s.sqlite3")]) == 0
