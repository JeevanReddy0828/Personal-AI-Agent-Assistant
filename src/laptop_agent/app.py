from __future__ import annotations

from collections.abc import Callable

from laptop_agent.agents.orchestrator import AgentContext, AgentOrchestrator
from laptop_agent.audit import AuditLogger
from laptop_agent.config import AppConfig, load_config
from laptop_agent.memory import MemoryStore
from laptop_agent.safety import ApprovalGate, ApprovalRequest
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.web import WebTool


def build_orchestrator(
    approval_callback: Callable[[ApprovalRequest], bool] | None = None,
    config: AppConfig | None = None,
) -> AgentOrchestrator:
    config = config or load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    audit = AuditLogger(config.audit_log_path)
    approval_gate = ApprovalGate(approval_callback, audit)
    files = FileTool()
    web = WebTool(approval_gate, config.downloads_dir)
    desktop = DesktopTool(approval_gate)

    context = AgentContext(
        memory=MemoryStore(config.memory_path),
        files=files,
        web=web,
        browser=BrowserAutomationTool(approval_gate),
        desktop=desktop,
        email=EmailTool(approval_gate, config),
        music=MusicTool(approval_gate, desktop, web),
        audit=audit,
    )
    return AgentOrchestrator(context)
