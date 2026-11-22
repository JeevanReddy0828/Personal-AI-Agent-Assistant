from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from laptop_agent.tracing import TraceStore, TurnTrace, begin_trace, current_trace, end_trace


class TraceRollupTests(unittest.TestCase):
    """The ring keeps 300 turns, about three weeks here: too short to see an hour-of-week
    pattern, so every turn is also folded into an hourly rollup kept for 90 days."""

    def test_an_hour_counts_fallbacks_against_the_tier_asked_for(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json", max_traces=2)
            store.add(TurnTrace(at="2026-10-01T05:10:00+00:00", model="smart", total_ms=900, ttft_ms=400))
            store.add(
                TurnTrace(
                    at="2026-10-01T05:40:00+00:00",
                    model="fast",
                    requested_model="smart",
                    degraded=True,
                    total_ms=3000,
                    ttft_ms=1500,
                )
            )
            store.add(TurnTrace(at="2026-10-01T05:50:00+00:00", model="smart", ok=False, total_ms=40000))
            store.add(TurnTrace(at="2026-10-01T06:01:00+00:00", kind="command", total_ms=250))
            rows = TraceStore(Path(raw) / "traces.json").hourly()  # outlives the ring and a reload
        self.assertEqual(len(rows), 2)
        smart, command = rows
        self.assertEqual((smart["hour"], smart["kind"], smart["tier"]), ("2026-10-01T05", "chat", "smart"))
        self.assertEqual((smart["turns"], smart["failed"], smart["degraded"]), (3, 1, 1))
        # Buckets: <=250, <=500, <=1000, <=2000, <=4000, <=8000, <=16000, <=32000, slower.
        self.assertEqual(smart["total_ms"], [0, 0, 1, 0, 1, 0, 0, 0, 1])
        self.assertEqual(smart["ttft_ms"], [0, 1, 0, 1, 0, 0, 0, 0, 0])
        self.assertEqual(command["total_ms"][0], 1, "an upper bound is inclusive")

    def test_an_hour_past_the_retention_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            store.add(TurnTrace(at="2026-07-01T05:00:00+00:00", total_ms=100))
            store.add(TurnTrace(at="2026-09-28T05:00:00+00:00", total_ms=100))  # 89 days on
            self.assertEqual(len(store.hourly()), 2)
            store.add(TurnTrace(at="2026-09-30T06:00:00+00:00", total_ms=100))  # 91 days on
            self.assertEqual([row["hour"] for row in store.hourly()], ["2026-09-28T05", "2026-09-30T06"])
            self.assertNotIn("2026-07-01", store.timings_path.read_text(encoding="utf-8"))

    def test_the_log_holds_timings_only(self) -> None:
        # The record keeps a verb; the log and the hours keep the tier, kind and outcome.
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            store.add(TurnTrace(at="2026-10-01T05:00:00+00:00", kind="command", verb="image"))
            line = json.loads(store.timings_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(
                set(line),
                {
                    "at",
                    "kind",
                    "tier",
                    "ok",
                    "degraded",
                    "total_ms",
                    "ttft_ms",
                    "answered",
                    "route",
                    "route_ms",
                    "tool_ms",
                    "tier_source",
                    "tier_ms",
                },
            )
            self.assertNotIn("image", json.dumps(line))
            self.assertEqual(
                set(store.hourly()[0]), {"hour", "kind", "tier", "turns", "failed", "degraded", "total_ms", "ttft_ms"}
            )

    def test_the_log_keeps_where_the_turn_spent_its_time(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            store.add(
                TurnTrace(
                    at="2026-10-01T05:00:00+00:00",
                    model="fast",
                    requested_model="smart",
                    route_source="llm",
                    route_ms=1800,
                    tier_source="heuristic",
                    tier_ms=0.4,
                    ttft_ms=2600,
                    total_ms=5000,
                )
            )
            store.add(TurnTrace(at="2026-10-01T05:01:00+00:00", kind="command", route_source="heuristic", tool_ms=320))
            chat, command = (json.loads(row) for row in store.timings_path.read_text(encoding="utf-8").splitlines())
        self.assertEqual((chat["tier"], chat["answered"]), ("smart", "fast"))
        self.assertEqual(
            (chat["route"], chat["route_ms"], chat["tier_source"], chat["tier_ms"]), ("llm", 1800, "heuristic", 0)
        )
        self.assertEqual((command["route"], command["tool_ms"], command["tier_ms"]), ("heuristic", 320, None))

    def test_a_voice_turn_logs_where_the_wait_went(self) -> None:
        # Seconds from the start of listening, as the page marks them.
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            self.assertTrue(store.log_voice({"speech": 0.4, "settle": 2.1, "stt": 3.05, "reply": 4.6, "speak": 5.9}))
            self.assertTrue(store.log_voice({"settle": 1.0, "reply": 2.5, "speak": 3.0}))  # the browser's recognizer
            first, second = (json.loads(row) for row in store.timings_path.read_text(encoding="utf-8").splitlines())
            hours = store.hourly()
        self.assertEqual(first["kind"], "voice")
        self.assertEqual(
            (first["stt_ms"], first["reply_ms"], first["speak_ms"], first["total_ms"]), (950, 1550, 1300, 3800)
        )
        self.assertEqual((second["stt_ms"], second["reply_ms"], second["total_ms"]), (None, 1500, 2000))
        self.assertEqual([(row["kind"], row["turns"]) for row in hours], [("voice", 2)])

    def test_voice_marks_that_cannot_be_a_turn_are_not_logged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            for marks in (
                {"settle": 2.0, "reply": 1.0, "speak": 3.0},  # out of order
                {"settle": 1.0, "reply": 2.0},  # never spoke
                {"settle": "1", "reply": 2.0, "speak": 3.0},
                {"settle": True, "reply": 2.0, "speak": 3.0},
                {"settle": -1.0, "reply": 2.0, "speak": 3.0},
                {"settle": 1.0, "reply": 2.0, "speak": 3.0e9},
                {"settle": float("nan"), "reply": 2.0, "speak": 3.0},
                "settle",
            ):
                with self.subTest(marks):
                    self.assertFalse(store.log_voice(marks))  # type: ignore[arg-type]
            self.assertFalse(store.timings_path.exists())

    def test_a_stage_time_that_is_not_a_whole_number_skips_the_line(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "traces.json"
            fields = '"kind": "chat", "tier": "", "ok": true, "degraded": false, "total_ms": 5, "ttft_ms": null'
            path.with_name("traces_timings.jsonl").write_text(
                '{"at": "2026-10-01T05:01:00+00:00", ' + fields + ', "route_ms": "slow"}\n'
                '{"at": "2026-10-01T05:02:00+00:00", ' + fields + "}\n",
                encoding="utf-8",
            )
            self.assertEqual(TraceStore(path).hourly()[0]["turns"], 1, "an older line without stages still counts")

    def test_a_line_that_cannot_be_read_is_skipped_rather_than_failing_a_turn(self) -> None:
        # A torn last write or a hand edit; the caller guards only OSError, so anything this
        # raised would end the user's turn.
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "traces.json"
            path.with_name("traces_timings.jsonl").write_text(
                '{"at": "2026-10-01T05:01:00+00:00", "kind": "chat", "tier": "", "ok": true, "degr\n'
                '{"at": "2026-10-01T05:02:00+00:00", "kind": "chat", "tier": "", "ok": "yes", '
                '"degraded": false, "total_ms": 5, "ttft_ms": null}\n',
                encoding="utf-8",
            )
            store = TraceStore(path)
            store.add(TurnTrace(at="2026-10-01T05:30:00+00:00", total_ms=40000))
            self.assertEqual(store.hourly()[0]["turns"], 1)

    def test_a_time_out_of_range_costs_one_line_not_the_turn(self) -> None:
        # Review of #157: these parse, then raise from astimezone - OverflowError, and OSError on
        # Windows - neither of them a ValueError, so one such line failed the first turn of
        # every day and every call to hourly().
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "traces.json"
            fields = '"kind": "chat", "tier": "", "ok": true, "degraded": false, "total_ms": 5, "ttft_ms": null'
            path.with_name("traces_timings.jsonl").write_text(
                '{"at": "9999-12-31T23:59:59-05:00", ' + fields + '}\n{"at": "1969-06-01T00:00:00", ' + fields + "}\n",
                encoding="utf-8",
            )
            store = TraceStore(path)
            store.add(TurnTrace(at="2026-10-01T05:30:00+00:00", total_ms=40000))
            self.assertEqual([row["turns"] for row in store.hourly()], [1])

    def test_bytes_that_are_not_utf8_cost_one_line_not_the_turn(self) -> None:
        # Codex's review of #157: the whole log was decoded before any line was checked, so
        # one bad byte raised UnicodeDecodeError, a ValueError the turn's OSError guard does
        # not catch, from the first prune after a restart.
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "traces.json"
            TraceStore(path).add(TurnTrace(at="2026-10-01T05:00:00+00:00", total_ms=100))
            with path.with_name("traces_timings.jsonl").open("ab") as log:
                log.write(b"\xff\n")
            reopened = TraceStore(path)
            reopened.add(TurnTrace(at="2026-10-01T05:30:00+00:00", total_ms=100))
            self.assertEqual(reopened.hourly()[0]["turns"], 2)

    def test_an_append_does_not_grow_with_the_history(self) -> None:
        # Rewriting a 90-day rollup on every turn measured 122ms at its worst: the history
        # is appended to, and only rewritten once a day to drop what has aged out.
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            store.add(TurnTrace(at="2026-10-01T05:00:00+00:00", total_ms=100))
            with patch("laptop_agent.tracing.atomic_write_text") as rewrite:
                store.add(TurnTrace(at="2026-10-01T05:01:00+00:00", total_ms=100))
            self.assertEqual([call.args[0] for call in rewrite.call_args_list], [store.path])
            self.assertEqual(store.hourly()[0]["turns"], 2)

    def test_a_turn_writes_the_ring_without_a_backup(self) -> None:
        # Keeping a backup re-read the whole ring and wrote it twice with two fsyncs on every turn:
        # 15 of an instant command's 17ms. Traces are diagnostics; a lost write loses one turn's timing.
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            for minute in range(3):
                store.add(TurnTrace(at=f"2026-10-01T05:0{minute}:00+00:00", total_ms=100))
            self.assertFalse((Path(raw) / "traces.json.bak").exists())
            self.assertEqual(len(TraceStore(Path(raw) / "traces.json").recent()), 3)


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
            {
                "kind",
                "verb",
                "route_source",
                "route_ms",
                "tool_ms",
                "ttft_ms",
                "total_ms",
                "model",
                "requested_model",
                "degraded",
                "ok",
                "at",
                "tier_source",
                "tier_ms",
            },
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


class VoiceTimingEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import laptop_agent.webui as webui

        cls.webui = webui
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, payload: object) -> dict:
        request = urllib.request.Request(
            self.base + "/api/voice-timing",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "X-Jarvis-Token": self.webui._API_TOKEN},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            return {"status": error.code}

    def test_the_page_marks_reach_the_timing_log(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = TraceStore(Path(raw) / "traces.json")
            with patch.object(self.webui._orchestrator, "traces", store):
                self.assertEqual(self.post({"marks": {"settle": 1.0, "reply": 2.0, "speak": 2.5}}), {"ok": True})
                self.assertEqual(self.post({"marks": {"settle": 3.0, "reply": 2.0, "speak": 2.5}}), {"ok": False})
                self.assertEqual(self.post({"marks": "settle"}), {"ok": False})
            self.assertEqual(len(store.timings_path.read_text(encoding="utf-8").splitlines()), 1)

    def test_a_personal_account_may_use_it(self) -> None:
        self.assertIn("/api/voice-timing", self.webui._PERSONAL_ROUTES)


if __name__ == "__main__":
    unittest.main()
