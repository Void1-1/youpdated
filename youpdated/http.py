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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any, Generator, Iterable, Sequence
from urllib.parse import urlsplit

import httpx

from . import __version__
from .config import PrivacyConfig
from .state import State

# failures before the request leaves
UNSENT_ERRORS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.ProxyError,
)
NETWORK_ERRORS: tuple[type[Exception], ...]
try:  # ships with httpx[socks]; guard anyway so an HTTP-proxy-only install still imports
    from socksio.exceptions import SOCKSError

    NETWORK_ERRORS = (httpx.HTTPError, SOCKSError)
    UNSENT_ERRORS += (SOCKSError,)
except ImportError:  # pragma: no cover
    NETWORK_ERRORS = (httpx.HTTPError,)

# ``(url, etag, last_modified)`` from a 200, see :meth:`Client.deferred_validators`
Validator = tuple[str, str | None, str | None]

# Small pool of current desktop UAs. Rotating inside a plausible set
# it blends in better, but can be changed here
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:160.0) Gecko/20100101 Firefox/160.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/27.2 Safari/605.1.15",
)

# `user_agent: youpdated`: states the tool, version, and where to get info
YOUPDATED_USER_AGENT = f"Youpdated/{__version__} (+https://github.com/Void1-1/youpdated)"

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# Statuses whose ``Retry-After`` header is honored (RFC 6585 §4, RFC 9110 §10.2.3).
RETRY_AFTER_STATUSES = frozenset({429, 503})
# A POST is retried only when the server says it did nothing; a 500, 502, or 504 may come
# after the webhook was delivered
POST_RETRY_STATUSES = RETRY_AFTER_STATUSES
# An unexpected refusal (not a ``soft_statuses``) is usually a host-wide block
BLOCKED_STATUSES = frozenset({403})

# httpx's default
MAX_REDIRECTS = 20
# Headers a caller may pass that carry nothing secret, so a redirect to another host keeps them
_PORTABLE_HEADERS = frozenset({"user-agent", "accept", "accept-language"})


def _same_origin(a: httpx.URL, b: httpx.URL) -> bool:
    """same scheme, host, and port, or the same host from http to https"""
    if a.host != b.host:
        return False
    if a.scheme == b.scheme:
        return a.port == b.port
    return (a.scheme, b.scheme) == ("http", "https") and a.port in (None, 80) and b.port in (None, 443)


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds a ``Retry-After`` header asks for, or ``None`` if absent or unreadable.

    Takes both forms the header allows: delta-seconds (``120``) and an HTTP-date
    """
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:  # HTTP-dates are GMT
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def redact_url(url: str) -> str:
    """``scheme://host/…``: tell endpoints apart without leaking secret paths"""
    parts = urlsplit(url)
    tail = "/…" if parts.path.strip("/") or parts.query else ""
    return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{tail}"


class FetchError(Exception):
    """Network or unexpected-status failure for one URL."""


class ProxyUnavailable(Exception):
    """The configured proxy could not be reached. Raised before any source runs."""


# Where a proxy listens when the URL omits the port.
_DEFAULT_PROXY_PORTS = {"socks5": 1080, "socks4": 1080, "http": 8080, "https": 8080}


def probe_proxy(proxy: str, timeout: float = 5.0) -> None:
    """Open and drop a TCP connection to the proxy, or raise :class:`ProxyUnavailable`."""
    parts = urlsplit(proxy)
    host = parts.hostname
    if not host:
        raise ProxyUnavailable(f"`settings.privacy.proxy` has no host: {proxy}")
    try:
        port = parts.port
    except ValueError as exc:  # out-of-range port in the URL
        raise ProxyUnavailable(f"`settings.privacy.proxy` has an invalid port: {proxy}") from exc
    port = port or _DEFAULT_PROXY_PORTS.get(parts.scheme.lower())
    if port is None:
        raise ProxyUnavailable(
            f"`settings.privacy.proxy` needs an explicit port for scheme `{parts.scheme}`: {proxy}"
        )

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError as exc:
        raise ProxyUnavailable(
            f"cannot reach the proxy at {host}:{port}: {exc}.\n"
            "Start the proxy or remove `settings.privacy.proxy`."
        ) from exc


# Without a proxy, a connection that hasn't opened in this long stops
CONNECT_TIMEOUT = 8.0
# once it has timed out, it's tried only this many more times
CONNECT_TIMEOUT_RETRIES = 1

