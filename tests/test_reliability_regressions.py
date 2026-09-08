from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from laptop_agent.cancellation import operation, cancel, check_cancelled, OperationCancelled
from laptop_agent.copilot import JobCopilot, render_resume_html
from laptop_agent.jobs import JobTracker
from laptop_agent.memory import MemoryStore
from laptop_agent.scheduler import SchedulerStore
from laptop_agent.storage import atomic_write_text, read_json, storage_warnings
from laptop_agent.agents.orchestrator import AgentOrchestrator
from laptop_agent.reasoning import AutonomousAgent
from laptop_agent.tools.base import ToolResult


class ReliabilityRegressions(unittest.TestCase):
    def test_concurrent_store_instances_preserve_all_updates(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "jobs.json"
            stores = [JobTracker(path) for _ in range(4)]
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: stores[i % 4].add(f"Company {i}"), range(32)))
            jobs = JobTracker(path).list()
            self.assertEqual(len(jobs), 32)
            self.assertEqual(len({job['id'] for job in jobs}), 32)
            memory = [MemoryStore(Path(raw) / "memory.json") for _ in range(2)]
            memory[0].set_profile_value("first", "one")
            memory[1].set_profile_value("second", "two")
            self.assertEqual(memory[0].get_profile(), {"first": "one", "second": "two"})

    def test_interrupted_write_keeps_previous_json(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "state.json"
            atomic_write_text(path, '{"value":1}')
            import os
            replace = os.replace
            def interrupted(src, dest):
                if Path(dest) == path:
                    raise OSError("simulated disk failure")
                return replace(src, dest)
            with patch("laptop_agent.storage.os.replace", side_effect=interrupted):
                with self.assertRaises(OSError):
                    atomic_write_text(path, '{"value":2}')
            self.assertEqual(read_json(path, {}), {"value": 1})
            self.assertFalse(list(Path(raw).glob("*.tmp")))

    def test_corrupt_file_recovers_backup_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "state.json"
            atomic_write_text(path, '{"value":1}')
            atomic_write_text(path, '{"value":2}')
            path.write_bytes(b'{broken')
            self.assertEqual(read_json(path, {}), {"value": 1})
            self.assertEqual(next(Path(raw).glob("*.corrupt-*")).read_bytes(), b'{broken')
            self.assertTrue(storage_warnings())
            path.write_text('{"profile":null,"notes":"wrong"}')
            memory = MemoryStore(path)
            memory.set_profile_value("name", "Test")
            memory.add_note("safe")
            self.assertEqual(memory.get_profile()["name"], "Test")

    def test_due_claim_is_exclusive_across_instances(self):
        with tempfile.TemporaryDirectory() as raw:
            now = datetime.now(UTC)
            path = Path(raw) / "schedules.json"
            first, second = SchedulerStore(path), SchedulerStore(path)
            job = first.add("command", "help", "hourly", now)
            with ThreadPoolExecutor(max_workers=2) as pool:
                claims = list(pool.map(lambda store: store.claim_due_jobs(now), [first, second]))
            self.assertEqual(sum(map(len, claims)), 1)
            first.mark_ran(job.id, now, "ok")
            self.assertEqual(second.claim_due_jobs(now), [])
            self.assertEqual(second.list_jobs()[0].run_count, 1)

    def test_cancel_before_start_and_before_approval(self):
        from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
        called = []
        cancel("early-stop")
        with self.assertRaises(OperationCancelled):
            with operation("early-stop"):
                called.append("unexpected")
        with operation("gate-stop"):
            cancel("gate-stop")
            with self.assertRaises(OperationCancelled):
                ApprovalGate(lambda request: called.append(request)).require(
                    ApprovalRequest("launch", RiskLevel.HIGH, "test"))
        self.assertEqual(called, [])

    def test_cancelled_agent_runs_no_later_tools(self):
        called = []
        async def execute(command):
            called.append(command)
            cancel("agent-stop")
            return ToolResult.success("done first step")
        agent = AutonomousAgent(lambda prompt: "ACTION: help", execute)
        with operation("agent-stop"):
            with self.assertRaises(OperationCancelled):
                asyncio.run(agent.run("test"))
        self.assertEqual(called, ["help"])

    def test_mid_stream_error_resets_and_allows_fallback(self):
        class BrokenProvider:
            def stream_answer(self, *args):
                yield "partial"
                raise TimeoutError("interrupted")
        tokens = []
        def token(text):
            tokens.append(text)
        token.reset = tokens.clear
        reply = AgentOrchestrator._tier_reply(BrokenProvider(), "hi", {}, [], token)
        self.assertEqual(reply, "")
        self.assertEqual(tokens, [])

    def test_fabricated_resume_and_malformed_nested_values_are_rejected(self):
        source = "Candidate\nAcme Engineer 2024 Remote\n- Built Python APIs."
        content = {"experiences": [{"company": "Acme", "title": "CEO", "dates": "2024",
                                    "bullets": ["Built Python APIs worth 9 billion dollars."]}]}
        result = JobCopilot(lambda _: json.dumps(content)).tailor_resume(source, "Python")
        self.assertFalse(result.ok)
        self.assertIn("CEO", result.package)
        self.assertIn("9 billion", result.package)
        for malformed in (None, "wrong", ["wrong"], [{"company": ["Acme"]}]):
            content["experiences"] = malformed
            self.assertFalse(JobCopilot(lambda _: json.dumps(content)).tailor_resume(source, "Python").ok)

    def test_multi_runs_blocking_tools_concurrently(self):
        import laptop_agent.webui as webui
        barrier = threading.Barrier(2)
        async def blocking(command, **kwargs):
            barrier.wait(timeout=3)
            return ToolResult.success(command)
        with patch.object(webui._orchestrator, "handle", blocking):
            result = asyncio.run(webui._orchestrator._run_many("help ;; memory"))
        self.assertTrue(result.ok, result.message)
        self.assertEqual(len(result.data["results"]), 2)

    def test_job_application_date_and_stale_resume_invalidation(self):
        with tempfile.TemporaryDirectory() as raw:
            tracker = JobTracker(Path(raw) / "jobs.json")
            job = tracker.add("Acme", stage="lead")
            self.assertIsNone(job["applied_at"])
            self.assertEqual(tracker.stats()["by_week"], [])
            tracker.update(job["id"], stage="interview")
            self.assertTrue(tracker.get(job["id"])["applied_at"])
            tracker.update(job["id"], stage="rejected")
            self.assertEqual(tracker.stats()["interviews"], 1)
            self.assertEqual(tracker.stats()["response_rate"], 1)
            tracker.set_tailoring(job["id"], package="html", used_llm=True)
            tracker.set_tailored_pdf(job["id"], "old.pdf")
            tracker.set_resume("Updated base")
            self.assertFalse(tracker.get(job["id"])["tailored"])
            self.assertNotIn("tailored_pdf", tracker.get(job["id"]))

    def test_export_cannot_attach_to_a_newer_resume(self):
        with tempfile.TemporaryDirectory() as raw:
            tracker = JobTracker(Path(raw) / "jobs.json")
            job = tracker.add("Acme")
            base = tracker.set_resume("original")
            tracker.set_resume_profile({"contact_links": "changed@example.com"})
            self.assertIsNone(tracker.set_tailoring(job["id"], package="old", used_llm=True, expected_resume=base["updated_at"]))
            tracker.set_tailoring(job["id"], package="new", used_llm=True)
            self.assertIsNone(tracker.set_tailored_pdf(job["id"], "old.pdf", expected_package="old"))
            self.assertNotIn("tailored_pdf", tracker.get(job["id"]))

    def test_naive_reminder_uses_local_time_and_explicit_offset_wins(self):
        from datetime import timezone, timedelta
        from laptop_agent.reminders import ReminderStore
        local = timezone(timedelta(hours=-5))
        class LocalClock(datetime):
            def astimezone(self, tz=None):
                value = self if self.tzinfo else self.replace(tzinfo=local)
                return datetime.astimezone(value, tz or local)
        with patch("laptop_agent.reminders.datetime", LocalClock):
            self.assertEqual(ReminderStore._parse_due_at("2030-01-01 09:00").hour, 14)
            self.assertEqual(ReminderStore._parse_due_at("2030-01-01T09:00+02:00").hour, 7)

    def test_native_webview_uses_persistent_profile(self):
        import laptop_agent.webui as webui
        from types import SimpleNamespace
        from unittest.mock import Mock
        backend = SimpleNamespace(create_window=Mock(), start=Mock())
        with patch.dict("sys.modules", {"webview": backend}):
            self.assertTrue(webui._launch_webview("http://127.0.0.1:8770"))
        kwargs = backend.start.call_args.kwargs
        self.assertFalse(kwargs["private_mode"])
        self.assertEqual(Path(kwargs["storage_path"]), webui._CONFIG.data_dir / "webview")

    def test_contacts_and_certifications_cannot_inject_active_html(self):
        result = render_resume_html("Name", '<a href="javascript:alert(1)">link</a><img src=x onerror=alert(1)>', '<script>bad()</script>', {})
        self.assertNotIn('href="javascript:', result)
        self.assertNotIn('<img', result)
        self.assertNotIn('<script', result)
