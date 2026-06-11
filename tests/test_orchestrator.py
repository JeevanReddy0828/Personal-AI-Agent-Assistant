from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from laptop_agent.agents.orchestrator import AgentContext, AgentOrchestrator
from laptop_agent.audit import AuditLogger
from laptop_agent.config import AppConfig
from laptop_agent.memory import MemoryStore
from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.web import WebTool


class OrchestratorTests(unittest.TestCase):
    def build(self, tmp: Path) -> AgentOrchestrator:
        gate = ApprovalGate(lambda request: True)
        config = AppConfig(
            data_dir=tmp,
            memory_path=tmp / "memory.json",
            audit_log_path=tmp / "audit.jsonl",
            downloads_dir=tmp / "downloads",
            smtp_host=None,
            smtp_port=587,
            smtp_username=None,
            smtp_password=None,
            smtp_from=None,
        )
        desktop = DesktopTool(gate)
        web = WebTool(gate, config.downloads_dir)
        return AgentOrchestrator(
            AgentContext(
                memory=MemoryStore(config.memory_path),
                files=FileTool(),
                web=web,
                browser=BrowserAutomationTool(gate),
                desktop=desktop,
                email=EmailTool(gate, config),
                music=MusicTool(gate, desktop, web),
                audit=AuditLogger(config.audit_log_path),
            )
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

    def test_email_parse_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("email hello"))
            self.assertFalse(result.ok)

    def test_audit_command_returns_events(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.build(Path(raw))
            result = asyncio.run(orchestrator.handle("audit"))
            self.assertTrue(result.ok)
            self.assertIn("events", result.data)


if __name__ == "__main__":
    unittest.main()
