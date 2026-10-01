"""Per-turn latency traces: where a turn actually spent its time.

Deliberately records timings and outcomes, never prompts or replies. The point is to
answer "why did that feel slow" without turning the trace file into a second, unguarded
copy of the conversation. The only text kept is the resolved command's **verb** (``image``,
``web search``, ``chat``) — a tool name, not something the user wrote.
"""

from __future__ import annotations

import threading
import time
from bisect import bisect_left
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from laptop_agent.storage import atomic_write_text, read_json, synchronized

import json

# The ring of recent traces holds 300 turns, about three weeks here: too short to see an
# hour-of-week pattern. So every turn's timings are also appended to `<name>_timings.jsonl`,
# one short line each, kept for ROLLUP_DAYS, and `hourly()` folds them into hours when asked.
# An append costs the same however long the history is; rewriting a 90-day rollup file on
# every turn measured 122ms at its worst. LATENCY_BUCKETS are the upper bounds (ms) of the
# buckets an hour counts, the last bucket being everything slower.
ROLLUP_DAYS = 90
LATENCY_BUCKETS = (250, 500, 1000, 2000, 4000, 8000, 16000, 32000)
_HISTOGRAMS = ("total_ms", "ttft_ms")


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


def _timing(line: str | bytes) -> dict[str, object] | None:
    """One line of the timing log, or None for a line that cannot be one (a torn last write,
    a hand edit, bytes that are not UTF-8): the log is read inside a user's turn, and the
    caller guards only OSError."""
    try:
        entry = json.loads(line)
        at = datetime.fromisoformat(entry["at"]).astimezone(UTC)
    except (ValueError, TypeError, KeyError, OverflowError, OSError):
        # OverflowError and OSError: a time that parses but cannot be placed in UTC (year 9999
        # behind a negative offset; on Windows, a naive one before 1970).
        return None
    if not (isinstance(entry, dict) and isinstance(entry.get("kind"), str) and isinstance(entry.get("tier"), str)
            and isinstance(entry.get("ok"), bool) and isinstance(entry.get("degraded"), bool)):
        return None
    if any(entry.get(name) is not None and type(entry.get(name)) is not int for name in _HISTOGRAMS):
        return None
    return {**entry, "at": at}


class TraceStore:
    """A bounded ring of recent turn traces, persisted so history survives a restart, and a
    log of every turn's timings (`<name>_timings.jsonl`), kept for `ROLLUP_DAYS`."""

    def __init__(self, path: Path, max_traces: int = 300) -> None:
        self.path = Path(path)
        self.timings_path = self.path.with_name(self.path.stem + "_timings.jsonl")
        self.max_traces = max_traces
        self._lock = threading.Lock()
        self._traces: list[dict[str, object]] = []
        self._pruned_on = ""
        self._load()

    def _load(self) -> None:
        loaded = read_json(self.path, [])
        if isinstance(loaded, list):
            self._traces = [item for item in loaded if isinstance(item, dict)][-self.max_traces :]

    def _save(self) -> None:
        atomic_write_text(self.path, json.dumps(self._traces, indent=2, default=str))

    def _timings(self) -> list[dict[str, object]]:
        # Bytes, decoded line by line inside _timing: decoding the whole file first raised
        # UnicodeDecodeError for one bad byte anywhere in it (Codex's review of #157).
        try:
            lines = self.timings_path.read_bytes().splitlines()
        except FileNotFoundError:
            return []
        return [timing for timing in map(_timing, lines) if timing is not None]

    def _log_timing(self, record: dict[str, object]) -> None:
        """Append one turn's timings: when, the tier that was asked for (so a turn that fell
        back counts against the tier that failed it), and how it went. Timings and outcomes
        only, like the record itself: no verb, nothing the user wrote."""
        entry = {"at": record.get("at"), "kind": record.get("kind"),
                 "tier": record.get("requested_model") or record.get("model") or "",
                 "ok": record.get("ok") is not False, "degraded": bool(record.get("degraded")),
                 **{name: record.get(name) for name in _HISTOGRAMS}}
        if _timing(json.dumps(entry)) is None:
            return
        day = str(entry["at"])[:10]
        if day != self._pruned_on:   # once a day: drop what has aged out, in one rewrite
            self._pruned_on = day
            oldest = datetime.fromisoformat(str(entry["at"])).astimezone(UTC) - timedelta(days=ROLLUP_DAYS)
            kept = [timing for timing in self._timings() if timing["at"] >= oldest]
            # No backup: the prune drops lines on purpose, and backing up the old copy meant
            # decoding the whole file as UTF-8 again, which one bad byte turns into an error.
            atomic_write_text(self.timings_path, "".join(
                json.dumps({**timing, "at": timing["at"].isoformat()}) + "\n" for timing in kept), backup=False)
        with self.timings_path.open("a", encoding="utf-8", newline="\n") as log:
            log.write(json.dumps(entry) + "\n")

    @synchronized
    def add(self, trace: TurnTrace) -> dict[str, object]:
        record = trace.record()
        self._traces.append(record)
        if len(self._traces) > self.max_traces:
            self._traces = self._traces[-self.max_traces :]
        self._save()
        self._log_timing(record)
        return record

    @synchronized
    def hourly(self) -> list[dict[str, object]]:
        """The timing log folded into hours (UTC), oldest first: turns, failures and fallbacks
        per kind and tier, and how many turns fell in each `LATENCY_BUCKETS` bucket for total
        time and for time to first token. The log is pruned to `ROLLUP_DAYS` once a day."""
        rows: dict[tuple[str, str, str], dict[str, object]] = {}
        for timing in self._timings():
            hour = timing["at"].strftime("%Y-%m-%dT%H")
            row = rows.setdefault((hour, timing["kind"], timing["tier"]), {
                "hour": hour, "kind": timing["kind"], "tier": timing["tier"], "turns": 0, "failed": 0,
                "degraded": 0, **{name: [0] * (len(LATENCY_BUCKETS) + 1) for name in _HISTOGRAMS}})
            row["turns"] += 1
            row["failed"] += int(not timing["ok"])
            row["degraded"] += int(timing["degraded"])
            for name in _HISTOGRAMS:
                if timing.get(name) is not None:
                    row[name][bisect_left(LATENCY_BUCKETS, timing[name])] += 1
        return [rows[key] for key in sorted(rows)]

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
