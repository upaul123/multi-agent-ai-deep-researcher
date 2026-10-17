"""HTTP helpers: retries with backoff for transient errors, and SSRF-safe page fetching.

Agents cannot browse freely. They can only (a) call the configured search APIs and (b) fetch pages
whose URLs came from those search results, through `safe_fetch`, which allows only public http(s)
hosts, caps size and time, and accepts only HTML/text responses.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from typing import TypeVar
from urllib.parse import urljoin, urlparse

import httpx

from src.search.base import ProviderError
from src.services.observability import operation, span

logger = logging.getLogger(__name__)
T = TypeVar("T")

TRANSIENT_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_FETCH_BYTES = 3 * 1024 * 1024


def with_retries(fn: Callable[[], T], attempts: int = 3, base_delay: float = 1.0,
                 sleep: Callable[[float], None] | None = None) -> T:
    """Call `fn`, retrying transient failures (timeouts, connection errors, 429/5xx) with exponential backoff."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with span("http.request_attempt", metadata={"attempt": attempt}) as telemetry:
                try:
                    result = fn()
                    telemetry["outcome"] = "completed"
                    return result
                except httpx.HTTPStatusError as exc:
                    telemetry["http_status"] = exc.response.status_code
                    raise
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status not in TRANSIENT_STATUS:
                raise ProviderError(f"HTTP {status} from {exc.request.url.host}") from exc
            last = exc
            retry_after = exc.response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else base_delay * 2 ** (attempt - 1)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
            delay = base_delay * 2 ** (attempt - 1)
        if attempt < attempts:
            logger.info("Transient error (%s); retrying in %.1fs", type(last).__name__, delay)
            (sleep or time.sleep)(min(delay, 20))
    raise ProviderError(f"Failed after {attempts} attempts: {type(last).__name__}") from last


class HttpClient:
    def __init__(self, timeout: float = 20.0, contact_email: str | None = None, transport: httpx.BaseTransport | None = None):
        agent = "DeepResearcherAI/0.1 (research assistant" + (f"; mailto:{contact_email}" if contact_email else "") + ")"
        self._fetch_timeout, self._fetch_transport = timeout, transport
        self.client = httpx.Client(timeout=timeout, headers={"User-Agent": agent}, follow_redirects=False,
                                   transport=transport)

    @operation("http.get_json")
    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        def call():
            response = self.client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
        return with_retries(call)

    @operation("http.post_json")
    def post_json(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        def call():
            response = self.client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        return with_retries(call)

    @operation("http.get_text")
    def get_text(self, url: str, params: dict | None = None) -> str:
        def call():
            response = self.client.get(url, params=params)
            response.raise_for_status()
            return response.text
        return with_retries(call)

    @operation("http.safe_fetch")
    def safe_fetch(self, url: str, max_redirects: int = 3) -> str | None:
        """Fetch a public web page's HTML. Returns None (never raises) when blocked or unavailable."""
        for _ in range(max_redirects + 1):
            addresses = _public_addresses(url)
            if not addresses:
                logger.info("Blocked non-public URL")
                return None
            try:
                original = httpx.URL(url)
                pinned = original.copy_with(host=addresses[0])
                # A fresh per-hop client prevents a connection authenticated for another hostname
                # on the same IP being reused. Proxies must not resolve the original host for us.
                with httpx.Client(timeout=self._fetch_timeout, headers=self.client.headers,
                                  transport=self._fetch_transport, trust_env=False,
                                  follow_redirects=False) as fetch_client, \
                     span("http.fetch_attempt", metadata={"redirect_hop": _}) as telemetry, \
                     fetch_client.stream("GET", pinned, headers={"Host": original.netloc.decode("ascii")},
                                         extensions={"sni_hostname": original.raw_host.decode("ascii")}) as response:
                    telemetry["http_status"] = response.status_code
                    if response.is_redirect:
                        url = urljoin(str(original), response.headers.get("location", ""))
                        if not response.headers.get("location"):
                            return None
                        continue
                    if response.status_code != 200:
                        return None
                    ctype = response.headers.get("content-type", "")
                    if "html" not in ctype and "text/plain" not in ctype:
                        return None
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_FETCH_BYTES:
                            break
                        chunks.append(chunk)
                    return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
            except (httpx.HTTPError, ValueError):
                return None
        return None


def _public_addresses(url: str) -> list[str]:
    """Resolve once and reject the whole answer set unless every address is globally routable."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return []
        normalized = httpx.URL(url)
        host = normalized.raw_host.decode("ascii")
        if host == "localhost" or host.endswith((".local", ".internal")):
            return []
        port = normalized.port or (443 if parsed.scheme == "https" else 80)
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        else:
            addresses = [literal]
        if not addresses or any(not ip.is_global or ip.is_multicast or
                                (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global)
                                for ip in addresses):
            return []
        return list(dict.fromkeys(str(ip) for ip in addresses))
    except (OSError, ValueError, httpx.InvalidURL):
        return []


def is_public_http_url(url: str) -> bool:
    """Check URL eligibility; safe_fetch also pins the connection to its validated DNS result."""
    return bool(_public_addresses(url))
