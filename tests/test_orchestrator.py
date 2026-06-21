from __future__ import annotations

import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path

from laptop_agent.agents.orchestrator import AgentContext, AgentOrchestrator
from laptop_agent.audit import AuditLogger
from laptop_agent.config import AppConfig
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.planner import HeuristicPlannerProvider, PlanDecision, Planner
from laptop_agent.reminders import ReminderStore
from laptop_agent.safety import ApprovalGate
from laptop_agent.tasks import TaskTracker
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.obsidian import ObsidianVault
from laptop_agent.autopilot import AutopilotTracker
from laptop_agent.reasoning import AgentRunTracker
from laptop_agent.scheduler import SchedulerStore
from laptop_agent.tools.research import ResearchTool
from laptop_agent.tools.terminal import TerminalTool
from laptop_agent.tools.transcribe import TranscribeTool
from laptop_agent.tools.web import WebTool
from laptop_agent.tools.webcam import WebcamTool
from laptop_agent.tools.websearch import WebSearchTool
from laptop_agent.workflows import WorkflowTracker


class OrchestratorTests(unittest.TestCase):
    def build(self, tmp: Path) -> AgentOrchestrator:
        gate = ApprovalGate(lambda request: True)
        config = AppConfig(
            data_dir=tmp,
            memory_path=tmp / "memory.json",
            audit_log_path=tmp / "audit.jsonl",
            token_vault_path=tmp / "email_tokens.json",
            downloads_dir=tmp / "downloads",
            smtp_host=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_from=None,
            imap_host=None,
            imap_port=993,
            imap_username=None,
            imap_password=None,
            imap_mailbox="INBOX",
            google_client_id=None,
            google_client_secret=None,
            google_redirect_uri="http://localhost:8765/oauth/callback",
            microsoft_client_id=None,
            microsoft_client_secret=None,
            microsoft_tenant="common",
            microsoft_redirect_uri="http://localhost:8765/oauth/callback",
            llm_provider="heuristic",
            llm_base_url="https://api.openai.com/v1",
            llm_model=None,
            llm_smart_model=None,
            llm_ultra_model=None,
            llm_vision_model=None,
            llm_api_key=None,
            obsidian_vault=str(tmp / "vault"),
        )
        desktop = DesktopTool(gate, screenshot_backend=lambda path: path.write_bytes(b"\x89PNG\r\n"))
        web = WebTool(gate, config.downloads_dir)
        return AgentOrchestrator(
            AgentContext(
                memory=MemoryStore(config.memory_path),
                files=FileTool(gate),
                web=web,
                websearch=WebSearchTool(
                    gate,
                    search_backend=lambda query, limit: [
                        {"title": f"Result for {query}", "url": "https://example.com/1", "snippet": "snippet one"},
                        {"title": "Second", "url": "https://example.com/2", "snippet": "snippet two"},
                    ][:limit],
                ),
                browser=BrowserAutomationTool(gate),
                desktop=desktop,
                email=EmailTool(gate, config),
                music=MusicTool(gate, desktop, web),
                research=ResearchTool(
                    gate,
                    search_backend=lambda query, limit: [
                        {"title": f"{query} overview", "url": "https://example.com/a", "snippet": "intro"},
                        {"title": f"{query} details", "url": "https://example.com/b", "snippet": "more"},
                    ][:limit],
                    fetch_backend=lambda url: (
                        f"Detailed page body for {url}. It explains the subject thoroughly. "
                        "The topic matters because it improves understanding. Many readers find it useful."
                    ),
                ),
                terminal=TerminalTool(
                    gate,
                    runner=lambda command, cwd, timeout: subprocess.CompletedProcess(
                        command,
                        0,
                        stdout=f"ran::{command}::in::{cwd.name}",
                        stderr="",
                    ),
                ),
                transcribe=TranscribeTool(
                    ocr_backend=lambda path: f"ocr-text::{path.name}",
                    asr_backend=lambda path: {"text": f"transcript::{path.name}", "engine": "fake", "segments": []},
                ),
                webcam=WebcamTool(capture_backend=lambda device, dest: (dest.write_bytes(b"\x89PNG-fake"), dest)[1]),
                audit=AuditLogger(config.audit_log_path),
                tasks=TaskTracker(),
                workflows=WorkflowTracker(config.data_dir / "workflows.json"),
                reminders=ReminderStore(config.data_dir / "reminders.json"),
                knowledge=KnowledgeBase(config.data_dir / "knowledge.json"),
                obsidian=ObsidianVault(config.obsidian_vault),
                autopilot=AutopilotTracker(config.data_dir / "autopilot.json"),
                agent_runs=AgentRunTracker(config.data_dir / "agent_runs.json"),
                scheduler=SchedulerStore(config.data_dir / "scheduler.json"),
            ),
            Planner(HeuristicPlannerProvider()),
        )

    def test_remember_and_memory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("remember name = Ada"))
            self.assertTrue(result.ok)
            memory = asyncio.run(orchestrator.handle("memory"))
            self.assertEqual(memory.data["memory"]["profile"]["name"], "Ada")

    def test_search_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "note.md").write_text("hello agent\n", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"search files agent {root}"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["matches"][0]["line"], 1)

    def test_summarize_file_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "doc.txt"
            doc.write_text(
                "The agent reads files. The agent summarizes files offline. "
                "Offline summaries protect privacy for the user.",
                encoding="utf-8",
            )
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"summarize file {doc}"))
            self.assertTrue(result.ok)
            self.assertTrue(result.data["summary"])

    def test_convert_file_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            src = root / "in.md"
            dst = root / "out.txt"
            src.write_text("hello", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"convert file {src} to {dst}"))
            self.assertTrue(result.ok)
            self.assertTrue(dst.exists())

    def test_organize_folder_preview_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a.pdf").write_text("x", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"organize folder {root}"))
            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["planned"]), 1)
            self.assertTrue((root / "a.pdf").exists())

    def test_ocr_image_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "scan.png"
            image.write_bytes(b"\x89PNG\r\n")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"ocr image {image}"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["text"], "ocr-text::scan.png")

    def test_transcribe_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            clip = root / "memo.mp3"
            clip.write_bytes(b"ID3")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"transcribe {clip}"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["text"], "transcript::memo.mp3")
            self.assertEqual(result.data["kind"], "audio")

    def test_transcribe_rejects_non_media(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "notes.txt"
            doc.write_text("hello", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"transcribe {doc}"))
            self.assertFalse(result.ok)

    def test_summarize_image_uses_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "poster.png"
            image.write_bytes(b"\x89PNG")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"summarize file {image}"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["extracted_from"], "ocr")
            self.assertTrue(result.data["summary"])

    def test_extract_text_unified_for_image(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "card.jpg"
            image.write_bytes(b"\xff\xd8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"extract text {image}"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["text"], "ocr-text::card.jpg")

    def test_extract_text_unified_for_document(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "doc.txt"
            doc.write_text("plain content", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"extract text {doc}"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["text"], "plain content")

    def test_read_screen_uses_vision_when_available(self) -> None:
        from laptop_agent.planner.core import Planner as _Planner

        class FakeVision:
            def describe_image(self, path, prompt, model=None):
                return "I see a code editor with a Python file open."

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            orchestrator.vision_planner = _Planner(FakeVision())
            result = asyncio.run(orchestrator.handle("look at my screen and tell me"))
            self.assertTrue(result.ok)
            self.assertIn("code editor", result.message)
            self.assertTrue(result.data.get("vision"))

    def test_describe_image_uses_vision(self) -> None:
        from laptop_agent.planner.core import Planner as _Planner

        class FakeVision:
            def describe_image(self, path, prompt, model=None):
                return "A photo of a receipt."

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            img = root / "pic.png"
            img.write_bytes(b"\x89PNG")
            orchestrator = self.build(root / "d")
            orchestrator.vision_planner = _Planner(FakeVision())
            result = asyncio.run(orchestrator.handle(f"describe image {img}"))
            self.assertTrue(result.ok)
            self.assertEqual(result.message, "A photo of a receipt.")

    def test_read_screen_captures_and_ocrs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("read screen"))
            self.assertTrue(result.ok)
            self.assertIn("ocr-text::", result.data["text"])
            self.assertTrue(result.data["screenshot"])

    def test_multi_records_task_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("multi help ;; memory ;; read file missing.txt"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["dashboard"]["task_count"], 3)
            self.assertTrue(result.data["dashboard"]["retry_available"])
            dash = asyncio.run(orchestrator.handle("tasks"))
            self.assertTrue(dash.ok)
            self.assertIn("multi retry failed", dash.message)
            self.assertEqual(dash.data["dashboard"]["run"], 1)
            self.assertEqual(dash.data["dashboard"]["task_count"], 3)

    def test_multi_retry_failed_without_run_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("multi retry failed"))
            self.assertFalse(result.ok)
            self.assertIn("No task run", result.message)

    def test_multi_retry_failed_reruns_failed_commands(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            first = asyncio.run(orchestrator.handle("multi help ;; read file missing.txt"))
            self.assertTrue(first.ok)
            self.assertEqual(first.data["dashboard"]["failed_commands"], ["read file missing.txt"])
            retry = asyncio.run(orchestrator.handle("multi retry failed"))
            self.assertTrue(retry.ok)
            self.assertEqual(retry.data["dashboard"]["retry_of"], 1)
            self.assertEqual(retry.data["dashboard"]["task_count"], 1)
            self.assertEqual(retry.data["results"][0]["command"], "read file missing.txt")

    def test_workflow_runs_steps_sequentially(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("workflow memory ;; tasks"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["workflow"]["step_count"], 2)
            self.assertEqual(result.data["workflow"]["failed_count"], 0)
            status = asyncio.run(orchestrator.handle("workflow status"))
            self.assertTrue(status.ok)
            self.assertEqual(status.data["workflow"]["run"], 1)

    def test_workflow_stops_on_failure_and_retries(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            missing = root / "missing.txt"
            existing = root / "ok.txt"
            existing.write_text("hello", encoding="utf-8")
            orchestrator = self.build(root / "data")
            first = asyncio.run(orchestrator.handle(f"workflow read file {missing} ;; read file {existing}"))
            self.assertFalse(first.ok)
            self.assertEqual(first.data["workflow"]["stopped_at"], 0)
            self.assertEqual(len(first.data["results"]), 1)

            missing.write_text("now exists", encoding="utf-8")
            retry = asyncio.run(orchestrator.handle("workflow retry failed"))
            self.assertTrue(retry.ok)
            self.assertEqual(retry.data["workflow"]["step_count"], 2)

    def test_summarize_auto_indexes_for_recall(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "spec.txt"
            doc.write_text(
                "The Helios payment gateway processes transactions through a sharded ledger. "
                "It guarantees idempotency with request keys and reconciles nightly against the bank feed. "
                "Latency targets are under fifty milliseconds at the ninety-ninth percentile for authorizations.",
                encoding="utf-8",
            )
            orchestrator = self.build(root / "data")
            summary = asyncio.run(orchestrator.handle(f"summarize file {doc}"))
            self.assertTrue(summary.ok)
            self.assertTrue(summary.data.get("indexed"))
            recalled = asyncio.run(orchestrator.handle("recall Helios payment gateway"))
            self.assertTrue(recalled.data["results"])

    def test_index_and_recall_document(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "report.txt"
            doc.write_text("The migration plan covers database sharding and rollback safety.", encoding="utf-8")
            orchestrator = self.build(root / "data")
            indexed = asyncio.run(orchestrator.handle(f"index file {doc}"))
            self.assertTrue(indexed.ok)
            recalled = asyncio.run(orchestrator.handle("recall database sharding"))
            self.assertTrue(recalled.ok)
            self.assertTrue(recalled.data["results"])
            self.assertIn("sharding", recalled.data["results"][0]["snippet"].lower())

    def test_ask_file_answers_from_document(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "runbook.txt"
            doc.write_text(
                "Payment retries use idempotency keys. "
                "Daily reconciliation compares ledger entries with bank feeds.",
                encoding="utf-8",
            )
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"ask file {doc} about retries"))
            self.assertTrue(result.ok)
            self.assertIn("idempotency", result.data["answer"])

    def test_ask_knowledge_answers_from_index(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "runbook.txt"
            doc.write_text(
                "Payment retries use idempotency keys. "
                "Daily reconciliation compares ledger entries with bank feeds.",
                encoding="utf-8",
            )
            orchestrator = self.build(root / "data")
            asyncio.run(orchestrator.handle(f"index file {doc}"))
            result = asyncio.run(orchestrator.handle("ask knowledge payment retries"))
            self.assertTrue(result.ok)
            self.assertIn("idempotency", result.data["answer"])
            self.assertEqual(result.data["sources"], [str(doc)])

    def test_natural_language_ask_file_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "runbook.txt"
            doc.write_text("Payment retries use idempotency keys.", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"what does {doc} say about retries"))
            self.assertTrue(result.ok)
            self.assertIn("planner", result.data)
            self.assertIn("idempotency", result.message)

    def test_index_image_uses_ocr_then_recall(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "card.png"
            image.write_bytes(b"\x89PNG")
            orchestrator = self.build(root / "data")
            asyncio.run(orchestrator.handle(f"index file {image}"))
            recalled = asyncio.run(orchestrator.handle("recall card"))
            self.assertTrue(recalled.ok)
            self.assertTrue(recalled.data["results"])

    def test_knowledge_list_and_forget(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "a.txt"
            doc.write_text("indexable content here", encoding="utf-8")
            orchestrator = self.build(root / "data")
            asyncio.run(orchestrator.handle(f"index file {doc}"))
            listed = asyncio.run(orchestrator.handle("knowledge list"))
            self.assertEqual(len(listed.data["documents"]), 1)
            doc_id = listed.data["documents"][0]["id"]
            forgotten = asyncio.run(orchestrator.handle(f"knowledge forget {doc_id}"))
            self.assertTrue(forgotten.data["removed"])

    def test_knowledge_stats_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "a.txt"
            export_path = root / "knowledge.md"
            doc.write_text("indexable content about local memory", encoding="utf-8")
            orchestrator = self.build(root / "data")
            asyncio.run(orchestrator.handle(f"index file {doc}"))
            stats = asyncio.run(orchestrator.handle("knowledge stats"))
            self.assertTrue(stats.ok)
            self.assertEqual(stats.data["stats"]["document_count"], 1)
            exported = asyncio.run(orchestrator.handle(f"knowledge export {export_path}"))
            self.assertTrue(exported.ok)
            self.assertTrue(export_path.exists())
            self.assertIn("a.txt", export_path.read_text(encoding="utf-8"))

    def test_natural_language_knowledge_export_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "a.txt"
            export_path = root / "knowledge.md"
            doc.write_text("indexable content about local memory", encoding="utf-8")
            orchestrator = self.build(root / "data")
            asyncio.run(orchestrator.handle(f"index file {doc}"))
            result = asyncio.run(orchestrator.handle(f"export knowledge to {export_path}"))
            self.assertTrue(result.ok)
            self.assertIn("planner", result.data)
            self.assertTrue(export_path.exists())

    def test_research_command_summarizes_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("research distributed systems"))
            self.assertTrue(result.ok)
            self.assertTrue(result.data["summary"])
            self.assertEqual(len(result.data["sources"]), 2)
            self.assertTrue(result.data["indexed"]["ok"])
            recalled = asyncio.run(orchestrator.handle("recall distributed"))
            self.assertTrue(recalled.data["results"])

    def test_research_report_command_returns_markdown_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("research report distributed systems"))
            self.assertTrue(result.ok)
            self.assertIn("# Research Report: distributed systems", result.data["report"])
            self.assertTrue(result.data["indexed"]["ok"])
            recalled = asyncio.run(orchestrator.handle("recall research report distributed systems"))
            self.assertTrue(recalled.data["results"])

    def test_save_research_report_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            orchestrator = self.build(root / "data")
            dest = root / "distributed-report.md"
            result = asyncio.run(orchestrator.handle(f"save research report distributed systems to {dest}"))
            self.assertTrue(result.ok)
            self.assertTrue(dest.exists())
            self.assertIn("# Research Report: distributed systems", dest.read_text(encoding="utf-8"))
            self.assertEqual(result.data["saved"]["destination"], str(dest.resolve()))

    def test_save_research_report_to_obsidian(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "data" / "vault").mkdir(parents=True)
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle("save research report distributed systems to obsidian"))
            self.assertTrue(result.ok)
            self.assertEqual(
                result.data["saved"]["rel"].replace("\\", "/"),
                "Research Reports/Research Report - distributed systems.md",
            )

    def test_natural_language_research_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("look into quantum computing"))
            self.assertTrue(result.ok)
            self.assertIn("planner", result.data)
            self.assertEqual(result.data["topic"], "quantum computing")

    def test_web_search_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("web search python asyncio"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["results"][0]["url"], "https://example.com/1")
            self.assertEqual(result.data["query"], "python asyncio")

    def test_natural_language_web_search_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("search the web for tide times"))
            self.assertTrue(result.ok)
            self.assertIn("planner", result.data)
            self.assertEqual(result.data["query"], "tide times")

    def test_tasks_without_runs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("tasks"))
            self.assertTrue(result.ok)
            self.assertIsNone(result.data["dashboard"])

    def test_reminder_flow(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            added = asyncio.run(orchestrator.handle("reminder add 2026-06-10 09:00 Call Alex"))
            self.assertTrue(added.ok)
            self.assertEqual(added.data["reminder"]["message"], "Call Alex")
            due = asyncio.run(orchestrator.handle("reminders due"))
            self.assertTrue(due.ok)
            self.assertEqual(due.data["reminders"][0]["message"], "Call Alex")
            done = asyncio.run(orchestrator.handle("reminder done 1"))
            self.assertTrue(done.data["completed"])
            due_again = asyncio.run(orchestrator.handle("reminders due"))
            self.assertEqual(due_again.data["reminders"], [])

    def test_run_command_returns_terminal_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("run command echo hello"))
            self.assertTrue(result.ok)
            self.assertIn("ran::echo hello", result.data["stdout"])

    def test_run_command_with_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            work = root / "work"
            work.mkdir()
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"run command in {work} :: echo hello"))
            self.assertTrue(result.ok)
            self.assertIn("::in::work", result.data["stdout"])

    def test_briefing_combines_local_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            asyncio.run(orchestrator.handle("reminder add 2026-06-10 09:00 Call Alex"))
            result = asyncio.run(orchestrator.handle("briefing"))
            self.assertTrue(result.ok)
            self.assertIn("Briefing", result.message)
            self.assertEqual(result.data["due_reminders"][0]["message"], "Call Alex")
            self.assertIn("knowledge", result.data)
            self.assertIn("metrics", result.data)

    def test_autopilot_runs_safe_plan(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("autopilot daily briefing"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["autopilot"]["status"], "ok")
            self.assertGreaterEqual(result.data["autopilot"]["ok_count"], 1)
            status = asyncio.run(orchestrator.handle("autopilot status"))
            self.assertTrue(status.ok)
            self.assertEqual(status.data["autopilot"]["run"], 1)

    def test_autopilot_blocks_supervised_steps(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("autopilot workflow briefing ;; run command echo hello"))
            self.assertFalse(result.ok)
            self.assertEqual(result.data["autopilot"]["blocked_count"], 1)
            blocked = [step for step in result.data["autopilot"]["steps"] if step["status"] == "blocked"]
            self.assertEqual(blocked[0]["command"], "run command echo hello")

    def test_autonomous_agent_runs_to_completion(self) -> None:
        class ScriptedProvider:
            def __init__(self):
                self.calls = 0

            def answer(self, text, memory_profile, model=None, history=None):
                self.calls += 1
                if self.calls == 1:
                    return "THOUGHT: check what I know\nACTION: knowledge stats"
                return "THOUGHT: done\nFINAL: Checked the knowledge base; it is ready."

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            orchestrator.smart_planner = Planner(ScriptedProvider())
            result = asyncio.run(orchestrator.handle("agent run check my knowledge base"))
            self.assertTrue(result.ok)
            self.assertIn("knowledge base", result.message)
            self.assertEqual(result.data["agent"]["status"], "ok")
            self.assertEqual(result.data["agent"]["ok_count"], 1)
            self.assertEqual(result.data["steps"][0]["command"], "knowledge stats")

            last = asyncio.run(orchestrator.handle("agent last"))
            self.assertTrue(last.ok)
            self.assertEqual(last.data["agent"]["run"], 1)

    def test_autonomous_agent_without_llm_fails_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))  # heuristic planner only — no answer()
            result = asyncio.run(orchestrator.handle("agent run do something"))
            self.assertFalse(result.ok)
            self.assertIn("reasoning model", result.message.lower())

    def test_needs_fresh_info_detection(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            o = self.build(Path(raw))
            for q in [
                "did the iran war end?",
                "what's the latest on the election",
                "who won the 2026 finals",
                "is the bridge still closed",
                "update on the ceasefire",
            ]:
                self.assertTrue(o._needs_fresh_info(q), q)
            for q in ["what is 2 + 2", "write a poem about the sea", "define entropy"]:
                self.assertFalse(o._needs_fresh_info(q), q)

    def test_fresh_question_answers_from_web(self) -> None:
        class DualProvider:
            def __init__(self):
                self.saw_web = False

            def answer(self, text, memory_profile, model=None, history=None):
                if "WEB SEARCH RESULTS" in text:
                    self.saw_web = True
                    return "As of today, a ceasefire holds. [1]"
                return "(generic non-web reply)"

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            provider = DualProvider()
            orchestrator.smart_planner = Planner(provider)
            result = asyncio.run(orchestrator.handle("did the iran war end?"))
            self.assertTrue(result.ok)
            self.assertTrue(result.data.get("grounded"))
            self.assertTrue(provider.saw_web)
            self.assertEqual(len(result.data["sources"]), 2)
            self.assertIn("Sources", result.message)
            self.assertIn("https://example.com/1", result.message)

    def test_non_fresh_question_skips_web(self) -> None:
        class DualProvider:
            def __init__(self):
                self.saw_web = False

            def answer(self, text, memory_profile, model=None, history=None):
                if "WEB SEARCH RESULTS" in text:
                    self.saw_web = True
                return "a calm poem about waves"

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            provider = DualProvider()
            orchestrator.smart_planner = Planner(provider)
            result = asyncio.run(orchestrator.handle("write a poem about the sea"))
            self.assertTrue(result.ok)
            self.assertFalse(provider.saw_web)
            self.assertNotIn("grounded", result.data)

    def test_fresh_question_warns_when_no_llm_to_ground(self) -> None:
        # Heuristic-only build (no answer()): a time-sensitive question can't be grounded,
        # so the reply should carry an honest "may be out of date" warning rather than
        # sound confident.
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("what is the latest news on the election"))
            self.assertTrue(result.ok)
            self.assertTrue(result.data.get("stale_warning"))
            self.assertIn("out of date", result.message.lower())

    def test_schedule_add_list_and_run_due(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            added = asyncio.run(orchestrator.handle("schedule every 30 minutes :: briefing"))
            self.assertTrue(added.ok)
            self.assertEqual(added.data["job"]["kind"], "command")
            self.assertEqual(added.data["job"]["spec"], "briefing")

            listed = asyncio.run(orchestrator.handle("schedule list"))
            self.assertEqual(len(listed.data["jobs"]), 1)

            # A brand-new interval job is immediately due, so run-due should fire it once.
            ran = asyncio.run(orchestrator.handle("schedule run due"))
            self.assertTrue(ran.ok)
            self.assertEqual(len(ran.data["ran"]), 1)
            self.assertEqual(ran.data["ran"][0]["status"], "ok")

            removed = asyncio.run(orchestrator.handle("schedule remove 1"))
            self.assertTrue(removed.ok)
            self.assertEqual(asyncio.run(orchestrator.handle("schedule list")).data["jobs"], [])

    def test_schedule_rejects_bad_expression(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            bad = asyncio.run(orchestrator.handle("schedule briefing"))  # missing '::'
            self.assertFalse(bad.ok)
            self.assertIn("::", bad.message)

    def test_webcam_capture_falls_back_to_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))  # no vision planner; fake OCR returns text
            result = asyncio.run(orchestrator.handle("look at webcam"))
            # The injected webcam backend writes a frame; with no vision model the orchestrator
            # falls back to OCR, which (in tests) returns text — so the run succeeds with the frame.
            self.assertTrue(result.ok)
            self.assertIn("webcam", result.data)
            self.assertIn("ocr-text", result.data.get("text", ""))

    def test_natural_language_reminder_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("remind me to call Alex at 2026-06-20 09:00"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["reminder"]["message"], "call Alex")

    def test_routed_command_is_humanized_locally(self) -> None:
        from laptop_agent.planner.core import PlanDecision

        class RoutingProvider:
            def plan(self, text, available_commands, memory_profile):
                return PlanDecision(action="command", command="memory", confidence=0.9, explanation="route")

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            asyncio.run(orchestrator.handle("remember my name is Ada"))
            orchestrator.planner = Planner(RoutingProvider())
            result = asyncio.run(orchestrator.handle("what do you remember about me"))
            self.assertIn("Ada", result.message)
            self.assertIn("Here's what I remember", result.message)
            self.assertIn("planner", result.data)

    def test_email_digest_summarizes_via_llm(self) -> None:
        from laptop_agent.planner.core import PlanDecision, Planner
        from laptop_agent.tools.base import ToolResult

        class FakeEmail:
            def search_inbox(self, query="UNSEEN", limit=10):
                return ToolResult.success(
                    "Found 2 email message(s).",
                    messages=[
                        {"from": "LinkedIn", "subject": "Job at IBM", "snippet": "match"},
                        {"from": "Google", "subject": "Security alert", "snippet": "sign-in"},
                    ],
                )

        class DigestProvider:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.5, explanation="x", response="ok")

            def answer(self, text, memory_profile, model=None, history=None):
                return "You have 2 unread: 1 job alert, 1 security alert."

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            object.__setattr__(orchestrator.context, "email", FakeEmail())
            orchestrator.planner = Planner(DigestProvider())
            result = asyncio.run(orchestrator.handle("email digest"))
            self.assertTrue(result.ok)
            self.assertIn("job alert", result.message)
            self.assertTrue(result.data.get("digest"))

    def test_email_digest_empty_inbox(self) -> None:
        from laptop_agent.tools.base import ToolResult

        class EmptyEmail:
            def search_inbox(self, query="UNSEEN", limit=10):
                return ToolResult.success("Found 0 email message(s).", messages=[])

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            object.__setattr__(orchestrator.context, "email", EmptyEmail())
            result = asyncio.run(orchestrator.handle("summarize my inbox"))
            self.assertTrue(result.ok)
            self.assertIn("caught up", result.message)

    def test_humanize_formats_email_messages(self) -> None:
        from laptop_agent.tools.base import ToolResult

        result = ToolResult.success(
            "Found 2 email message(s).",
            messages=[
                {"from": "LinkedIn <jobs@linkedin.com>", "subject": "Software Developer at IBM", "snippet": "Jobs that match you"},
                {"from": "Google <no-reply@google.com>", "subject": "Security alert", "snippet": "New sign-in"},
            ],
        )
        human = AgentOrchestrator._humanize(result)
        self.assertIn("LinkedIn", human)
        self.assertIn("Software Developer at IBM", human)
        self.assertNotIn("{", human)  # no raw JSON

    def test_planner_recursion_is_bounded(self) -> None:
        from laptop_agent.planner.core import PlanDecision

        class LoopingProvider:
            def plan(self, text, available_commands, memory_profile):
                return PlanDecision(action="command", command="totally unknown command", confidence=0.9, explanation="loop")

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            orchestrator.planner = Planner(LoopingProvider())
            result = asyncio.run(orchestrator.handle("do something vague"))
            self.assertTrue(result.ok)

    def test_natural_language_summarize_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = root / "doc.txt"
            doc.write_text("Sentence one here. Sentence two here. Sentence three here.", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"summarize the file {doc}"))
            self.assertTrue(result.ok)
            self.assertIn("planner", result.data)

    def test_email_parse_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email hello"))
            self.assertFalse(result.ok)

    def test_email_oauth_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email oauth status"))
            self.assertTrue(result.ok)
            self.assertIn("gmail", result.data["providers"])

    def test_email_token_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email tokens status"))
            self.assertTrue(result.ok)
            self.assertIn("vault", result.data)

    def test_email_api_search_without_token_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email api search gmail invoice"))
            self.assertFalse(result.ok)
            self.assertIn("OAuth token", result.message)

    def test_email_oauth_refresh_without_token_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email oauth refresh gmail"))
            self.assertFalse(result.ok)
            self.assertIn("OAuth token", result.message)

    def test_email_api_draft_without_token_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email api draft gmail to ada@example.com subject Hi body Hello"))
            self.assertFalse(result.ok)
            self.assertIn("OAuth token", result.message)

    def test_email_api_send_without_token_fails_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email api send outlook to ada@example.com subject Hi body Hello"))
            self.assertFalse(result.ok)
            self.assertIn("OAuth token", result.message)

    def test_audit_command_returns_events(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("audit"))
            self.assertTrue(result.ok)
            self.assertIn("events", result.data)

    def test_agents_command_returns_control_room(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("agents"))
            self.assertTrue(result.ok)
            room = result.data["control_room"]
            self.assertGreaterEqual(room["summary"]["available"], 1)
            self.assertTrue(any(agent["id"] == "research" for agent in room["agents"]))

    def test_agent_detail_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("agent files"))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["agent"]["id"], "files")

    def test_natural_language_remember_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("please remember my name is Ada"))
            self.assertTrue(result.ok)
            self.assertIn("planner", result.data)
            memory = asyncio.run(orchestrator.handle("memory"))
            self.assertEqual(memory.data["memory"]["profile"]["name"], "Ada")

    def test_direct_remember_understands_natural_profile_field(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("remember my name is Ada"))
            self.assertTrue(result.ok)
            memory = asyncio.run(orchestrator.handle("memory"))
            self.assertEqual(memory.data["memory"]["profile"]["name"], "Ada")

    def test_natural_language_file_search_uses_planner(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "note.md").write_text("hello agent\n", encoding="utf-8")
            orchestrator = self.build(root / "data")
            result = asyncio.run(orchestrator.handle(f"find agent in {root}"))
            self.assertTrue(result.ok)
            self.assertIn("planner", result.data)
            self.assertEqual(result.data["matches"][0]["line"], 1)

    def test_three_tier_model_selection(self) -> None:
        class ChatPlanner:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.8, explanation="chat", response="fast-reply")

        class Tier:
            def __init__(self, label):
                self.label = label

            def answer(self, text, memory_profile, model=None, history=None):
                return f"{self.label}-reply"

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            orchestrator.planner = Planner(ChatPlanner())
            orchestrator.smart_planner = Planner(Tier("smart"))
            orchestrator.ultra_planner = Planner(Tier("ultra"))
            # A non-greeting so it goes through the LLM router (greetings short-circuit it).
            simple = asyncio.run(orchestrator.handle("tell me something fun"))
            mid = asyncio.run(orchestrator.handle("explain how this works"))
            hard = asyncio.run(orchestrator.handle("give me a comprehensive in-depth analysis from scratch"))
            self.assertEqual(simple.message, "fast-reply")
            self.assertEqual(mid.message, "smart-reply")
            self.assertEqual(hard.message, "ultra-reply")
            self.assertEqual(hard.data["planner"]["model"], "ultra")

    def test_greeting_skips_llm_routing_call(self) -> None:
        # A confident heuristic greeting should answer without the extra LLM routing
        # round-trip (which would only confirm "this is chat"), halving small-talk latency.
        class CountingChatPlanner:
            def __init__(self) -> None:
                self.calls = 0

            def plan(self, text, available_commands, memory_profile, history=None):
                self.calls += 1
                return PlanDecision(action="chat", confidence=0.8, explanation="chat", response="router-reply")

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            counting = CountingChatPlanner()
            orchestrator.planner = Planner(counting)

            greeting = asyncio.run(orchestrator.handle("hey jarvis how are you"))
            self.assertTrue(greeting.ok)
            self.assertEqual(counting.calls, 0)  # short-circuited — no routing LLM call

            # A non-greeting chat still consults the LLM router.
            asyncio.run(orchestrator.handle("tell me something fun"))
            self.assertEqual(counting.calls, 1)

    def test_smart_answer_receives_history(self) -> None:
        class ChatPlanner:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.8, explanation="chat", response="fast")

        class SmartPlanner:
            def __init__(self) -> None:
                self.history = None

            def answer(self, text, memory_profile, model=None, history=None):
                self.history = history
                return "smart"

        with tempfile.TemporaryDirectory() as raw:
            smart = SmartPlanner()
            orchestrator = self.build(Path(raw))
            orchestrator.planner = Planner(ChatPlanner())
            orchestrator.smart_planner = Planner(smart)
            history = [{"role": "user", "text": "previous question"}]
            result = asyncio.run(orchestrator.handle("explain this design in depth", history=history))
            self.assertTrue(result.ok)
            self.assertEqual(result.message, "smart")
            self.assertEqual(smart.history, history)

    def test_chat_falls_back_when_smart_tier_busy(self) -> None:
        class LowConfRouter:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.0, explanation="", response=None)

        class FastRouting:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.5, explanation="", response="fast routed answer")

        class CongestedSmart:
            def answer(self, text, profile, model=None, history=None):
                return None  # tier busy / unreachable

        with tempfile.TemporaryDirectory() as raw:
            o = self.build(Path(raw))
            o.router = Planner(LowConfRouter())
            o.planner = Planner(FastRouting())
            o.smart_planner = Planner(CongestedSmart())
            o.ultra_planner = None
            res = asyncio.run(o.handle("explain the tradeoffs of this design in depth"))
            self.assertTrue(res.ok)
            self.assertIn("fast routed answer", res.message)
            self.assertIn("smart model was busy", res.message)
            self.assertTrue(res.data["degraded"])
            self.assertEqual(res.data["planner"]["model"], "fast")
            self.assertEqual(res.data["planner"]["requested_model"], "smart")
            self.assertEqual(o.model_status.status("smart"), "degraded")

    def test_chat_streams_fallback_when_smart_tier_busy(self) -> None:
        class LowConfRouter:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.0, explanation="", response=None)

        class FastRoutingStream:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.5, explanation="", response="fast routed")

            def stream_answer(self, text, profile, model=None, history=None):
                yield "fast "
                yield "stream"

        class CongestedSmartStream:
            def stream_answer(self, text, profile, model=None, history=None):
                return iter(())  # yields nothing -> tier busy

            def answer(self, text, profile, model=None, history=None):
                return None

        with tempfile.TemporaryDirectory() as raw:
            o = self.build(Path(raw))
            o.router = Planner(LowConfRouter())
            o.planner = Planner(FastRoutingStream())
            o.smart_planner = Planner(CongestedSmartStream())
            o.ultra_planner = None
            tokens: list[str] = []
            res = asyncio.run(o.handle("explain this design in depth", on_token=tokens.append))
            self.assertTrue(res.ok)
            self.assertIn("fast stream", res.message)
            self.assertIn("fast stream", "".join(tokens))
            self.assertTrue(res.data["degraded"])
            self.assertEqual(res.data["planner"]["model"], "fast")

    def test_chat_ultra_falls_back_to_smart(self) -> None:
        class LowConfRouter:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.0, explanation="", response=None)

        class FastRouting:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.5, explanation="", response="fast routed")

        class CongestedUltra:
            def answer(self, text, profile, model=None, history=None):
                return None

        class WorkingSmart:
            def answer(self, text, profile, model=None, history=None):
                return "smart deep answer"

        with tempfile.TemporaryDirectory() as raw:
            o = self.build(Path(raw))
            o.router = Planner(LowConfRouter())
            o.planner = Planner(FastRouting())
            o.ultra_planner = Planner(CongestedUltra())
            o.smart_planner = Planner(WorkingSmart())
            res = asyncio.run(o.handle("design a system architecture from scratch in depth"))
            self.assertTrue(res.ok)
            self.assertIn("smart deep answer", res.message)
            self.assertIn("ultra model was busy", res.message)
            self.assertEqual(res.data["planner"]["model"], "smart")
            self.assertEqual(res.data["planner"]["requested_model"], "ultra")
            self.assertEqual(o.model_status.status("ultra"), "degraded")
            self.assertEqual(o.model_status.status("smart"), "ok")

    def test_chat_falls_over_to_openrouter_when_all_primary_tiers_busy(self) -> None:
        class LowConfRouter:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.0, explanation="", response=None)

        class DownFast:  # primary fast: routing returned a failure, answer fails too
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.0, explanation="failed", response="(no model)")

            def answer(self, text, profile, model=None, history=None):
                return None

        class DownSmart:
            def answer(self, text, profile, model=None, history=None):
                return None

        class OpenRouter:
            def answer(self, text, profile, model=None, history=None):
                return "openrouter backup answer"

        with tempfile.TemporaryDirectory() as raw:
            o = self.build(Path(raw))
            o.router = Planner(LowConfRouter())
            o.planner = Planner(DownFast())
            o.smart_planner = Planner(DownSmart())
            o.ultra_planner = None
            o.fallback_planner = Planner(OpenRouter())
            res = asyncio.run(o.handle("explain the tradeoffs of this design in depth"))
            self.assertTrue(res.ok)
            self.assertIn("openrouter backup answer", res.message)
            self.assertIn("backup model", res.message)
            self.assertEqual(res.data["planner"]["model"], "openrouter")
            self.assertTrue(res.data["degraded"])
            self.assertEqual(o.model_status.status("openrouter"), "ok")
            self.assertEqual(o.model_status.status("smart"), "degraded")
            self.assertEqual(o.model_status.status("fast"), "degraded")

    def test_openrouter_not_consulted_when_a_primary_tier_answers(self) -> None:
        calls: list[int] = []

        class LowConfRouter:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.0, explanation="", response=None)

        class FastRouting:
            def plan(self, text, available_commands, memory_profile, history=None):
                return PlanDecision(action="chat", confidence=0.5, explanation="", response="routed")

        class WorkingSmart:
            def answer(self, text, profile, model=None, history=None):
                return "smart ok"

        class OpenRouterSpy:
            def answer(self, text, profile, model=None, history=None):
                calls.append(1)
                return "should not be used"

        with tempfile.TemporaryDirectory() as raw:
            o = self.build(Path(raw))
            o.router = Planner(LowConfRouter())
            o.planner = Planner(FastRouting())
            o.smart_planner = Planner(WorkingSmart())
            o.ultra_planner = None
            o.fallback_planner = Planner(OpenRouterSpy())
            res = asyncio.run(o.handle("explain this design in depth"))
            self.assertIn("smart ok", res.message)
            self.assertEqual(calls, [])  # primary tier answered -> fallback untouched
            self.assertFalse(res.data["degraded"])

    def test_solve_command_produces_indexed_advice(self) -> None:
        analysis = "## Problem\nChoose a DB.\n## Recommendation\nPostgres."

        class Brain:
            def answer(self, prompt, profile, model=None, history=None):
                return analysis

        with tempfile.TemporaryDirectory() as raw:
            o = self.build(Path(raw))
            o.smart_planner = Planner(Brain())  # backs _build_agent_brain
            res = asyncio.run(o.handle("solve should I use Postgres or MySQL"))
            self.assertTrue(res.ok)
            self.assertIn("Recommendation", res.message)
            self.assertEqual(res.data["problem"], "should I use Postgres or MySQL")
            self.assertTrue(res.data["used_research"])  # the test research backend grounds it
            self.assertTrue(res.data["indexed"]["ok"])
            recalled = asyncio.run(o.handle("recall Postgres"))
            self.assertTrue(recalled.data["results"])

    def test_around_command_only_for_known_category(self) -> None:
        from laptop_agent.tools.travel import TravelTool

        def transport(url: str):
            if "ip-api" in url:
                return {"status": "success", "lat": 30.27, "lon": -97.74,
                        "city": "Austin", "regionName": "Texas", "country": "US"}
            if "overpass" in url:
                return {"elements": [{"tags": {"name": "Cafe X"}, "lat": 30.28, "lon": -97.74}]}
            return {}

        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            orchestrator._travel_tool_cache = TravelTool(transport=transport)
            # A known category routes to the real around-me places lookup.
            hit = asyncio.run(orchestrator.handle("around coffee"))
            self.assertTrue(hit.ok)
            self.assertIn("Cafe X", hit.message)
            # "around 3pm…" must not be hijacked into a stray category lookup.
            miss = asyncio.run(orchestrator.handle("around 3pm and remind me to call mom"))
            self.assertFalse(miss.message.startswith("I can find:"))


if __name__ == "__main__":
    unittest.main()
