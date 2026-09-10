"""Cooperative request cancellation shared by handlers, planners and tools."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import socket
import threading
import time


class OperationCancelled(asyncio.CancelledError):
    """Bypass ordinary tool-error fallback so cancellation cannot launch more work."""


class Operation:
    def __init__(self):
        self.stopped = threading.Event()
        self.sockets: set = set()


_current: ContextVar[Operation | None] = ContextVar("operation", default=None)
_lock = threading.RLock()
_active: dict[str, Operation] = {}
_pending: dict[str, float] = {}


@contextmanager
def operation(request_id: str):
    state = Operation()
    with _lock:
        if request_id in _active:
            raise ValueError("Request ID is already running")
        if _pending.pop(request_id, 0) > time.monotonic():
            state.stopped.set()
        _active[request_id] = state
    token = _current.set(state)
    try:
        check_cancelled()
        yield state
    finally:
        _current.reset(token)
        with _lock:
            _active.pop(request_id, None)


def cancel(request_id: str) -> bool:
    with _lock:
        now = time.monotonic()
        for key in list(_pending):
            if _pending[key] < now:
                _pending.pop(key)
        state = _active.get(request_id)
        if state is None:
            if len(_pending) < 1000:
                _pending[request_id] = now + 60
            return False
        state.stopped.set()
        for sock in list(state.sockets):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return True


def check_cancelled():
    state = _current.get()
    if state is not None and state.stopped.is_set():
        raise OperationCancelled("Stopped by user")


@contextmanager
def interruptible_response(response):
    state = _current.get()
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    if state is not None and sock is not None:
        with _lock:
            state.sockets.add(sock)
    try:
        check_cancelled()
        yield response
        check_cancelled()
    finally:
        if state is not None and sock is not None:
            with _lock:
                state.sockets.discard(sock)
