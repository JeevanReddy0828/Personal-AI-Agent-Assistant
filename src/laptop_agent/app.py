from __future__ import annotations

from collections.abc import Callable

from laptop_agent.agents.orchestrator import AgentContext, AgentOrchestrator
from laptop_agent.audit import AuditLogger
from laptop_agent.config import AppConfig, load_config
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.planner import HeuristicPlannerProvider, OpenAICompatiblePlannerProvider, Planner
from laptop_agent.safety import ApprovalGate, ApprovalRequest
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


def build_orchestrator(
    approval_callback: Callable[[ApprovalRequest], bool] | None = None,
    config: AppConfig | None = None,
) -> AgentOrchestrator:
    config = config or load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    audit = AuditLogger(config.audit_log_path)
    approval_gate = ApprovalGate(approval_callback, audit)
    files = FileTool(approval_gate)
    web = WebTool(approval_gate, config.downloads_dir)
    desktop = DesktopTool(approval_gate)

    context = AgentContext(
        memory=MemoryStore(config.memory_path),
        files=files,
        web=web,
        websearch=WebSearchTool(approval_gate),
        browser=BrowserAutomationTool(approval_gate),
        desktop=desktop,
        email=EmailTool(approval_gate, config),
        music=MusicTool(approval_gate, desktop, web),
        research=ResearchTool(approval_gate),
        transcribe=TranscribeTool(),
        audit=audit,
        tasks=TaskTracker(),
        knowledge=KnowledgeBase(config.data_dir / "knowledge.json"),
        obsidian=ObsidianVault(config.obsidian_vault),
    )
    return AgentOrchestrator(context, _build_planner(config), _build_smart_planner(config))


def _has_llm(config: AppConfig) -> bool:
    return config.llm_provider in {"openai", "openai-compatible"} and bool(config.llm_api_key) and bool(config.llm_model)


def _build_planner(config: AppConfig) -> Planner:
    if _has_llm(config):
        return Planner(OpenAICompatiblePlannerProvider(config.llm_api_key, config.llm_model, config.llm_base_url))
    return Planner(HeuristicPlannerProvider())


def _build_smart_planner(config: AppConfig) -> Planner | None:
    # The smart model handles complex questions; falls back to the fast model.
    if not _has_llm(config):
        return None
    smart_model = config.llm_smart_model or config.llm_model
    return Planner(OpenAICompatiblePlannerProvider(config.llm_api_key, smart_model, config.llm_base_url))
