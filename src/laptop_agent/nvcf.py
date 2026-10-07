"""Which function id answers for a hosted NVIDIA speech model right now.

The Riva gRPC host picks the model by function id, and NVIDIA rotates those ids between
releases (its own `nemotron-speech` skill says to resolve them fresh). The app pins one per
model, so a rotation would fail every call. Measured 2026-10-06: an unknown or INACTIVE id
answers NOT_FOUND, while an outage answers UNAVAILABLE (a bare 502) or DEADLINE_EXCEEDED and
says nothing about the function. So NOT_FOUND alone sends us to the function list, once, for
the newest ACTIVE function with the model's exact name, and the call is retried once with it.

Agreed with Codex (pair log, 2026-10-06): an id the user set is never replaced, and a lookup
that cannot run - a key without list scope answers 403 - keeps the original failure and says
what to set, without trying again for `LOOKUP_EVERY` seconds."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections.abc import Callable
from typing import TypeVar

from laptop_agent.failures import record_failure

LIST_URL = "https://api.nvcf.nvidia.com/v2/nvcf/functions?visibility=authorized,public"
LOOKUP_EVERY = 1800.0

T = TypeVar("T")

_lock = threading.Lock()
_found: dict[str, str] = {}
_looked: dict[str, float] = {}


class StaleFunctionError(RuntimeError):
    """The pinned function id is gone and no current one could be found."""


def _list_functions(key: str) -> list[dict]:
    request = urllib.request.Request(LIST_URL, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return list(json.loads(response.read().decode("utf-8")).get("functions") or [])


def is_stale(error: BaseException) -> bool:
    code = getattr(error, "code", None)
    if not callable(code):
        return False
    try:
        return getattr(code(), "name", None) == "NOT_FOUND"
    except Exception:
        return False


def _lookup(name: str, key: str) -> str | None:
    now = time.monotonic()
    with _lock:
        last = _looked.get(name)
        if last is not None and now - last < LOOKUP_EVERY:
            return None
        _looked[name] = now
    try:
        functions = _list_functions(key)
    except Exception as exc:  # a key without list scope (403), or the network
        record_failure("nvcf/lookup", exc)
        return None
    active = [item for item in functions
              if item.get("name") == name and item.get("status") == "ACTIVE" and item.get("id")]
    if not active:
        record_failure("nvcf/lookup", LookupError(f"no ACTIVE function is named {name}"))
        return None
    return str(max(active, key=lambda item: str(item.get("createdAt") or ""))["id"])


def call(name: str, pinned: str, variable: str, key: str, attempt: Callable[[str], T]) -> T:
    """`attempt(function_id)`, with one retry on a current id if the one used has gone."""
    override = os.environ.get(variable, "").strip()
    if override:
        return attempt(override)
    with _lock:
        function_id = _found.get(name, pinned)
    try:
        return attempt(function_id)
    except Exception as exc:
        if not is_stale(exc):
            raise
        fresh = _lookup(name, key)
        if not fresh or fresh == function_id:
            raise StaleFunctionError(
                f"NVIDIA no longer finds {name} at the id this app uses. Set {variable} to its "
                "current function id, from the model's API page on build.nvidia.com."
            ) from exc
    with _lock:
        _found[name] = fresh
    return attempt(fresh)


def _reset() -> None:
    """For tests: forget every id found and every lookup made."""
    with _lock:
        _found.clear()
        _looked.clear()
