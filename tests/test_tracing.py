from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.tracing import TraceStore, TurnTrace, begin_trace, current_trace, end_trace


class TurnTraceTests(unittest.TestCase):
    def test_phases_are_recorded_in_milliseconds(self) -> None:
        trace = TurnTrace()
        trace.route_done("llm")
        trace.tool_started()
        trace.tool_done()
        trace.first_token()
        trace.finish(True)
        record = trace.record()
        self.assertEqual(record["route_source"], "llm")
        for key in ("route_ms", "tool_ms", "ttft_ms", "total_ms"):
            self.assertIsInstance(record[key], int, key)
            self.assertGreaterEqual(record[key], 0)

    def test_only_the_first_token_sets_time_to_first_token(self) -> None:
        trace = TurnTrace()
        trace.first_token()
        first = trace.ttft_ms
        for _ in range(5):
            trace.first_token()
        self.assertEqual(trace.ttft_ms, first)

    def test_a_record_never_carries_conversation_content(self) -> None:
        # The whole point of the trace file is that it is not a second copy of the chat.
        trace = TurnTrace(kind="command", verb="image")
        trace.finish(True)
        record = trace.record()
        self.assertEqual(
            set(record),
            {"kind", "verb", "route_source", "route_ms", "tool_ms", "ttft_ms", "total_ms",
             "model", "requested_model", "degraded", "ok", "at"},
        )

    def test_the_current_trace_is_scoped(self) -> None:
        self.assertIsNone(current_trace())
        trace = TurnTrace()
        token = begin_trace(trace)
        try:
            self.assertIs(current_trace(), trace)
        finally:
            end_trace(token)
        self.assertIsNone(current_trace())


class TraceStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "traces.json"
        self.addCleanup(self._tmp.cleanup)

    def add(self, store: TraceStore, **kwargs) -> None:
        trace = TurnTrace(**{k: v for k, v in kwargs.items() if k != "total_ms"})
        trace.finish(kwargs.get("ok", True))
        if "total_ms" in kwargs:
            trace.total_ms = kwargs["total_ms"]
        store.add(trace)

    def test_traces_survive_a_restart(self) -> None:
        store = TraceStore(self.path)
        self.add(store, kind="chat", total_ms=120)
        reopened = TraceStore(self.path)
        self.assertEqual(len(reopened.recent()), 1)
        self.assertEqual(reopened.recent()[0]["total_ms"], 120)

    def test_the_ring_is_bounded(self) -> None:
        store = TraceStore(self.path, max_traces=5)
        for index in range(12):
            self.add(store, kind="chat", total_ms=index)
        kept = store.recent(50)
        self.assertEqual(len(kept), 5)
        self.assertEqual([row["total_ms"] for row in kept], [11, 10, 9, 8, 7])  # newest first

    def test_summary_uses_medians_not_means(self) -> None:
        store = TraceStore(self.path)
        for value in (100, 100, 100, 100, 30000):  # one very slow turn
            self.add(store, kind="chat", total_ms=value)
        stats = store.summary()
        self.assertEqual(stats["turns"], 5)
        self.assertEqual(stats["median_total_ms"], 100)

    def test_summary_counts_the_extra_classification_call(self) -> None:
        store = TraceStore(self.path)
        for source in ("llm", "llm", "llm", "heuristic"):
            self.add(store, kind="chat", route_source=source)
        stats = store.summary()
        self.assertEqual(stats["llm_routed"], 3)
        self.assertEqual(stats["llm_routed_pct"], 75.0)

    def test_an_empty_store_summarises_to_nothing(self) -> None:
        self.assertEqual(TraceStore(self.path).summary(), {"turns": 0})

    def test_tool_time_is_measured_from_commands_only(self) -> None:
        store = TraceStore(self.path)
        self.add(store, kind="chat", route_source="llm")
        trace = TurnTrace(kind="command", verb="image", route_source="heuristic")
        trace.tool_started()
        trace.tool_done()
        trace.total_ms = 900
        trace.tool_ms = 800
        store.add(trace.finish(True))
        stats = store.summary()
        self.assertEqual(stats["command_turns"], 1)
        self.assertEqual(stats["median_tool_ms"], 800)


if __name__ == "__main__":
    unittest.main()
