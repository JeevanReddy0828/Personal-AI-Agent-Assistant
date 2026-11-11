from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_patches import patch_agents

from laptop_agent import diagnosis
from laptop_agent.diagnosis import Probe, diagnose, run_probes
from laptop_agent.model_status import BROKEN, DEGRADED, ModelStatus
from laptop_agent.safety import ApprovalDenied
from laptop_agent.selfcheck import Check
from laptop_agent.tools.base import ToolResult


class Provider:
    def __init__(self, failure: tuple[str, str] | None = None, wait=None, raises: Exception | None = None) -> None:
        self.failure, self.wait, self.raises = failure, wait, raises

    def ping(self, on_failure=None) -> bool:
        if self.wait is not None:
            self.wait()
        if self.raises is not None:
            raise self.raises
        if self.failure is not None:
            on_failure(*self.failure)
            return False
        return True


class Search:
    def __init__(self, result: ToolResult | None = None, raises: Exception | None = None) -> None:
        self.result, self.raises, self.threads = result, raises, []

    def search(self, query: str, limit: int = 5) -> ToolResult:
        self.threads.append(threading.current_thread())
        if self.raises is not None:
            raise self.raises
        return self.result or ToolResult.success("Found 3 web result(s).")


class Traces:
    def __init__(self, **stats) -> None:
        self.stats = stats

    def summary(self) -> dict[str, object]:
        return dict(self.stats)


def tier(provider: Provider | None) -> SimpleNamespace | None:
    return None if provider is None else SimpleNamespace(provider=provider)


def agent(fast=None, smart=None, ultra=None, vision=None, backup=None, search=None, traces=None):
    return SimpleNamespace(planner=tier(fast), smart_planner=tier(smart), ultra_planner=tier(ultra),
                           vision_planner=tier(vision), fallback_planner=tier(backup), model_status=ModelStatus(),
                           traces=traces or Traces(turns=0), context=SimpleNamespace(websearch=search), router=None)


def row(key: str, name: str, state: str, detail: str = "", next_step: str | None = None) -> dict[str, object]:
    return {"key": key, "name": name, "state": state, "detail": detail, "next": next_step}


class Failures:
    def __init__(self, entries: list[dict[str, object]]) -> None:
        self.entries = entries

    def recent(self, limit: int = 40) -> list[dict[str, object]]:
        return self.entries[:limit]


