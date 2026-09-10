"""Per-turn latency traces: where a turn actually spent its time.

Deliberately records timings and outcomes, never prompts or replies. The point is to
answer "why did that feel slow" without turning the trace file into a second, unguarded
copy of the conversation. The only text kept is the resolved command's **verb** (``image``,
``web search``, ``chat``) — a tool name, not something the user wrote.
"""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from laptop_agent.storage import atomic_write_text, read_json, synchronized

import json


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))


@dataclass
class TurnTrace:
    """One turn's timing. Build with ``start()``, mark phases, then ``finish()``."""

    kind: str = "chat"                 # "chat" | "command"
    verb: str = ""                     # the resolved tool, e.g. "image" — never user text
    route_source: str = ""             # "heuristic" | "llm" | "direct"
    route_ms: int = 0
    tool_ms: int = 0
    ttft_ms: int | None = None         # time to first streamed token
    total_ms: int = 0
    model: str = ""                    # tier that answered: fast | smart | ultra | openrouter
    requested_model: str = ""          # tier asked for, when it differed
    degraded: bool = False
    ok: bool = True
    at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # Monotonic marks, not part of the persisted record.
    _t0: float = field(default_factory=time.monotonic, repr=False, compare=False)
    _route_at: float | None = field(default=None, repr=False, compare=False)
    _tool_at: float | None = field(default=None, repr=False, compare=False)

    def route_done(self, source: str) -> None:
        self.route_source = source
        self.route_ms = _ms(time.monotonic() - self._t0)
        self._route_at = time.monotonic()

    def tool_started(self) -> None:
        self._tool_at = time.monotonic()

    def tool_done(self) -> None:
        if self._tool_at is not None:
            self.tool_ms = _ms(time.monotonic() - self._tool_at)

    def first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = _ms(time.monotonic() - self._t0)

    def finish(self, ok: bool = True) -> "TurnTrace":
        self.total_ms = _ms(time.monotonic() - self._t0)
        self.ok = ok
        return self

    def record(self) -> dict[str, object]:
        data = {key: value for key, value in asdict(self).items() if not key.startswith("_")}
        return data


# The turn being timed. A ContextVar rather than an attribute because the web server
# answers turns on worker threads and the routing code is several frames down: two
# concurrent turns must not write into each other's trace.
_CURRENT: ContextVar[TurnTrace | None] = ContextVar("turn_trace", default=None)


def current_trace() -> TurnTrace | None:
    return _CURRENT.get()


def begin_trace(trace: TurnTrace):
    return _CURRENT.set(trace)


def end_trace(token) -> None:
    _CURRENT.reset(token)


class TraceStore:
    """A bounded ring of recent turn traces, persisted so history survives a restart."""

    def __init__(self, path: Path, max_traces: int = 300) -> None:
        self.path = Path(path)
        self.max_traces = max_traces
        self._lock = threading.Lock()
        self._traces: list[dict[str, object]] = []
        self._load()

    def _load(self) -> None:
        loaded = read_json(self.path, [])
        if isinstance(loaded, list):
            self._traces = [item for item in loaded if isinstance(item, dict)][-self.max_traces :]

    def _save(self) -> None:
        atomic_write_text(self.path, json.dumps(self._traces, indent=2, default=str))

    @synchronized
    def add(self, trace: TurnTrace) -> dict[str, object]:
        record = trace.record()
        self._traces.append(record)
        if len(self._traces) > self.max_traces:
            self._traces = self._traces[-self.max_traces :]
        self._save()
        return record

    @synchronized
    def recent(self, limit: int = 25) -> list[dict[str, object]]:
        return list(self._traces[-limit:])[::-1]

    @synchronized
    def summary(self, limit: int = 100) -> dict[str, object]:
        """Medians, not means: one 300s ultra turn should not describe the other ninety-nine."""
        window = self._traces[-limit:]
        if not window:
            return {"turns": 0}
        chat = [t for t in window if t.get("kind") == "chat"]
        commands = [t for t in window if t.get("kind") == "command"]

        def median(values: list[int]) -> int | None:
            numbers = sorted(v for v in values if isinstance(v, int))
            if not numbers:
                return None
            middle = len(numbers) // 2
            if len(numbers) % 2:
                return numbers[middle]
            return (numbers[middle - 1] + numbers[middle]) // 2

        def pick(rows: list[dict[str, object]], key: str) -> list[int]:
            return [row.get(key) for row in rows if isinstance(row.get(key), int)]  # type: ignore[misc]

        llm_routed = sum(1 for t in window if t.get("route_source") == "llm")
        return {
            "turns": len(window),
            "chat_turns": len(chat),
            "command_turns": len(commands),
            "median_total_ms": median(pick(window, "total_ms")),
            "median_route_ms": median(pick(window, "route_ms")),
            "median_tool_ms": median(pick(commands, "tool_ms")),
            "median_ttft_ms": median(pick(chat, "ttft_ms")),
            "median_chat_total_ms": median(pick(chat, "total_ms")),
            # The share of turns that paid an LLM round-trip only to classify the request.
            "llm_routed": llm_routed,
            "llm_routed_pct": round(100.0 * llm_routed / len(window), 1),
            "degraded": sum(1 for t in window if t.get("degraded")),
            "failed": sum(1 for t in window if t.get("ok") is False),
        }
