from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from laptop_agent.agents.orchestrator import AgentContext, AgentOrchestrator
from laptop_agent.audit import AuditLogger
from laptop_agent.config import AppConfig
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.planner import HeuristicPlannerProvider, PlanDecision, Planner
from laptop_agent.safety import ApprovalGate
from laptop_agent.tasks import TaskTracker
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.obsidian import ObsidianVault
from laptop_agent.tools.research import ResearchTool
from laptop_agent.tools.transcribe import TranscribeTool
from laptop_agent.tools.web import WebTool
from laptop_agent.tools.websearch import WebSearchTool


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
                transcribe=TranscribeTool(
                    ocr_backend=lambda path: f"ocr-text::{path.name}",
                    asr_backend=lambda path: {"text": f"transcript::{path.name}", "engine": "fake", "segments": []},
                ),
                audit=AuditLogger(config.audit_log_path),
                tasks=TaskTracker(),
                knowledge=KnowledgeBase(config.data_dir / "knowledge.json"),
                obsidian=ObsidianVault(config.obsidian_vault),
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
            simple = asyncio.run(orchestrator.handle("hey there"))
            mid = asyncio.run(orchestrator.handle("explain how this works"))
            hard = asyncio.run(orchestrator.handle("give me a comprehensive in-depth analysis from scratch"))
            self.assertEqual(simple.message, "fast-reply")
            self.assertEqual(mid.message, "smart-reply")
            self.assertEqual(hard.message, "ultra-reply")
            self.assertEqual(hard.data["planner"]["model"], "ultra")

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


if __name__ == "__main__":
    unittest.main()
