"""A short live status for a slow tool ("Writing the document"), shown while it runs.

The text is fixed by the tool, never the user's words: it goes into the stream as its own
event, and nothing the user typed should ride along with it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_listener: ContextVar[Callable[[str], None] | None] = ContextVar("progress_listener", default=None)


@contextmanager
def progress_listener(listener: Callable[[str], None]) -> Iterator[None]:
    token = _listener.set(listener)
    try:
        yield
    finally:
        _listener.reset(token)


def report_progress(status: str) -> None:
    listener = _listener.get()
    if listener is not None:
        listener(status)