class Quiet(unittest.TestCase):
    """Nothing caught, nothing unsaved and the routing contract whole, unless a test says so."""

    def setUp(self) -> None:
        for name, value in (("storage_warnings", lambda: []), ("FAILURES", Failures([])),
                            ("run_selfcheck", lambda router=None: ([Check("routes: x", True, "")], "All 1 checks pass."))):
            patcher = patch.object(diagnosis, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def probe(self, orchestrator, key: str) -> Probe:
        return next(probe for probe in run_probes(orchestrator) if probe.key == key)


class ProbeTests(Quiet):
    def test_a_broken_tier_is_a_problem_named_without_its_model_id(self) -> None:
        reason = "that model id has been retired by the provider (secret-vendor/model-x) - HTTP 410"
        orchestrator = agent(fast=Provider(), ultra=Provider((BROKEN, reason)), search=Search())
        report = diagnose(orchestrator, [])
        self.assertEqual(report.status, "fail")
        [problem] = report.problems
        self.assertEqual((problem.area, problem.fix), ("Deep model", "Check OPENAI_ULTRA_MODEL."))
        self.assertIn("HTTP 410", problem.detail)
        # The Setup panel's rule: the page and the chat never show a model id.
        self.assertNotIn("secret-vendor", report.markdown())
        self.assertNotIn("secret-vendor", json.dumps(report.as_data()))
        self.assertEqual(orchestrator.model_status.broken_tiers(), ["ultra"])

    def test_a_rejected_key_names_the_key_and_not_the_model(self) -> None:
        orchestrator = agent(backup=Provider((BROKEN, "the API key was rejected (m) - HTTP 401")))
        self.assertEqual(self.probe(orchestrator, "openrouter").fix, "Check OPENROUTER_API_KEY.")

    def test_an_answer_clears_a_tier_already_marked_broken(self) -> None:
        """A key fixed while the app runs is proved by the next diagnosis, not the next chat turn."""
        orchestrator = agent(fast=Provider())
        orchestrator.model_status.record("fast", False, reason=BROKEN, detail="the API key was rejected - HTTP 401")
        report = diagnose(orchestrator, [])
        self.assertEqual(orchestrator.model_status.status("fast"), "ok")
        self.assertTrue(report.working[0].startswith("Chat model ("), report.working)

    def test_a_busy_tier_is_a_warning_with_nothing_to_set_up(self) -> None:
        orchestrator = agent(smart=Provider((DEGRADED, "the model (m) returned HTTP 503")))
        report = diagnose(orchestrator, [])
        [warning] = report.warnings
        self.assertEqual((warning.area, warning.fix), ("Balanced model", None))
        self.assertIn("HTTP 503", warning.detail)
        self.assertEqual(orchestrator.model_status.status("smart"), DEGRADED)

    def test_an_unreachable_backup_names_its_own_address(self) -> None:
        orchestrator = agent(backup=Provider((DEGRADED, "could not reach the model (m): [Errno 11001] getaddrinfo")))
        probe = self.probe(orchestrator, "openrouter")
        self.assertEqual((probe.state, probe.fix), ("busy", "Check OPENROUTER_BASE_URL and the network."))
        self.assertNotIn("11001", probe.detail)

    def test_the_vision_tier_is_called_but_not_recorded(self) -> None:
        """model_status tracks the chat ladder only; a vision row there would show in the pill."""
        orchestrator = agent(vision=Provider((DEGRADED, "the model (m) did not answer in time")))
        self.assertEqual(self.probe(orchestrator, "vision").state, "busy")
        self.assertEqual(orchestrator.model_status.snapshot()["tiers"], {})

    def test_the_tiers_are_called_at_once(self) -> None:
        together = threading.Barrier(3, timeout=5)
        orchestrator = agent(fast=Provider(wait=together.wait), smart=Provider(wait=together.wait),
                             ultra=Provider(wait=together.wait))
        states = {probe.key: probe.state for probe in run_probes(orchestrator)}
        self.assertEqual(states, {"fast": "ok", "smart": "ok", "ultra": "ok"}, "the tiers were called one by one")

    def test_a_hung_tier_is_reported_slow_without_holding_the_report(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)
        orchestrator = agent(fast=Provider(), ultra=Provider(wait=release.wait))
        started = time.monotonic()
        probes = {probe.key: probe for probe in run_probes(orchestrator, deadline=0.3)}
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual((probes["fast"].state, probes["ultra"].state), ("ok", "slow"))

    def test_a_provider_that_raises_is_reported_and_recorded(self) -> None:
        with patch.object(diagnosis, "record_failure") as recorded:
            probe = self.probe(agent(fast=Provider(raises=RuntimeError("boom"))), "fast")
        self.assertEqual(probe.state, "busy")
        self.assertEqual(recorded.call_args.args[0], "diagnosis/fast")

    def test_the_built_in_rules_have_nothing_to_call(self) -> None:
        orchestrator = agent()
        orchestrator.planner = SimpleNamespace(provider=object())
        self.assertEqual(run_probes(orchestrator), [])

    def test_the_search_runs_on_the_calling_thread_and_a_failed_one_warns(self) -> None:
        """On this thread because it asks the approval gate, which reads the request's
        principal and cancellation from context."""
        search = Search(ToolResult.failure("No web results found."))
        report = diagnose(agent(search=search), [])
        self.assertEqual(search.threads, [threading.current_thread()])
        [warning] = report.warnings
        self.assertEqual(warning.area, "Web search")
        self.assertIn("SEARCH_API_KEY", warning.fix or "")

    def test_a_refused_search_is_skipped_not_reported(self) -> None:
        report = diagnose(agent(search=Search(raises=ApprovalDenied("no"))), [])
        self.assertEqual([probe.state for probe in report.probes], ["skipped"])
        self.assertEqual(report.findings, [])


class OfflineTests(Quiet):
    def test_setup_rows_are_ranked_by_what_they_need(self) -> None:
        rows = [row("docs", "Documents", "missing", "Word: no.", "pip install laptop-agent[docs]"),
                row("ocr", "Reading text in images", "broken", "Gone.", "Install Tesseract."),
                row("search", "Web search", "ready", "DuckDuckGo: a key but no provider.", "Set SEARCH_PROVIDER."),
                row("email", "Email", "off", "Not connected.", "Set IMAP_HOST."),
                row("youtube", "YouTube summaries", "ready", "Summarises.")]
        report = diagnose(agent(), rows)
        self.assertEqual([f.area for f in report.problems], ["Reading text in images"])
        self.assertEqual([f.area for f in report.warnings], ["Documents", "Web search"])
        self.assertEqual(report.optional, ["Email"])
        self.assertIn("YouTube summaries", report.working)

    def test_no_chat_model_is_a_warning_not_an_option(self) -> None:
        report = diagnose(agent(), [row("chat", "Chat model", "off", "Replies come from built-in rules only.", "Set it.")])
        self.assertEqual([f.detail for f in report.warnings],
                         ["No chat model is connected: replies come from built-in rules only."])

    def test_a_row_a_probe_answers_is_not_reported_twice(self) -> None:
        rows = [row("chat", "Chat model", "busy", "Busy at the moment."),
                row("deeper", "Deeper models", "busy", "Deep configured; one is busy.")]
        report = diagnose(agent(fast=Provider(), ultra=Provider((DEGRADED, "the model returned HTTP 429"))), rows)
        self.assertEqual([f.area for f in report.findings], ["Deep model"])

    def test_web_search_is_named_once_but_its_set_up_advice_is_kept(self) -> None:
        """Found live: the probe and the Setup row both put "Web search" under Working."""
        report = diagnose(agent(search=Search()), [row("search", "Web search", "ready", "DuckDuckGo, no key needed.")])
        self.assertEqual(len([name for name in report.working if name.startswith("Web search")]), 1, report.working)
        advice = [row("search", "Web search", "ready", "DuckDuckGo: a key but no provider.", "Set SEARCH_PROVIDER.")]
        self.assertEqual([f.fix for f in diagnose(agent(search=Search()), advice).warnings], ["Set SEARCH_PROVIDER."])

    def test_a_storage_warning_is_a_problem(self) -> None:
        with patch.object(diagnosis, "storage_warnings", lambda: ["Could not save reminders: disk full."]):
            report = diagnose(agent(), [])
        self.assertEqual([(f.area, f.detail) for f in report.problems], [("Storage", "Could not save reminders: disk full.")])
        self.assertNotIn("storage", report.working)

    def test_a_route_that_no_longer_reaches_its_tool_is_a_problem(self) -> None:
        broken = ([Check("routes: what time is it", False, "got nothing"), Check("routes: x", True, "")], "")
        with patch.object(diagnosis, "run_selfcheck", lambda router=None: broken):
            report = diagnose(agent(), [])
        [problem] = report.problems
        self.assertIn("1 of 2 phrasings", problem.detail)
        self.assertIn("'what time is it'", problem.detail)

    def test_only_the_last_hours_failures_count(self) -> None:
        entries = [{"where": "planner/route", "kind": "TimeoutError", "age_seconds": 30.0}] * 3 + [
            {"where": "tts/riva", "kind": "OSError", "age_seconds": 120.0},
            {"where": "old/thing", "kind": "ValueError", "age_seconds": 7200.0}]
        with patch.object(diagnosis, "FAILURES", Failures(entries)):
            [warning] = diagnose(agent(), []).warnings
        self.assertIn("4 in the last hour", warning.detail)
        self.assertIn("planner/route (TimeoutError) ×3", warning.detail)
        self.assertNotIn("old/thing", warning.detail)

    def test_slow_replies_and_failed_turns_are_warnings(self) -> None:
        traces = Traces(turns=20, chat_turns=12, median_ttft_ms=9200, llm_routed_pct=40.0, failed=2)
        report = diagnose(agent(traces=traces), [])
        self.assertEqual([f.area for f in report.warnings], ["Slow replies", "Model routing", "Failed turns"])
        self.assertIn("9.2 s", report.warnings[0].detail)
        self.assertNotIn("response times", report.working)

    def test_a_handful_of_turns_says_nothing_about_speed(self) -> None:
        report = diagnose(agent(traces=Traces(turns=3, chat_turns=3, median_ttft_ms=30000, failed=1)), [])
        self.assertEqual(report.findings, [])
        self.assertNotIn("response times", report.working)

    def test_all_well_says_so_and_lists_what_was_checked(self) -> None:
        traces = Traces(turns=10, chat_turns=6, median_ttft_ms=1200, llm_routed_pct=5.0, failed=0)
        report = diagnose(agent(fast=Provider(), search=Search(), traces=traces), [])
        self.assertEqual((report.status, report.verdict), ("ok", "Everything I checked is working."))
        self.assertEqual([name.split(" (")[0] for name in report.working],
                         ["Chat model", "Web search", "storage", "routing", "response times"])
        json.dumps(report.as_data())

    def test_problems_come_before_warnings_and_set_the_status(self) -> None:
        orchestrator = agent(fast=Provider((DEGRADED, "the model returned HTTP 503")),
                             smart=Provider((BROKEN, "the API key was rejected - HTTP 401")))
        report = diagnose(orchestrator, [])
        self.assertEqual([f.severity for f in report.findings], ["problem", "warning"])
        self.assertEqual((report.status, report.verdict), ("fail", "1 problem and 1 warning."))
        self.assertTrue(report.markdown().startswith("**Self-diagnosis: 1 problem and 1 warning.**"))


class CommandTests(Quiet):
    def build(self):
        from laptop_agent.app import build_orchestrator

        orchestrator = build_orchestrator(lambda request: True)
        orchestrator.setup_source = lambda: [row("ocr", "Reading text in images", "ready", "Tesseract.")]
        return orchestrator

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.object(diagnosis, "run_probes", lambda orchestrator, deadline=30.0: [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_command_answers_with_the_report(self) -> None:
        result = asyncio.run(self.build().handle("diagnose"))
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.message.startswith("**Self-diagnosis:"), result.message)
        self.assertIn("Reading text in images", result.data["working"])

    def test_a_problem_makes_the_command_itself_fail(self) -> None:
        orchestrator = self.build()
        orchestrator.setup_source = lambda: [row("ocr", "Reading text in images", "broken", "Gone.")]
        result = asyncio.run(orchestrator.handle("diagnose"))
        self.assertFalse(result.ok, "the command reported success while listing a problem")
        self.assertEqual(result.data["status"], "fail")

    def test_whats_wrong_with_you_reaches_the_diagnosis(self) -> None:
        result = asyncio.run(self.build().handle("what's wrong with you?"))
        self.assertIn("verdict", result.data, result.message)

    def test_selfcheck_is_still_the_routing_contract_alone(self) -> None:
        result = asyncio.run(self.build().handle("selfcheck"))
        self.assertIn("checks", result.data)
        self.assertNotIn("verdict", result.data)

    def test_without_the_web_server_the_rows_are_looked_up_here(self) -> None:
        orchestrator = self.build()
        orchestrator.setup_source = None
        rows = [row("docs", "Documents", "missing", "Word: no.")]
        with patch_agents(diagnosis.local_setup_rows, lambda orchestrator: rows):
            result = asyncio.run(orchestrator.handle("diagnose"))
        # Only the row: other tests' turns share the runner's data directory, so latency may add one.
        self.assertIn("Documents", [f["area"] for f in result.data["findings"]])

    def test_a_broken_engine_does_not_break_the_local_rows(self) -> None:
        with patch("laptop_agent.tools.transcribe.stt_engine_name", side_effect=OSError("no device")), \
                patch("laptop_agent.tools.transcribe.ocr_engine_name", return_value=None), \
                patch("laptop_agent.voice.tts_engine_name", return_value=None), \
                patch.object(diagnosis, "record_failure") as recorded:
            rows = diagnosis.local_setup_rows(self.build())
        self.assertEqual({r["key"]: r["state"] for r in rows}["stt"], "missing")
        self.assertEqual(recorded.call_args.args[0], "diagnosis/stt")


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer

        import laptop_agent.webui as webui

        cls.webui = webui
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        from laptop_agent.accounts import AccountStore
        from laptop_agent.sessions import SessionStore

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        accounts = AccountStore(Path(tmp.name) / "accounts.json", cost=(2 ** 10, 8, 1))
        sessions = SessionStore(Path(tmp.name) / "sessions.json")
        self.ran = MagicMock(return_value=ToolResult.failure("**Self-diagnosis: 1 problem.**", status="fail",
                                                              verdict="1 problem.", findings=[], probes=[]))
        for target, name, value in ((self.webui, "ACCOUNTS", accounts), (self.webui, "SESSIONS", sessions),
                                    (self.webui._orchestrator, "diagnose", self.ran)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cookies = {}
        for username, role in (("jeevan", "dev"), ("family", "personal")):
            account = accounts.create(username, role, "correct horse battery")
            self.cookies[role] = f"jarvis_session={sessions.create(account.id, 'password')}"

    def post(self, role: str):
        request = urllib.request.Request(self.base + "/api/diagnose", method="POST", data=b"{}",
                                         headers={"Content-Type": "application/json", "Cookie": self.cookies[role],
                                                  "X-Jarvis-Token": self.webui._API_TOKEN})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read()), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}"), dict(error.headers)

    def test_a_developer_gets_the_report(self):
        status, body, _ = self.post("dev")
        self.assertEqual(status, 200)
        # `ok` is whether it ran; whether everything works is the report's own `status`.
        self.assertEqual((body["ok"], body["status"], body["verdict"]), (True, "fail", "1 problem."))
        self.ran.assert_called_once_with()

    def test_a_personal_account_is_refused_before_anything_is_called(self):
        status, _, headers = self.post("personal")
        self.assertEqual((status, headers.get("X-Jarvis-Denied")), (403, "role"))
        self.ran.assert_not_called()

    def test_the_server_gives_the_orchestrator_its_setup_rows(self):
        self.assertIs(self.webui._orchestrator.setup_source, self.webui._setup_items)


if __name__ == "__main__":
    unittest.main()
