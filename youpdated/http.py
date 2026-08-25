"""HTTP path for the tool.

All outbound go through :class:`Client`
"""

from __future__ import annotations

import random
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Sequence
from urllib.parse import urlsplit

import httpx

from .config import PrivacyConfig
from .state import State

try:  # ships with httpx[socks]; guard anyway so an HTTP-proxy-only install still imports
    from socksio.exceptions import SOCKSError
except ImportError:  # pragma: no cover
    class SOCKSError(Exception):
        """Placeholder when socksio is absent."""

NETWORK_ERRORS = (httpx.HTTPError, SOCKSError)

# Small pool of current desktop UAs. Rotating inside a plausible set
# it blends in better, but can be changed here
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    """Network or unexpected-status failure for one URL."""


class ProxyUnavailable(Exception):
    """The configured proxy could not be reached. Raised before any source runs."""


#: Where a proxy listens when the URL omits the port.
_DEFAULT_PROXY_PORTS = {"socks5": 1080, "socks4": 1080, "http": 8080, "https": 8080}


def probe_proxy(proxy: str, timeout: float = 5.0) -> None:
    """Open and drop a TCP connection to the proxy, or raise :class:`ProxyUnavailable`."""
    parts = urlsplit(proxy)
    host = parts.hostname
    if not host:
        raise ProxyUnavailable(f"`privacy.proxy` has no host: {proxy}")
    try:
        port = parts.port
    except ValueError as exc:  # out-of-range port in the URL
        raise ProxyUnavailable(f"`privacy.proxy` has an invalid port: {proxy}") from exc
    port = port or _DEFAULT_PROXY_PORTS.get(parts.scheme.lower())
    if port is None:
        raise ProxyUnavailable(
            f"`privacy.proxy` needs an explicit port for scheme `{parts.scheme}`: {proxy}"
        )

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        raise ProxyUnavailable(
            f"cannot reach the proxy at {host}:{port} — {exc}.\n"
            "Start the proxy (Tor listens on 9050 by default), or remove "
            "`privacy.proxy` from your config."
        ) from exc


@dataclass
class Fetched:
    url: str
    status: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        import json

        return json.loads(self.content)