# Gaps between hits on one host in fast mode, instead of `privacy.jitter`
FAST_JITTER: tuple[float, float] = (0.1, 0.5)

# (Some) platforms that give each user a subdomain, paced as one host
SHARED_HOST_SUFFIXES: frozenset[str] = frozenset({
    "itch.io",
    "github.io",
    "gitlab.io",
    "codeberg.page",
    "substack.com",
    "blogspot.com",
    "wordpress.com",
    "tumblr.com",
    "medium.com",
    "bandcamp.com",
    "neocities.org",
})

# Public suffixes two labels long
_TWO_LABEL_SUFFIXES: frozenset[str] = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "co.jp", "ne.jp", "or.jp", "ac.jp", "co.kr", "or.kr",
    "co.in", "org.in", "co.za", "co.il", "com.br", "com.cn", "com.tw", "com.hk",
    "com.mx", "com.ar", "com.tr", "com.sg", "com.my", "com.pl", "com.ua",
})


def _hostname(netloc: str) -> str:
    """``netloc`` without login, port, or trailing dot, lowercased"""
    host = netloc.rpartition("@")[2].lower()
    if host.startswith("["):  # IPv6 literal
        return host.partition("]")[0] + "]"
    return host.partition(":")[0].rstrip(".")


def site_of(netloc: str) -> str:
    """The site a host belongs to"""
    host = _hostname(netloc)
    if host.startswith("[") or host.replace(".", "").isdigit():
        return host
    labels = host.split(".")
    keep = 3 if ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES else 2
    return ".".join(labels[-keep:])


def pace_key(netloc: str, *, by_site: bool = True) -> str:
    """What requests to ``netloc`` are paced as"""
    if by_site:
        return site_of(netloc)
    host = _hostname(netloc)
    labels = host.split(".")
    for i in range(1, len(labels) - 1):
        suffix = ".".join(labels[i:])
        if suffix in SHARED_HOST_SUFFIXES:
            return suffix
    return host


