"""Model calls over kept-alive connections, so a turn pays the TCP and TLS handshake once.

`urllib.request.urlopen` sends `Connection: close` and opens a new socket for every call, so
the routing call and the chat stream that follows it each paid a full handshake to the same
host. `urlopen` here takes the same `Request` and timeout, raises the same `URLError` and
`HTTPError`, and returns an `http.client.HTTPResponse` behind a thin wrapper; a connection whose
response was read to the end goes back to a small per-host pool for the next call.
"""

from __future__ import annotations

import http.client
import io
import select
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from laptop_agent.failures import record_failure

# A server that closes an idle connection with a FIN is caught by `_alive`; a router or load
# balancer that drops it silently is not, and a request sent on it waits out the whole timeout.
# So nothing older than this is reused. Measured 2026-11-22: the NVIDIA host kept an idle
# connection for at least 95 s, and a new connection cost about 120 ms against 30 ms for a request
# on a kept one.
IDLE_SECONDS = 50.0
MAX_IDLE_PER_HOST = 4
# What the rest of a stream after `[DONE]` may cost: the closing chunk is a few bytes, already
# in flight. A server that keeps sending is not drained, its connection is closed instead.
DRAIN_BYTES = 65536
DRAIN_SECONDS = 1.0

_USER_AGENT = f"Python-urllib/{sys.version_info[0]}.{sys.version_info[1]}"
_STALE = (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

_lock = threading.Lock()
_idle: dict[tuple[str, str, int], list[tuple[http.client.HTTPConnection, float]]] = {}


def _alive(connection: http.client.HTTPConnection) -> bool:
    """An idle connection has nothing to read: readable means the server closed it (or sent
    something no request asked for), and either way it cannot carry the next request."""
    sock = connection.sock
    if sock is None:
        return False
    try:
        readable, _, _ = select.select([sock], [], [], 0)
    except (OSError, ValueError):
        return False
    return not readable


def _take(key: tuple[str, str, int]) -> http.client.HTTPConnection | None:
    now = time.monotonic()
    with _lock:
        pool = _idle.get(key, [])
        while pool:
            connection, since = pool.pop()
            if now - since <= IDLE_SECONDS and _alive(connection):
                return connection
            connection.close()
    return None


def _give_back(key: tuple[str, str, int], connection: http.client.HTTPConnection) -> None:
    with _lock:
        pool = _idle.setdefault(key, [])
        if len(pool) < MAX_IDLE_PER_HOST:
            pool.append((connection, time.monotonic()))
            return
    connection.close()


def clear() -> None:
    """Close every idle connection."""
    with _lock:
        pools = list(_idle.values())
        _idle.clear()
    for pool in pools:
        for connection, _ in pool:
            connection.close()


class PooledResponse:
    """The response, and the connection it came on: closing it returns the connection to the
    pool when the body was read to the end, and closes it otherwise."""

    def __init__(self, response: http.client.HTTPResponse, connection: http.client.HTTPConnection, key) -> None:
        self._response = response
        self._connection = connection
        self._key = key
        self._closed = False
        self.status = response.status
        self.headers = response.headers

    @property
    def fp(self):
        # `cancellation.interruptible_response` finds the socket here to shut it on Stop.
        return self._response.fp

    def read(self, amt: int | None = None) -> bytes:
        return self._response.read(amt)

    def __iter__(self):
        return iter(self._response)

    def __enter__(self) -> PooledResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def drain(self) -> None:
        """Read what is left of a body that is known to be ending, so its connection can be
        reused; a body still going after `DRAIN_SECONDS` or `DRAIN_BYTES` is left, and closed."""
        if self._closed or self._response.isclosed() or self._connection.sock is None:
            return
        try:
            self._connection.sock.settimeout(DRAIN_SECONDS)
            self._response.read(DRAIN_BYTES)
        except (OSError, http.client.HTTPException) as exc:
            record_failure("llm/drain", exc)  # the connection is closed, not reused

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        reusable = self._response.isclosed() and not self._response.will_close
        self._response.close()
        if reusable:
            _give_back(self._key, self._connection)
        else:
            self._connection.close()


def drain(response: object) -> None:
    """`PooledResponse.drain` for a response that has one: a test's stand-in has not."""
    finish = getattr(response, "drain", None)
    if callable(finish):
        finish()


def _proxied(host: str) -> bool:
    proxies = urllib.request.getproxies()
    return bool(proxies.get("https") or proxies.get("http")) and not urllib.request.proxy_bypass(host)


def urlopen(request: urllib.request.Request, timeout: float | None = None):
    """`urllib.request.urlopen(request, timeout=...)` for a model call, on a kept-alive
    connection. Behind a proxy it is urllib's own, which knows how to reach one."""
    parts = urllib.parse.urlsplit(request.full_url)
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or _proxied(host):
        return urllib.request.urlopen(request, timeout=timeout)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    key = (parts.scheme, host, port)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    headers = {name.title(): value for name, value in request.header_items()}
    headers.setdefault("User-Agent", _USER_AGENT)

    connection = _take(key)
    fresh = connection is None
    while True:
        if connection is None:
            factory = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
            connection = factory(host, port, timeout=timeout)
        elif connection.sock is not None:
            connection.sock.settimeout(timeout)
        try:
            connection.request(request.get_method(), path, body=request.data, headers=headers)
            response = connection.getresponse()
            break
        except _STALE as exc:
            connection.close()
            if not fresh:
                # The server had closed the idle connection: once more, on a new one.
                connection, fresh = None, True
                continue
            raise urllib.error.URLError(exc) from exc
        except OSError as exc:
            connection.close()
            raise urllib.error.URLError(exc) from exc
        except BaseException:
            connection.close()
            raise
    pooled = PooledResponse(response, connection, key)
    if not 200 <= response.status < 300:
        try:
            body = response.read()
        finally:
            pooled.close()
        raise urllib.error.HTTPError(
            request.full_url, response.status, response.reason, response.headers, io.BytesIO(body)
        )
    return pooled
