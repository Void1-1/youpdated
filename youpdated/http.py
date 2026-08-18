"""HTTP path for the tool.

All outbound go through :class:`Client`
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
from urllib.parse import urlsplit

import httpx

from .config import PrivacyConfig
from .state import State

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
        self._log = logger
        self.requested_urls: list[str] = []

        self._host_locks: dict[str, threading.Lock] = {}
        self._host_last: dict[str, float] = {}
        self._registry_lock = threading.Lock()

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
            except httpx.HTTPError as exc:
                last_error = exc
                self.note(f"GET {url} -> {type(exc).__name__}: {exc}")
                if attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc

            self.note(f"GET {url} -> {response.status_code}")

            if response.status_code == 304:
                return None

            if response.status_code == 200:
                if conditional and self.state is not None:
                    self.state.remember_validators(
                        url,
                        response.headers.get("etag"),
                        response.headers.get("last-modified"),
                    )
                return Fetched(
                    url=str(response.url),
                    status=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )

            if response.status_code in soft:
                return Fetched(
                    url=str(response.url),
                    status=response.status_code,
                    content=response.content,
                    headers=dict(response.headers),
                )

            if response.status_code in RETRY_STATUSES and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue

            raise FetchError(f"{url}: HTTP {response.status_code}")

        raise FetchError(f"{url}: {last_error}")
