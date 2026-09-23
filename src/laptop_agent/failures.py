"""One place every swallowed failure gets recorded.

This exists because of two bugs that hid for a long time behind code that handled an
error "gracefully":

- Every ultra-tier turn returned HTTP 400 (`thinking_token_budget is not yet supported`).
  The provider caught `URLError`, returned None, and the orchestrator read None as
  congestion — so a permanently broken tier reported itself as merely busy.
- `document ... as pdf` answered "The model returned an empty document" whenever the API
  returned 503, because the same catch turned the error into an empty string.

Neither was visible anywhere. The fix is not fewer `except` blocks - a tool must not
crash the app - it is that catching something must also *record* it. A caught exception
is a fact about the system, and if nothing writes it down the next person debugs from
guesswork.

Read it with the `failures` command or `/api/failures`.

**It outlives the process, because the reason is worth nothing if it does not.** This was
an in-memory ring "for the session you are in", which defeated the purpose above: measured
on the real `.agent_data`, four `image` turns failed after ~61 seconds each and not one of
their reasons survived to be read, so the question "why does image generation fail" could
not be answered from the log that exists to answer it. Bounded still (`MAX_RECORDS`), and
still not an audit trail - audit.py is that.

`when` is **wall clock, never `time.monotonic()`**: monotonic counts from a point that
restarts with the process, so a persisted monotonic stamp read back by a fresh one puts
the age anywhere between negative and centuries. `model_status.py` learned this first.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from laptop_agent.storage import atomic_write_text, read_json

MAX_RECORDS = 200


@dataclass(frozen=True)
class Failure:
    where: str
    kind: str
    message: str
    when: float
    context: dict[str, object] = field(default_factory=dict)
    trace_tail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "where": self.where,
            "kind": self.kind,
            "message": self.message,
            "age_seconds": round(max(0.0, time.time() - self.when), 1),
            "context": dict(self.context),
            "trace_tail": self.trace_tail,
        }


class FailureLog:
    """Thread-safe ring of recent caught failures. Never raises: a recorder that can
    throw would turn a handled error into an unhandled one."""

    def __init__(self, limit: int = MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._records: deque[Failure] = deque(maxlen=max(1, limit))
        self._counts: dict[str, int] = {}
        self._path: Path | None = None

    def attach(self, path: Path) -> None:
        """Start persisting here, and adopt whatever is already written.

        The caller supplies the path — this never reads `load_config()`, which would make
        one process-wide directory out of every caller's own, the way traces from a test
        run once landed in the live `.agent_data`.
        """
        try:
            loaded = [Failure(**dict(row, context=dict(row.get("context") or {})))
                      for row in (read_json(path, []) or []) if isinstance(row, dict)]
        except Exception:
            loaded = []          # a corrupt or half-written file must not cost the log
        with self._lock:
            self._path = path
            # Anything recorded before the path was known (imports, early startup) keeps
            # its place at the end — the file is history, this session is the tail of it.
            merged = (loaded + list(self._records))[-(self._records.maxlen or 1):]
            self._records.clear()
            self._counts.clear()
            for entry in merged:
                self._records.append(entry)
                key = f"{entry.where}:{entry.kind}"
                self._counts[key] = self._counts.get(key, 0) + 1

    def _save_locked(self) -> None:
        """Best effort, and silent on failure by design: this is the recorder of last
        resort, so a write error here must not become the unhandled exception that a
        handled one was being recorded for."""
        if self._path is None:
            return
        try:
            rows = [{"where": r.where, "kind": r.kind, "message": r.message,
                     "when": r.when, "context": dict(r.context), "trace_tail": r.trace_tail}
                    for r in self._records]
            atomic_write_text(self._path, json.dumps(rows, indent=2))
        except Exception:
            pass

    def record(self, where: str, error: BaseException | str, **context: object) -> None:
        try:
            if isinstance(error, BaseException):
                kind = type(error).__name__
                try:
                    message = str(error)[:400] or kind
                except Exception:
                    message = kind
                try:
                    tail = "".join(
                        traceback.format_exception(type(error), error, error.__traceback__)
                    )[-700:]
                except Exception:
                    tail = ""
            else:
                kind, message, tail = "Reported", _small(error), ""
            entry = Failure(
                where=str(where)[:80],
                kind=kind,
                message=str(message)[:400],
                when=time.time(),
                context={str(k): _small(v) for k, v in list(context.items())[:8]},
                trace_tail=tail,
            )
            with self._lock:
                self._records.append(entry)
                key = f"{where}:{kind}"
                self._counts[key] = self._counts.get(key, 0) + 1
                self._save_locked()
        except Exception:
            # Recording is best-effort by definition. Losing a diagnostic must never
            # escalate into a crash.
            pass

    def recent(self, limit: int = 40) -> list[dict[str, object]]:
        with self._lock:
            records = list(self._records)[-max(1, limit):]
        return [record.as_dict() for record in reversed(records)]

    def summary(self) -> dict[str, object]:
        with self._lock:
            counts = dict(self._counts)
            total = len(self._records)
            newest = self._records[-1].as_dict() if self._records else None
        repeated = sorted(counts.items(), key=lambda item: -item[1])[:8]
        return {
            "kept": total,
            "distinct": len(counts),
            "most_frequent": [{"what": key, "count": count} for key, count in repeated],
            "newest": newest,
        }

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._counts.clear()
            self._save_locked()


def _small(value: object) -> object:
    """Context is for diagnosis, not for storing payloads - and never for secrets.

    A value whose __str__ raises must not cost us the record: the failure is the point,
    the context is a nicety.
    """
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        text = str(value)
    except Exception:
        return f"<unprintable {type(value).__name__}>"
    return text[:200] + ("…" if len(text) > 200 else "")


# One log per process. Tools, providers and request handlers all write here.
FAILURES = FailureLog()


def record_failure(where: str, error: BaseException | str, **context: object) -> None:
    """Note that something was caught. Call this from every `except` that swallows."""
    FAILURES.record(where, error, **context)