class Client:
    """Paced, proxied, no cookie HTTP client with conditional-GET support."""

    def __init__(
        self,
        privacy: PrivacyConfig | None = None,
        state: State | None = None,
        *,
        test: bool = False,
        verbose: bool = False,
        use_conditional: bool = True,
        transport: httpx.BaseTransport | None = None,
        logger=None,
    ):
        self.privacy = privacy or PrivacyConfig()
        self.state = state
        self.test = test
        self.verbose = verbose
        # Conditional GETs save requests, but a 304 doesn't return items
        self.use_conditional = use_conditional
        #: Seconds multiplied by the attempt number between retries. Tests set
        #: this to 0 so a retry path costs no wall-clock time.
        self.retry_backoff = 1.5
        self._log = logger
        self.requested_urls: list[str] = []

        self._host_locks: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._registry_lock = threading.Lock()

        # One upstream document can back several targets: every Brave channel lives
        # in one releases feed, every Firefox channel in one JSON. Bodies fetched
        # during this run are reused so those targets neither re-download the
        # document nor lose it to a validator this same run stored.
        self._run_bodies: dict[tuple[str, tuple[tuple[str, str], ...]], Fetched] = {}
        self._bodies_lock = threading.Lock()
        #: Only reuse bodies inside :meth:`run_scope`. A client used directly,
        #: outside a run, keeps the plain one-GET-per-call contract.
        self._run_active = False

        self._client = httpx.Client(
            proxy=self.privacy.proxy,
            timeout=self.privacy.timeout,
            follow_redirects=True,
            transport=transport,
            headers={"Accept-Language": "en-US,en;q=0.9"},
        )

    # lifecycle

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def describe(self) -> str:
        proxy = self.privacy.proxy or "none (direct)"
        ua = "rotating" if self.privacy.user_agent == "rotate" else self.privacy.user_agent
        return (
            f"proxy={proxy} user_agent={ua} "
            f"jitter={self.privacy.jitter[0]}-{self.privacy.jitter[1]}s "
            f"concurrency={self.privacy.concurrency} timeout={self.privacy.timeout}s"
        )

    @contextmanager
    def run_scope(self) -> "Iterator[Client]":
        """Reuse each fetched document for the duration of one run.

        Bodies are dropped again on exit so a client reused for a later run never serves stale data.
        """
        with self._bodies_lock:
            self._run_bodies.clear()
            self._run_active = True
        try:
            yield self
        finally:
            with self._bodies_lock:
                self._run_bodies.clear()
                self._run_active = False

    # internals

    def _user_agent(self) -> str:
        if self.privacy.user_agent == "rotate":
            return random.choice(USER_AGENTS)
        return self.privacy.user_agent

    def _pace(self, host: str) -> None:
        """Serialize per host and leave a rand gap, so traffic to one site never looks like a burst."""
        with self._registry_lock:
            lock = self._host_locks.setdefault(host, threading.Lock())
        with lock:
            low, high = self.privacy.jitter
            last = self._host_last.get(host)
            now = time.monotonic()
            if last is not None:
                wait = random.uniform(low, high) - (now - last)
                if wait > 0:
                    time.sleep(wait)
            self._host_last[host] = time.monotonic()

    def _cached_body(
        self, key: tuple[str, tuple[tuple[str, str], ...]]
    ) -> "Fetched | None":
        """The 200 body already fetched for this key during this run, if any."""
        with self._bodies_lock:
            return self._run_bodies.get(key) if self._run_active else None

    def note(self, message: str) -> None:
        if self.verbose and self._log:
            self._log(message)

    # request method

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        conditional: bool = False,
        soft_statuses: Sequence[int] | Iterable[int] = (),
        retries: int = 2,
    ) -> Fetched | None:
        """GET ``url``.

        Returns ``None`` when the server answers 304 Not Modified, or with ``test``. 
        Statuses listed in ``soft_statuses`` come back as a :class:`Fetched` for interpretation
        (itch.io answers 404 for a game that simply has no devlog)
        """
        self.requested_urls.append(url)
        if self.test:
            self.note(f"[test]] GET {url}")
            return None

        soft = frozenset(soft_statuses)
        conditional = conditional and self.use_conditional
        cache_key = (url, tuple(sorted((headers or {}).items())))
        cached = self._cached_body(cache_key)
        if cached is not None:
            self.note(f"GET {url} -> reusing the copy already fetched this run")
            return cached
        request_headers = {"User-Agent": self._user_agent()}
        if headers:
            request_headers.update(headers)
        if conditional and self.state is not None:
            request_headers.update(self.state.conditional_headers(url))

        host = urlsplit(url).netloc
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            self._pace(host)
            try:
                # Cookies dropped every request
                self._client.cookies.clear()
                response = self._client.get(url, headers=request_headers)
            except NETWORK_ERRORS as exc:
                last_error = exc
                self.note(f"GET {url} -> {type(exc).__name__}: {exc}")
                if attempt < retries:
                    time.sleep(self.retry_backoff * (attempt + 1))
                    continue
                raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc

            self.note(f"GET {url} -> {response.status_code}")

            if response.status_code == 304:
                # Normally "unchanged since your last run", so there is nothing to
                # report. But if this run already holds the body, the validator we
                # sent was our own from moments ago: hand back what we have. Only
                # reachable when two threads race past the check above; the common
                # case is served from the cache without a request at all.
                return self._cached_body(cache_key)

            if response.status_code == 200:
                result = Fetched(
                    url=str(response.url),
                    status=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )
                # Cache the body before publishing validator. A thread racing
                # can only be answered 304 once the validator is stored, so this
                # ordering guarantees the body is already there for it to fall back
                # on.
                with self._bodies_lock:
                    if self._run_active:
                        self._run_bodies[cache_key] = result
                if conditional and self.state is not None:
                    self.state.remember_validators(
                        url,
                        response.headers.get("etag"),
                        response.headers.get("last-modified"),
                    )
                return result

            if response.status_code in soft:
                return Fetched(
                    url=str(response.url),
                    status=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )

            if response.status_code in RETRY_STATUSES and attempt < retries:
                time.sleep(self.retry_backoff * (attempt + 1))
                continue

            raise FetchError(f"{url}: HTTP {response.status_code}")

        raise FetchError(f"{url}: {last_error}")