class _Host:
    """Pacing state of a host, see :meth:`Client._turn`"""

    __slots__ = ("cond", "next_at", "busy", "holds")

    def __init__(self) -> None:
        self.cond = threading.Condition(threading.Lock())
        self.next_at = 0.0
        # a thread has the next turn and is waiting out the gap
        self.busy = False
        self.holds: dict[str, float] = {}


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
        # Seconds multiplied by the attempt number between retries. Tests set
        # this to 0 so a retry path costs no time.
        self.retry_backoff = 1.5
        # Longest ``Retry-After`` the client will wait
        self.retry_after_max = 60.0
        self._log = logger
        self.requested_urls: list[str] = []

        self._hosts: dict[str, _Host] = {}
        # With `user_agent: rotate`, the one each site is sent: a browser keeps its own
        self._agents: dict[str, str] = {}
        self._registry_lock = threading.Lock()
        # `privacy.concurrency` caps requests in flight
        self._in_flight = threading.BoundedSemaphore(max(1, self.privacy.concurrency))

        # One upstream document can back several targets: every Brave channel lives
        # in one releases feed, every Firefox channel in one JSON. Bodies fetched
        # during this run are reused so those targets neither re-download the
        # document nor lose it to a validator this same run stored.
        self._run_bodies: dict[tuple[str, tuple[tuple[str, str], ...]], Fetched] = {}
        self._bodies_lock = threading.Lock()
        # Only reuse bodies inside :meth:`run_scope`. A client used directly,
        # outside a run, keeps the plain one-GET-per-call contract.
        self._run_active = False
        # GETs in progress, by the same key
        self._flights: dict[tuple[str, tuple[tuple[str, str], ...]], threading.Event] = {}
        # Hosts that gave up on this run (unreachable, rate limited), by pace key
        self._down: dict[str, str] = {}
        # Per worker thread: send no validators, see :meth:`unconditional`
        self._local = threading.local()

        self._client = httpx.Client(
            proxy=self.privacy.proxy,
            timeout=self._timeout(),
            # Followed by hand in _send, so headers can be dropped on the way off-host
            follow_redirects=False,
            transport=transport,
            headers={"Accept-Language": "en-US,en;q=0.9"},
            cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
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
            f"jitter={self._jitter()[0]}-{self._jitter()[1]}s "
            f"concurrency={self.privacy.concurrency} timeout={self.privacy.timeout}s"
        ) + (" fast_mode=on" if self.privacy.fast_mode else "")

    @contextmanager
    def run_scope(self) -> "Generator[Client, None, None]":
        """Reuse each fetched document for the duration of one run.

        Bodies are dropped again on exit so a client reused for a later run never serves stale data.
        """
        with self._bodies_lock:
            self._run_bodies.clear()
            self._down.clear()
            self._run_active = True
        try:
            yield self
        finally:
            with self._bodies_lock:
                self._run_bodies.clear()
                self._down.clear()
                self._run_active = False

    @contextmanager
    def unconditional(self) -> "Generator[Client, None, None]":
        """Send no stored validators from this thread, so every GET returns a body"""
        previous = getattr(self._local, "unconditional", False)
        self._local.unconditional = True
        try:
            yield self
        finally:
            self._local.unconditional = previous

    @contextmanager
    def deferred_validators(self) -> "Generator[list[Validator], None, None]":
        """Collect validators this thread receives instead of storing"""
        previous = getattr(self._local, "pending", None)
        pending: list[Validator] = []
        self._local.pending = pending
        try:
            yield pending
        finally:
            self._local.pending = previous

    @contextmanager
    def tracked_urls(self) -> "Generator[list[str], None, None]":
        """collect conditional URLs this thread asks for"""
        previous = getattr(self._local, "urls", None)
        urls: list[str] = []
        self._local.urls = urls
        try:
            yield urls
        finally:
            self._local.urls = previous

    # internals

    def _user_agent(self, host: str) -> str:
        if self.privacy.user_agent == "youpdated":
            return YOUPDATED_USER_AGENT
        if self.privacy.user_agent != "rotate":
            return self.privacy.user_agent
        with self._registry_lock:
            return self._agents.setdefault(self._key(host), random.choice(USER_AGENTS))

    def _key(self, host: str) -> str:
        """The site lane ``host`` is paced in"""
        return pace_key(host, by_site=not self.privacy.fast_mode)

    @staticmethod
    def _held_key(host: str) -> str:
        """What a 429, backoff, or give-up applies to"""
        return pace_key(host, by_site=False)

    def _host(self, host: str) -> _Host:
        host = self._key(host)
        with self._registry_lock:
            state = self._hosts.get(host)
            if state is None:
                state = self._hosts[host] = _Host()
            return state

    def _jitter(self) -> tuple[float, float]:
        return FAST_JITTER if self.privacy.fast_mode else self.privacy.jitter

    def _gap(self) -> float:
        low, high = self._jitter()
        return random.uniform(low, high)

    def _give_up_on(self, host: str, why: str) -> None:
        """Fail this run's other requests to ``host`` without sending"""
        with self._bodies_lock:
            if self._run_active:
                self._down.setdefault(self._held_key(host), why)

    def _check_up(self, host: str, url: str) -> None:
        with self._bodies_lock:
            why = self._down.get(self._held_key(host)) if self._run_active else None
        if why is not None:
            raise FetchError(f"{url}: not sent, {self._held_key(host)} {why} earlier this run")

    @contextmanager
    def _turn(self, host: str) -> "Generator[None, None, None]":
        """Hold a request slot for one send to ``host``"""
        state = self._host(host)
        held_key = self._held_key(host)
        queued = acquired = False
        try:
            while True:
                with state.cond:
                    held = state.holds.get(held_key, 0.0)
                wait = held - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                with state.cond:
                    while state.busy:
                        state.cond.wait()
                    state.busy = queued = True
                    slot = state.next_at
                wait = slot - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                acquired = self._in_flight.acquire()
                # Shown before the next gap is measured from now, so it can't eat into it
                announce = getattr(self._local, "announce", None)
                if announce is not None:
                    self._local.announce = None
                    announce()
                with state.cond:
                    state.busy = queued = False
                    state.cond.notify_all()
                    # Unless a 429 pushed this host or the site out while this thread waited
                    if state.next_at == slot and state.holds.get(held_key, 0.0) == held:
                        # A slot in the past means the site has been idle for a gap
                        state.next_at = max(slot, time.monotonic()) + self._gap()
                        break
                acquired = False
                self._in_flight.release()
            yield
        finally:
            if acquired:
                self._in_flight.release()
            if queued:
                with state.cond:
                    state.busy = False
                    state.cond.notify_all()

    def _pace(self, host: str) -> None:
        """Wait for ``host``'s next turn, without sending anything in it"""
        with self._turn(host):
            pass

    def _hold_host(self, host: str, seconds: float) -> None:
        """Push the host's next request ``seconds`` out, plus the usual gap"""
        state = self._host(host)
        key = self._held_key(host)
        with state.cond:
            until = time.monotonic() + seconds + self._gap()
            state.holds[key] = max(state.holds.get(key, 0.0), until)
            state.cond.notify_all()

    @contextmanager
    def first_request(self, callback) -> "Generator[None, None, None]":
        """Call ``callback`` when this thread's next request is about to be sent"""
        previous = getattr(self._local, "announce", None)
        self._local.announce = callback
        try:
            yield
        finally:
            self._local.announce = previous

    def _cached_body(
        self, key: tuple[str, tuple[tuple[str, str], ...]]
    ) -> "Fetched | None":
        """The 200 body already fetched for this key during this run, if any."""
        with self._bodies_lock:
            return self._run_bodies.get(key) if self._run_active else None

    def _timeout(self) -> httpx.Timeout:
        timeout = self.privacy.timeout
        if self.privacy.proxy:
            return httpx.Timeout(timeout)
        return httpx.Timeout(timeout, connect=min(timeout, CONNECT_TIMEOUT))

    def _retries_after(self, exc: Exception, retries: int) -> int:
        """How many tries ``exc`` leaves of ``retries``"""
        if isinstance(exc, httpx.ConnectTimeout) and not self.privacy.proxy:
            return min(retries, CONNECT_TIMEOUT_RETRIES)
        return retries

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
        follow_redirects: bool = True,
    ) -> Fetched | None:
        """GET ``url``.

        Returns ``None`` when the server answers 304 Not Modified, or with ``test``. 
        Statuses listed in ``soft_statuses`` come back as a :class:`Fetched` for interpretation
        (itch.io answers 404 for a game that simply has no devlog). Without ``follow_redirects``
        a redirect is a status like any other, so list it there to read its ``location``
        """
        self.requested_urls.append(url)
        tracked = getattr(self._local, "urls", None)
        if conditional and tracked is not None:
            tracked.append(url)
        if self.test:
            self.note(f"[test] GET {url}")
            return None

        soft = frozenset(soft_statuses)
        conditional = conditional and self.use_conditional
        cache_key = (url, tuple(sorted((headers or {}).items())))
        cached = self._cached_body(cache_key)
        if cached is not None:
            self.note(f"GET {url} -> reusing the copy already fetched this run")
            return cached

        with self._bodies_lock:
            # The first one may have landed since the check above
            cached = self._run_bodies.get(cache_key) if self._run_active else None
            flight = self._flights.get(cache_key) if self._run_active else None
            leader = cached is None and flight is None and self._run_active
            if leader:
                flight = self._flights[cache_key] = threading.Event()
        if cached is not None:
            self.note(f"GET {url} -> reusing the copy already fetched this run")
            return cached
        if flight is not None and not leader:
            flight.wait()
            cached = self._cached_body(cache_key)
            if cached is not None:
                self.note(f"GET {url} -> reusing the copy already fetched this run")
                return cached
            # The first one's answer had no body to share (304, an error): ask for ourselves
        try:
            return self._fetch(
                url, cache_key, headers, conditional, soft, retries, follow_redirects
            )
        finally:
            if leader:
                with self._bodies_lock:
                    self._flights.pop(cache_key, None)
                flight.set()

    def _fetch(
        self,
        url: str,
        cache_key: tuple[str, tuple[tuple[str, str], ...]],
        headers: dict[str, str] | None,
        conditional: bool,
        soft: frozenset[int],
        retries: int,
        follow_redirects: bool,
    ) -> Fetched | None:
        """The network part of :meth:`get`"""
        host = urlsplit(url).netloc
        request_headers = {"User-Agent": self._user_agent(host)}
        if headers:
            request_headers.update(headers)
        send_validators = not getattr(self._local, "unconditional", False)
        if conditional and send_validators and self.state is not None:
            request_headers.update(self.state.conditional_headers(url))

        last_error: Exception | None = None

        for attempt in range(retries + 1):
            self._check_up(host, url)
            try:
                with self._turn(host):
                    # It may have given up while this one waited its turn
                    self._check_up(host, url)
                    response = self._send(
                        "GET", url, request_headers, private=headers or (), follow=follow_redirects
                    )
            except NETWORK_ERRORS as exc:
                last_error = exc
                self.note(f"GET {url} -> {type(exc).__name__}: {exc}")
                if attempt < self._retries_after(exc, retries):
                    # Every request to the host waits, not just this one
                    self._hold_host(host, self.retry_backoff * (attempt + 1))
                    continue
                self._give_up_on(host, f"didn't answer ({type(exc).__name__})")
                raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc

            self.note(f"GET {url} -> {response.status_code}")

            if response.status_code == 304:
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
                    validator = (
                        url,
                        response.headers.get("etag"),
                        response.headers.get("last-modified"),
                    )
                    pending = getattr(self._local, "pending", None)
                    if pending is not None:
                        pending.append(validator)
                    else:
                        self.state.remember_validators(*validator)
                return result

            if response.status_code in soft:
                return Fetched(
                    url=str(response.url),
                    status=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )

            if response.status_code in RETRY_STATUSES and attempt < retries:
                self._wait_to_retry(response, attempt, host, "GET", url)
                continue

            if response.status_code in RETRY_AFTER_STATUSES:
                self._give_up_on(host, f"kept answering HTTP {response.status_code}")
            elif response.status_code in BLOCKED_STATUSES:
                self._give_up_on(host, f"refused a request (HTTP {response.status_code})")
            raise FetchError(f"{url}: HTTP {response.status_code}")

        raise FetchError(f"{url}: {last_error}")

    def post(
        self,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
        retries: int = 2,
    ) -> Fetched | None:
        """POST ``json`` to ``url``. For notifications only

        Webhook URLs carry secret in path, so only the host is logged or raised.
        Returns ``None`` with ``test``
        """
        shown = redact_url(url)
        if self.test:
            self.note(f"[test] POST {shown}")
            return None

        host = urlsplit(url).netloc
        request_headers = {"User-Agent": self._user_agent(host)}
        if headers:
            request_headers.update(headers)

        for attempt in range(retries + 1):
            try:
                with self._turn(host):
                    response = self._send(
                        "POST", url, request_headers, private=headers or (), json=json
                    )
            except NETWORK_ERRORS as exc:
                self.note(f"POST {shown} -> {type(exc).__name__}")
                # A read timeout or dropped connection can come after a delivered notification
                if attempt < retries and isinstance(exc, UNSENT_ERRORS):
                    self._hold_host(host, self.retry_backoff * (attempt + 1))
                    continue
                raise FetchError(f"{shown}: {type(exc).__name__}") from exc

            self.note(f"POST {shown} -> {response.status_code}")
            if 200 <= response.status_code < 300:
                return Fetched(
                    url=shown,
                    status=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )
            if response.status_code in POST_RETRY_STATUSES and attempt < retries:
                self._wait_to_retry(response, attempt, host, "POST", shown)
                continue
            raise FetchError(f"{shown}: HTTP {response.status_code}")

        raise FetchError(f"{shown}: gave up after {retries + 1} attempts")

    def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        private: Iterable[str],
        follow: bool = True,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request and follow redirects"""
        private = [name for name in private if name.lower() not in _PORTABLE_HEADERS]
        request = self._client.build_request(method, url, headers=headers, **kwargs)
        origin = request.url
        response = self._client.send(request)
        if not follow:
            return response
        for _ in range(MAX_REDIRECTS):
            hop = response.next_request
            if hop is None or hop.method != method:
                return response
            if not _same_origin(origin, hop.url):
                for name in private:
                    hop.headers.pop(name, None)
            response.close()
            response = self._client.send(hop)
        if response.next_request is None:
            return response
        raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.", request=response.request)

    def _wait_to_retry(
        self,
        response: httpx.Response,
        attempt: int,
        host: str,
        method: str,
        url: str,
    ) -> None:
        """Wait before retrying with applicable status, or raise if the server asks too much"""
        wait = None
        if response.status_code in RETRY_AFTER_STATUSES:
            wait = retry_after_seconds(response.headers.get("retry-after"))
        if wait is None:
            # Every request to the host waits, not just this one
            self._hold_host(host, self.retry_backoff * (attempt + 1))
        elif wait > self.retry_after_max:
            self._give_up_on(host, f"asked to wait {wait:.0f}s")
            raise FetchError(
                f"{url}: HTTP {response.status_code} (server asked to retry "
                f"after {wait:.0f}s, over the {self.retry_after_max:.0f}s limit)"
            )
        else:
            self.note(f"{method} {url} -> waiting {wait:.0f}s as Retry-After asks")
            # The next _pace sleeps until the held slot
            self._hold_host(host, wait)
