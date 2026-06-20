from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from laptop_agent.agents.control_room import AgentControlRoom
from laptop_agent.audit import AuditLogger
from laptop_agent.autopilot import AutopilotPlanner, AutopilotStep, AutopilotTracker, parse_autopilot_steps
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.metrics import system_metrics
from laptop_agent.planner import HeuristicPlannerProvider, Planner
from laptop_agent.reasoning import AgentRunTracker, AutonomousAgent
from laptop_agent.reminders import ReminderStore
from laptop_agent.scheduler import ScheduleError, SchedulerStore
from laptop_agent.tasks import TaskRecord, TaskTracker
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailDraft, EmailTool
from laptop_agent.tools.file_processor import FileProcessor
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.obsidian import ObsidianVault
from laptop_agent.tools.research import ResearchTool
from laptop_agent.tools.terminal import TerminalTool
from laptop_agent.tools.transcribe import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, TranscribeTool
from laptop_agent.tools.weather import WeatherTool
from laptop_agent.tools.web import WebTool
from laptop_agent.tools.webcam import WebcamTool
from laptop_agent.tools.websearch import WebSearchTool
from laptop_agent.workflows import WorkflowStep, WorkflowTracker


@dataclass(frozen=True)
class AgentContext:
    memory: MemoryStore
    files: FileTool
    web: WebTool
    websearch: WebSearchTool
    browser: BrowserAutomationTool
    desktop: DesktopTool
    email: EmailTool
    music: MusicTool
    research: ResearchTool
    terminal: TerminalTool
    transcribe: TranscribeTool
    webcam: WebcamTool
    audit: AuditLogger
    autopilot: AutopilotTracker
    agent_runs: AgentRunTracker
    scheduler: SchedulerStore
    tasks: TaskTracker
    workflows: WorkflowTracker
    reminders: ReminderStore
    knowledge: KnowledgeBase
    obsidian: ObsidianVault


class AgentOrchestrator:
    def __init__(
        self,
        context: AgentContext,
        planner: Planner | None = None,
        smart_planner: Planner | None = None,
        vision_planner: Planner | None = None,
        ultra_planner: Planner | None = None,
    ) -> None:
        self.context = context
        self.planner = planner
        # Optional higher-capability model for moderately complex questions.
        self.smart_planner = smart_planner
        # Optional top-tier model for the hardest questions (slow, large).
        self.ultra_planner = ultra_planner
        # Optional vision model used to look at images and the screen.
        self.vision_planner = vision_planner
        self.control_room = AgentControlRoom.standard(obsidian_available=self.context.obsidian.available())
        self.autopilot_planner = AutopilotPlanner()
        # Fast deterministic router tried before any LLM, so common requests
        # route instantly and reliably with zero network latency.
        self.router = Planner(HeuristicPlannerProvider())
        self._file_processor_cache: FileProcessor | None = None
        self._weather_tool_cache: WeatherTool | None = None

    @staticmethod
    def _complexity(text: str) -> int:
        """0 = simple (fast model), 1 = complex (smart), 2 = very complex (ultra)."""
        lowered = text.lower()
        words = len(text.split())
        deep = (
            "in depth", "in-depth", "step by step", "step-by-step", "comprehensive", "thorough", "rigorous",
            "deep dive", "detailed analysis", "prove", "derive", "full implementation", "design a system",
            "architecture", "from scratch", "think hard", "deeply", "use the big model", "ultra",
        )
        if words > 70 or any(trigger in lowered for trigger in deep):
            return 2
        mid = (
            "explain", "why", "analy", "compare", "design", "reason", "debug", "refactor", "optimi",
            "trade-off", "tradeoff", "write code", "implement", "algorithm", "strategy", "pros and cons",
            "evaluate", "critique", "plan ",
        )
        if words > 35 or any(trigger in lowered for trigger in mid):
            return 1
        return 0

    def _pick_chat_model(self, level: int):
        """Return (planner, label) for the given complexity, falling back down the tiers."""
        if level >= 2 and self.ultra_planner is not None:
            return self.ultra_planner, "ultra"
        if level >= 1 and self.smart_planner is not None:
            return self.smart_planner, "smart"
        if level >= 2 and self.smart_planner is not None:
            return self.smart_planner, "smart"
        return None, "fast"

    def _route(
        self,
        command: str,
        profile: dict[str, object],
        history: list[dict[str, str]] | None = None,
    ):
        help_text = self.help_text()
        fast = self.router.plan(command, help_text, profile)
        if fast.is_command or self.planner is None:
            return fast
        if type(self.planner.provider).__name__ == "HeuristicPlannerProvider":
            return fast
        # When the instant router is already confident this is plain chat (e.g. a
        # greeting), skip the LLM routing round-trip — it would only confirm "this is
        # chat" and then the chat path makes a second LLM call to actually answer.
        # Cutting the redundant classify call roughly halves latency for small talk.
        if fast.is_chat and fast.confidence >= 0.6:
            return fast
        return self.planner.plan(command, help_text, profile, history)

    async def handle(
        self,
        text: str,
        _allow_planner: bool = True,
        history: list[dict[str, str]] | None = None,
        on_token=None,
    ) -> ToolResult:
        command = text.strip()
        lowered = command.lower()
        history_turns = history or []
        if not command:
            return ToolResult.success("Say something and I will route it.")

        if lowered in {"help", "/help"}:
            return ToolResult.success(self.help_text())

        if lowered.startswith("remember "):
            return self._remember(command[len("remember ") :])

        if lowered in {"memory", "show memory"}:
            return ToolResult.success("Memory loaded.", memory=self.context.memory.dump())

        if lowered in {"audit", "show audit"}:
            return ToolResult.success("Recent audit events.", events=self.context.audit.tail())

        if lowered in {"briefing", "daily briefing", "status briefing", "morning briefing"}:
            return self._briefing()

        if lowered in {"autopilot status", "autonomous status"}:
            return self._autopilot_status()

        if lowered.startswith("autopilot workflow "):
            return await self._run_autopilot(
                goal="custom workflow",
                commands=parse_autopilot_steps(command[len("autopilot workflow ") :].strip()),
            )

        if lowered.startswith("autopilot "):
            goal = command[len("autopilot ") :].strip()
            return await self._run_autopilot(goal=goal, commands=self.autopilot_planner.plan(goal))

        if lowered in {"reminders", "reminders list", "show reminders"}:
            return self._reminders_list()

        if lowered in {"reminders due", "due reminders", "show due reminders"}:
            return self._reminders_due()

        if lowered.startswith("reminder add "):
            return self._reminder_add(command[len("reminder add ") :].strip())

        if lowered.startswith("remind me "):
            return self._reminder_add(command[len("remind me ") :].strip())

        if lowered.startswith("reminder done "):
            return self._reminder_done(command[len("reminder done ") :].strip())

        if lowered in {"schedule", "schedule list", "schedules", "show schedule"}:
            return self._schedule_list()

        if lowered in {"schedule run due", "run due schedules", "schedule tick"}:
            return await self.run_due_schedules()

        if lowered.startswith("schedule remove "):
            return self._schedule_remove(command[len("schedule remove ") :].strip())

        if lowered.startswith("schedule agent "):
            return self._schedule_add("agent", command[len("schedule agent ") :].strip())

        if lowered.startswith("schedule "):
            return self._schedule_add("command", command[len("schedule ") :].strip())

        if lowered in {"agent runs", "agent history", "autonomous runs"}:
            return self._agent_runs()

        if lowered in {"agent last", "agent status", "last agent run"}:
            return self._agent_last()

        if lowered.startswith("agent run "):
            return await self._run_agent(command[len("agent run ") :].strip())

        if lowered in {"agents", "agent control", "control room", "agent dashboard"}:
            return self._agent_control_room()

        if lowered.startswith("agent "):
            return self._agent_detail(command[len("agent ") :].strip())

        if lowered.startswith("scan files "):
            return self.context.files.scan(command[len("scan files ") :].strip() or ".")

        if lowered.startswith("read file "):
            return self.context.files.read_text(command[len("read file ") :].strip())

        if lowered.startswith("ask file "):
            return self._ask_file(command[len("ask file ") :].strip())

        if lowered.startswith("summarize file "):
            return self._summarize_any(command[len("summarize file ") :].strip())

        if lowered.startswith("extract text "):
            return self._extract_text_any(command[len("extract text ") :].strip())

        if lowered.startswith("index file "):
            return self._index_file(command[len("index file ") :].strip())

        if lowered in {"knowledge list", "knowledge"}:
            documents = self.context.knowledge.list_documents()
            return ToolResult.success(f"{len(documents)} document(s) indexed.", documents=documents)

        if lowered in {"knowledge stats", "knowledge status"}:
            stats = self.context.knowledge.stats()
            return ToolResult.success(
                f"Knowledge base: {stats['document_count']} document(s), {stats['total_char_count']} indexed character(s).",
                stats=stats,
            )

        if lowered.startswith("knowledge export "):
            return self._knowledge_export(command[len("knowledge export ") :].strip())

        if lowered.startswith("knowledge forget "):
            return self._knowledge_forget(command[len("knowledge forget ") :].strip())

        if lowered in {"knowledge clear", "forget knowledge"}:
            removed = self.context.knowledge.clear()
            return ToolResult.success(f"Cleared {removed} indexed document(s).", removed=removed)

        if lowered.startswith("knowledge search "):
            return self._knowledge_search(command[len("knowledge search ") :].strip())

        if lowered.startswith("ask knowledge "):
            return self._knowledge_answer(command[len("ask knowledge ") :].strip())

        if lowered.startswith("answer from knowledge "):
            return self._knowledge_answer(command[len("answer from knowledge ") :].strip())

        if lowered.startswith("recall "):
            return self._knowledge_search(command[len("recall ") :].strip())

        if lowered in {"notes", "vault", "notes status", "vault status", "obsidian", "obsidian status"}:
            return self.context.obsidian.status()

        if lowered in {"notes list", "list notes"}:
            return self.context.obsidian.list_notes()

        if lowered.startswith("notes search "):
            return self.context.obsidian.search(command[len("notes search ") :].strip())

        if lowered.startswith("note search "):
            return self.context.obsidian.search(command[len("note search ") :].strip())

        if lowered.startswith("read note "):
            return self.context.obsidian.read_note(command[len("read note ") :].strip())

        if lowered.startswith("save note "):
            rest = command[len("save note ") :].strip()
            match = re.search(r"\s*[:|]\s*", rest)
            if match:
                title, body = rest[: match.start()].strip(), rest[match.end() :].strip()
            else:
                title, body = rest, rest
            return self.context.obsidian.save_note(title, body)

        if lowered.startswith("remember note "):
            return self.context.obsidian.append_memory(command[len("remember note ") :].strip())

        if lowered.startswith("file info "):
            return self.context.files.file_info(command[len("file info ") :].strip())

        if lowered.startswith("extract tables "):
            return self.context.files.extract_tables(command[len("extract tables ") :].strip())

        if lowered.startswith("analyze spreadsheet "):
            return self.context.files.analyze_spreadsheet(command[len("analyze spreadsheet ") :].strip())

        if lowered.startswith("analyse spreadsheet "):
            return self.context.files.analyze_spreadsheet(command[len("analyse spreadsheet ") :].strip())

        if lowered.startswith("inspect file "):
            return self._file_processor().inspect(command[len("inspect file ") :].strip())

        if lowered.startswith("process file "):
            rest = command[len("process file ") :].strip()
            target, intent = self._split_process_intent(rest)
            return self._file_processor().process(target, intent)

        if lowered.startswith("ocr image "):
            return self.context.transcribe.ocr_image(command[len("ocr image ") :].strip())

        if lowered.startswith("ocr "):
            return self.context.transcribe.ocr_image(command[len("ocr ") :].strip())

        if lowered.startswith("transcribe "):
            return self.context.transcribe.transcribe_media(command[len("transcribe ") :].strip())

        if lowered in {"read screen", "screen text", "what is on my screen", "what's on my screen", "look at my screen"}:
            return self._read_screen()

        if lowered.startswith("read screen "):
            return self._read_screen(command[len("read screen ") :].strip())

        if lowered.startswith("look at screen "):
            return self._read_screen(command[len("look at screen ") :].strip())

        if lowered.startswith("describe image "):
            return self._describe_image(command[len("describe image ") :].strip())

        if lowered.startswith("look at image "):
            return self._describe_image(command[len("look at image ") :].strip())

        if lowered in {"look at webcam", "describe webcam", "what do you see", "look at me", "webcam"}:
            return self._webcam_look()

        if lowered.startswith("look at webcam "):
            return self._webcam_look(command[len("look at webcam ") :].strip())

        if lowered.startswith("describe webcam "):
            return self._webcam_look(command[len("describe webcam ") :].strip())

        if lowered in {"capture webcam", "webcam capture", "take a photo"}:
            return self.context.webcam.capture()

        if lowered in {"tasks", "task dashboard", "show tasks"}:
            dashboard = self.context.tasks.latest()
            if dashboard is None:
                return ToolResult.success("No parallel task runs yet. Use 'multi <cmd> ;; <cmd>'.", dashboard=None)
            retry_hint = " Use 'multi retry failed' to rerun failed subtasks." if dashboard.get("retry_available") else ""
            return ToolResult.success(
                f"Latest run: {dashboard['ok_count']} ok, {dashboard['failed_count']} failed.{retry_hint}",
                dashboard=dashboard,
            )

        if lowered in {"workflow status", "workflows", "workflow dashboard"}:
            return self._workflow_status()

        if lowered in {"workflow retry failed", "retry failed workflow"}:
            return await self._workflow_retry_failed()

        if lowered.startswith("workflow "):
            return await self._run_workflow(command[len("workflow ") :].strip())

        if lowered.startswith("convert file "):
            rest = command[len("convert file ") :].strip()
            match = re.search(r"\s+to\s+", rest, re.IGNORECASE)
            if not match:
                return ToolResult.failure("Use: convert file <source> to <destination>")
            source = rest[: match.start()].strip().strip("'\"")
            destination = rest[match.end() :].strip().strip("'\"")
            if not source or not destination:
                return ToolResult.failure("Use: convert file <source> to <destination>")
            return self.context.files.convert(source, destination)

        if lowered.startswith("organize folder "):
            rest = command[len("organize folder ") :].strip()
            apply = False
            if re.search(r"\s+apply$", rest, re.IGNORECASE):
                apply = True
                rest = re.sub(r"\s+apply$", "", rest, flags=re.IGNORECASE).strip()
            target = rest.strip("'\"") or "."
            return self.context.files.organize(target, apply=apply)

        if lowered.startswith("search files "):
            rest = command[len("search files ") :].strip()
            parts = rest.split(maxsplit=1)
            if len(parts) < 2:
                return ToolResult.failure("Use: search files <query> <root>")
            return self.context.files.search_text(parts[0], parts[1])

        if lowered.startswith("save research report "):
            return self._save_research_report(command[len("save research report ") :].strip())

        if lowered.startswith("research report "):
            return self._research_report(command[len("research report ") :].strip())

        if lowered.startswith("research "):
            return self._research(command[len("research ") :].strip())

        if lowered.startswith("weather "):
            return self._weather_tool().forecast(command[len("weather ") :].strip())

        if lowered.startswith("web search "):
            return self.context.websearch.search(command[len("web search ") :].strip())

        if lowered.startswith("search web "):
            return self.context.websearch.search(command[len("search web ") :].strip())

        if lowered.startswith("open url "):
            return self.context.web.open_url(command[len("open url ") :].strip())

        if lowered.startswith("download "):
            return self.context.web.download(command[len("download ") :].strip())

        if lowered.startswith("inspect page "):
            return await self.context.browser.inspect_page(command[len("inspect page ") :].strip())

        if lowered.startswith("inspect forms "):
            return await self.context.browser.inspect_forms(command[len("inspect forms ") :].strip())

        if lowered.startswith("preview form fill "):
            return await self.context.browser.preview_form_fill(
                command[len("preview form fill ") :].strip(),
                self.context.memory.get_profile(),
            )

        if lowered.startswith("fill form "):
            return await self.context.browser.fill_form(
                command[len("fill form ") :].strip(),
                self.context.memory.get_profile(),
            )

        if lowered.startswith("open app "):
            return self.context.desktop.open_app_or_file(command[len("open app ") :].strip())

        if lowered.startswith("screenshot "):
            return self.context.desktop.screenshot(command[len("screenshot ") :].strip())

        if lowered.startswith("run command "):
            return self._run_terminal_command(command[len("run command ") :].strip())

        if lowered.startswith("terminal "):
            return self._run_terminal_command(command[len("terminal ") :].strip())

        if lowered.startswith("shell "):
            return self._run_terminal_command(command[len("shell ") :].strip())

        if lowered.startswith("play music "):
            return self.context.music.play(command[len("play music ") :].strip())

        if lowered.startswith("media "):
            return self.context.music.media_key(command[len("media ") :].strip())

        if lowered in {"email digest", "summarize inbox", "summarize my inbox", "summarize my emails", "summarize my unread", "inbox digest"}:
            return self._email_digest("UNSEEN")

        if lowered.startswith("email digest "):
            return self._email_digest(command[len("email digest ") :].strip() or "UNSEEN")

        if lowered.startswith("email search "):
            return self.context.email.search_inbox(command[len("email search ") :].strip() or "ALL")

        if lowered.startswith("email unread"):
            return self.context.email.search_inbox("UNSEEN")

        if lowered.startswith("email api search "):
            parts = command[len("email api search ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email api search gmail|outlook <query>")
            return self.context.email.search_oauth_mail(parts[0], parts[1])

        if lowered.startswith("email api unread "):
            return self.context.email.search_oauth_mail(command[len("email api unread ") :].strip(), "UNSEEN")

        if lowered.startswith("email api draft "):
            parts = command[len("email api draft ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email api draft gmail|outlook to <addr> subject <subject> body <body>")
            draft_result = self._parse_email(parts[1])
            if not draft_result.ok:
                return draft_result
            return self.context.email.create_oauth_draft(parts[0], draft_result.data["draft"])

        if lowered.startswith("email api send "):
            parts = command[len("email api send ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email api send gmail|outlook to <addr> subject <subject> body <body>")
            draft_result = self._parse_email(parts[1])
            if not draft_result.ok:
                return draft_result
            return self.context.email.send_oauth_mail(parts[0], draft_result.data["draft"])

        if lowered in {"email oauth", "email oauth status"}:
            return self.context.email.oauth_status()

        if lowered.startswith("email oauth url "):
            return self.context.email.oauth_authorization_url(command[len("email oauth url ") :].strip())

        if lowered.startswith("email oauth exchange "):
            parts = command[len("email oauth exchange ") :].strip().split(maxsplit=1)
            if len(parts) != 2:
                return ToolResult.failure("Use: email oauth exchange gmail|outlook <authorization-code>")
            return self.context.email.exchange_oauth_code(parts[0], parts[1])

        if lowered.startswith("email oauth refresh "):
            return self.context.email.refresh_oauth_token(command[len("email oauth refresh ") :].strip())

        if lowered.startswith("email oauth forget "):
            return self.context.email.forget_oauth_token(command[len("email oauth forget ") :].strip())

        if lowered in {"email tokens", "email token status", "email tokens status"}:
            return self.context.email.token_status()

        if lowered.startswith("email "):
            return self._email(command[len("email ") :])

        if lowered.startswith("send email "):
            draft_result = self._parse_email(command[len("send email ") :])
            if not draft_result.ok:
                return draft_result
            return self.context.email.send_smtp(draft_result.data["draft"])

        if lowered.startswith("plan apply job "):
            return await self.context.browser.prepare_job_application(
                command[len("plan apply job ") :].strip(),
                self.context.memory.get_profile(),
            )

        if lowered in {"multi retry failed", "retry failed tasks", "retry failed subtasks"}:
            return await self._retry_failed_tasks()

        if lowered.startswith("multi "):
            return await self._run_many(command[len("multi ") :])

        if _allow_planner:
            planned = self._route(command, self.context.memory.get_profile(), history_turns)
            if planned.is_command and planned.command and planned.command.strip().lower() != lowered:
                # _allow_planner=False stops a planned command from re-triggering
                # the planner, which would let an LLM loop or double-call itself.
                # Light up the specialist the planner delegated to, so the control
                # room reflects the resolved tool, not just the Planner.
                resolved_agent = self.control_room.start(planned.command)
                result = await self.handle(planned.command, _allow_planner=False, history=history_turns)
                self.control_room.finish(resolved_agent, result.message, ok=result.ok)
                # Format the tool result into plain language locally — instant, with
                # no second network round-trip, so natural-language requests stay fast.
                result.message = self._humanize(result)
                result.data.setdefault("planner", {})
                result.data["planner"].update(
                    {
                        "source_text": command,
                        "planned_command": planned.command,
                        "confidence": planned.confidence,
                        "explanation": planned.explanation,
                    }
                )
                return result
            if planned.is_chat and planned.response:
                # Time-sensitive questions ("latest", "did X end", a recent year, …) must
                # not be answered from stale model knowledge — search the web first and
                # answer grounded in the results. Falls back to normal chat if there is no
                # LLM or the search returns nothing.
                needs_fresh = self._needs_fresh_info(command)
                if needs_fresh:
                    grounded = self._grounded_news_answer(command, history_turns, on_token)
                    if grounded is not None:
                        return grounded
                response = planned.response
                # Escalate by task complexity: fast -> smart -> ultra.
                tier_planner, tier_label = self._pick_chat_model(self._complexity(command))
                answer_planner = tier_planner or self.planner
                model_used = tier_label if tier_planner is not None else "fast"
                provider = answer_planner.provider if answer_planner else None
                profile = self.context.memory.get_profile()
                streamer = getattr(provider, "stream_answer", None) if provider else None
                if on_token is not None and streamer is not None:
                    # Stream the reply token-by-token so it appears live.
                    chunks: list[str] = []
                    for token in streamer(command, profile, None, history_turns):
                        chunks.append(token)
                        on_token(token)
                    streamed = "".join(chunks).strip()
                    if streamed:
                        response = streamed
                elif tier_planner is not None:
                    answer = getattr(provider, "answer", None)
                    if answer is not None:
                        better = answer(command, profile, None, history_turns)
                        if better:
                            response = better
                if needs_fresh:
                    # We wanted live data but couldn't get it (no results / search down) — be
                    # honest that this answer may be stale rather than sounding confident.
                    note = "\n\n_Note: I couldn't reach live web search just now, so this may be out of date. Try again, or ask me to “search the web for …”._"
                    response = response + note
                    if on_token is not None:
                        on_token(note)
                return ToolResult.success(
                    response,
                    stale_warning=needs_fresh,
                    planner={
                        "source_text": command,
                        "confidence": planned.confidence,
                        "explanation": planned.explanation,
                        "model": model_used,
                    },
                )

        return self._conversation_fallback(command)

    # Signals that a question is about current/volatile facts and should be answered from
    # live web results rather than the model's training data.
    _FRESH_KEYWORDS = (
        "latest", "currently", "current ", "right now", "today", "tonight", "yesterday",
        "this week", "this month", "this year", "recent", "breaking", "so far", "as of",
        "up to date", "newest", " news", "this morning", "at the moment", "these days",
        "ongoing", "just happened", "this weekend", "nowadays", "this season",
    )
    _FRESH_PATTERNS = (
        r"\bwho (?:is|are|was|won) (?:the )?(?:current|new|latest|winning)?\b",
        r"\bwho won\b",
        r"\bdid .+?\b(?:end|win|won|lose|happen|die|resign|drop|launch|release|pass)\b",
        r"\bis .+?\b(?:still|over|dead|alive|out|available|open|closed|winning)\b",
        r"\bhas .+?\b(?:ended|started|launched|released|happened|died|won)\b",
        r"\bwhat(?:'s| is| are| was)?\b.*\b(?:happening|the latest|new|going on|status)\b",
        r"\b(?:price|stock|score|weather|forecast|exchange rate)\b",
        r"\b20(?:2[4-9]|3\d)\b",  # years 2024-2039 (recent/future vs. training cutoff)
        r"\belection\b",
        r"\bwho is (?:the )?president\b",
        r"\brelease date\b",
        r"\bwhen (?:is|does|will|did)\b.*\b(?:release|come out|launch|start|happen)\b",
        r"\bwar\b.*\b(?:end|ended|over|still|update|status|now|going|latest)\b",
        r"\b(?:update|news|latest) on\b",
    )

    def _needs_fresh_info(self, text: str) -> bool:
        if self.context.websearch is None:
            return False
        lowered = " " + text.lower()
        if any(keyword in lowered for keyword in self._FRESH_KEYWORDS):
            return True
        return any(re.search(pattern, lowered) for pattern in self._FRESH_PATTERNS)

    def _grounded_news_answer(self, command: str, history_turns, on_token) -> ToolResult | None:
        """Search the web and synthesize a cited answer. Returns None (so the caller falls
        back to normal chat) when there is no LLM or the search yields nothing."""
        provider = (self.smart_planner or self.planner).provider if (self.smart_planner or self.planner) else None
        answer = getattr(provider, "answer", None)
        if answer is None:
            return None  # no LLM to synthesize — let normal chat handle it
        # The free DuckDuckGo endpoint is occasionally rate-limited; one retry turns most
        # transient empty results into a usable answer instead of a silent stale fallback.
        results = None
        for _attempt in range(2):
            search = self.context.websearch.search(command, limit=5)
            results = search.data.get("results") if search.ok else None
            if results:
                break
        if not results:
            return None

        today = datetime.now().astimezone().strftime("%A, %B %d, %Y")
        sources = []
        lines = []
        for i, item in enumerate(results, 1):
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            url = str(item.get("url", "")).strip()
            lines.append(f"[{i}] {title}\n{snippet}\n({url})")
            sources.append({"n": i, "title": title, "url": url})
        context = "\n\n".join(lines)
        prompt = (
            f"Today is {today}. Answer the user's question using the web search results below, "
            f"which are current. Prefer this live information over any prior knowledge, lead with the "
            f"most up-to-date facts, and cite sources inline like [1]. If the results don't clearly "
            f"answer it, say what is and isn't known.\n\n"
            f"QUESTION: {command}\n\nWEB SEARCH RESULTS:\n{context}"
        )
        profile = self.context.memory.get_profile()
        streamer = getattr(provider, "stream_answer", None)
        streamed_live = on_token is not None and streamer is not None
        text = ""
        if streamed_live:
            chunks: list[str] = []
            for token in streamer(prompt, profile, None, history_turns):
                chunks.append(token)
                on_token(token)
            text = "".join(chunks).strip()
        if not text:
            text = (answer(prompt, profile, None, history_turns) or "").strip()
        if not text:
            return None

        footer = "\n\n**Sources**\n" + "\n".join(
            f"{s['n']}. [{s['title'] or s['url']}]({s['url']})" for s in sources if s["url"]
        )
        if streamed_live and on_token is not None:
            on_token(footer)  # stream the footer so the live view matches the final message
        return ToolResult.success(
            text + footer,
            grounded=True,
            sources=sources,
            query=search.data.get("query", command),
            planner={"source_text": command, "model": "web+llm", "explanation": "Answered from live web search."},
        )

    @staticmethod
    def help_text() -> str:
        return "\n".join(
            [
                "Commands:",
                "  remember <key> = <value>",
                "  memory",
                "  audit",
                "  briefing",
                "  autopilot <goal>",
                "  autopilot workflow <safe command 1> ;; <safe command 2>",
                "  autopilot status",
                "  agent run <goal>   (autonomous multi-step: plans, acts, observes, repeats)",
                "  agent runs | agent last",
                "  schedule <when> :: <command>     (e.g. 'daily at 08:00 :: briefing')",
                "  schedule agent <when> :: <goal>  (run the autonomous agent on a schedule)",
                "  schedule list | schedule remove <id> | schedule run due",
                "  reminder add <YYYY-MM-DD HH:MM> <message>",
                "  reminders | reminders due | reminder done <id>",
                "  agents | agent <id>",
                "  scan files <path>",
                "  read file <path>",
                "  ask file <path> about <question>",
                "  summarize file <path>  (text, PDF, DOCX, images, audio, video)",
                "  extract text <path>",
                "  file info <path>",
                "  extract tables <path>",
                "  analyze spreadsheet <path>  (per-column stats for CSV/TSV)",
                "  process file <path> [as <operation>]  (auto-detects type, picks the best action)",
                "  convert file <source> to <destination>",
                "  organize folder <path> [apply]",
                "  ocr image <path>",
                "  transcribe <audio-or-video-path>",
                "  read screen [question]   (vision: looks at your screen)",
                "  describe image <path>    (vision)",
                "  look at webcam [question]  (vision: captures and describes a camera frame)",
                "  index file <path>",
                "  recall <query>",
                "  ask knowledge <question>",
                "  knowledge list",
                "  knowledge stats",
                "  knowledge export <path>",
                "  knowledge forget <id>",
                "  notes status | notes list | notes search <query>",
                "  read note <name> | save note <title> : <body>",
                "  remember note <text>",
                "  search files <query> <path>",
                "  web search <query>",
                "  weather <location>  (real current + 3-day forecast)",
                "  research <topic>",
                "  research report <topic>",
                "  save research report <topic> to <path|obsidian>",
                "  open url <url>",
                "  download <url>",
                "  inspect page <url>",
                "  inspect forms <url>",
                "  preview form fill <url>",
                "  fill form <url>",
                "  open app <path-or-app>",
                "  screenshot <output.png>",
                "  run command <command>",
                "  run command in <cwd> :: <command>",
                "  play music <file-folder-or-url>",
                "  media playpause|next|previous|stop",
                "  email search <query>",
                "  email unread",
                "  email digest  (summarize your unread inbox)",
                "  email api search gmail|outlook <query>",
                "  email api unread gmail|outlook",
                "  email api draft gmail|outlook to <addr> subject <subject> body <body>",
                "  email api send gmail|outlook to <addr> subject <subject> body <body>",
                "  email oauth status",
                "  email oauth url gmail|outlook",
                "  email oauth exchange gmail|outlook <authorization-code>",
                "  email oauth refresh gmail|outlook",
                "  email oauth forget gmail|outlook",
                "  email tokens status",
                "  email to <addr> subject <subject> body <body>",
                "  send email to <addr> subject <subject> body <body>",
                "  plan apply job <job-url-or-description>",
                "  multi <command 1> ;; <command 2>",
                "  multi retry failed",
                "  tasks",
                "  workflow <command 1> ;; <command 2>",
                "  workflow status | workflow retry failed",
            ]
        )

    def _reminder_add(self, expression: str) -> ToolResult:
        cleaned = expression.strip().strip("'\"")
        match = re.match(
            r"(?P<date>\d{4}-\d{2}-\d{2})(?:[ T](?P<time>\d{2}:\d{2}))?\s+(?P<message>.+)$",
            cleaned,
        )
        if match:
            due_at = match.group("date") + (" " + match.group("time") if match.group("time") else " 09:00")
            message = match.group("message").strip()
        else:
            natural = re.match(r"(?:to\s+)?(?P<message>.+?)\s+(?:at|on)\s+(?P<due>.+)$", cleaned, re.IGNORECASE)
            if not natural:
                return ToolResult.failure("Use: reminder add <YYYY-MM-DD HH:MM> <message>")
            due_at = natural.group("due").strip()
            message = natural.group("message").strip()
        try:
            outcome = self.context.reminders.add(due_at, message)
        except ValueError:
            return ToolResult.failure("Reminder date/time must look like YYYY-MM-DD HH:MM.")
        if not outcome.get("ok"):
            return ToolResult.failure(f"Could not add reminder: {outcome.get('reason', 'unknown error')}")
        reminder = outcome["reminder"]
        return ToolResult.success(
            f"Reminder #{reminder['id']} set for {reminder['due_at']}: {reminder['message']}",
            reminder=reminder,
        )

    def _reminders_list(self) -> ToolResult:
        reminders = self.context.reminders.list()
        return ToolResult.success(
            f"{len(reminders)} active reminder(s).",
            reminders=reminders,
        )

    def _reminders_due(self) -> ToolResult:
        reminders = self.context.reminders.due()
        return ToolResult.success(
            f"{len(reminders)} reminder(s) due.",
            reminders=reminders,
        )

    def _reminder_done(self, raw: str) -> ToolResult:
        try:
            reminder_id = int(raw.strip())
        except ValueError:
            return ToolResult.failure("Use: reminder done <id>")
        completed = self.context.reminders.complete(reminder_id)
        return ToolResult.success(
            f"Completed reminder #{reminder_id}." if completed else f"No active reminder #{reminder_id}.",
            id=reminder_id,
            completed=completed,
        )

    def _briefing(self) -> ToolResult:
        due_reminders = self.context.reminders.due()
        active_reminders = self.context.reminders.list()
        latest_tasks = self.context.tasks.latest()
        knowledge_stats = self.context.knowledge.stats()
        agents = self.control_room.snapshot()
        metrics = system_metrics()

        lines = ["## Briefing", ""]
        if due_reminders:
            lines.append(f"**Due reminders:** {len(due_reminders)}")
            lines.extend(f"- #{item.get('id')} {item.get('message')} ({item.get('due_at')})" for item in due_reminders[:5])
        else:
            lines.append("**Due reminders:** none")

        upcoming = [item for item in active_reminders if item not in due_reminders]
        if upcoming:
            lines.append("")
            lines.append(f"**Upcoming:** {len(upcoming)} active reminder(s)")
            lines.extend(f"- #{item.get('id')} {item.get('message')} ({item.get('due_at')})" for item in upcoming[:3])

        lines.append("")
        if latest_tasks:
            lines.append(
                "**Latest task run:** "
                f"{latest_tasks.get('ok_count', 0)} ok, {latest_tasks.get('failed_count', 0)} failed "
                f"across {latest_tasks.get('task_count', 0)} task(s)"
            )
        else:
            lines.append("**Latest task run:** none yet")

        lines.append(
            "**Knowledge:** "
            f"{knowledge_stats['document_count']} document(s), {knowledge_stats['total_char_count']} indexed character(s)"
        )
        agent_summary = agents["summary"]
        lines.append(
            "**Agents:** "
            f"{agent_summary['working']} working, {agent_summary['idle']} idle, {agent_summary['unavailable']} unavailable"
        )
        cpu = metrics.get("cpu_percent")
        ram = metrics.get("ram_percent")
        lines.append(
            "**System:** "
            f"CPU {cpu if cpu is not None else 'n/a'}%, RAM {ram if ram is not None else 'n/a'}%"
        )

        return ToolResult.success(
            "\n".join(lines),
            due_reminders=due_reminders,
            active_reminders=active_reminders,
            latest_tasks=latest_tasks,
            knowledge=knowledge_stats,
            agents=agents,
            metrics=metrics,
        )

    def _run_terminal_command(self, expression: str) -> ToolResult:
        cleaned = expression.strip()
        if not cleaned:
            return ToolResult.failure("Use: run command <command>")
        match = re.match(r"in\s+(.+?)\s+::\s+(.+)$", cleaned, re.IGNORECASE | re.DOTALL)
        if match:
            cwd = match.group(1).strip().strip("'\"")
            command = match.group(2).strip()
            return self.context.terminal.run(command, cwd=cwd)
        return self.context.terminal.run(cleaned)

    async def _run_autopilot(self, goal: str, commands: list[str]) -> ToolResult:
        cleaned_goal = goal.strip() or "autopilot"
        if not commands:
            return ToolResult.failure("Autopilot could not build a plan for that goal.", goal=cleaned_goal)

        results = []
        records: list[AutopilotStep] = []
        for index, planned in enumerate(commands):
            command = planned.strip()
            if not self.autopilot_planner.is_safe_command(command):
                message = "Blocked: this command needs supervision or approval."
                results.append({"command": command, "ok": False, "message": message, "data": {}})
                records.append(AutopilotStep(index=index, command=command, status="blocked", message=message))
                continue
            agent_id = self.control_room.start(command)
            try:
                result = await self.handle(command, _allow_planner=False)
            except Exception as exc:
                self.control_room.finish(agent_id, str(exc), ok=False)
                result = ToolResult.failure(str(exc))
            else:
                self.control_room.finish(agent_id, result.message, ok=result.ok)
            results.append({"command": command, "ok": result.ok, "message": result.message, "data": result.data})
            records.append(
                AutopilotStep(
                    index=index,
                    command=command,
                    status="ok" if result.ok else "failed",
                    message=result.message,
                )
            )

        run = self.context.autopilot.record_run(cleaned_goal, records)
        if run["status"] == "ok":
            return ToolResult.success(
                f"Autopilot completed {run['ok_count']} step(s) for: {cleaned_goal}",
                goal=cleaned_goal,
                plan=commands,
                results=results,
                autopilot=run,
            )
        return ToolResult.failure(
            f"Autopilot finished with {run['failed_count']} failed and {run['blocked_count']} blocked step(s).",
            goal=cleaned_goal,
            plan=commands,
            results=results,
            autopilot=run,
        )

    def _autopilot_status(self) -> ToolResult:
        latest = self.context.autopilot.latest()
        if latest is None:
            return ToolResult.success("No autopilot runs yet.", autopilot=None)
        return ToolResult.success(
            f"Latest autopilot run: {latest['status']} ({latest['ok_count']} ok, {latest['failed_count']} failed, {latest['blocked_count']} blocked).",
            autopilot=latest,
        )

    # The autonomous agent's action vocabulary. These are canonical command forms the
    # orchestrator dispatches directly (no planner), with argument placeholders for the
    # model to fill. This is deliberately broad so users can speak naturally and let the
    # agent translate intent into the right action — read-only steps just run, while
    # state-changing ones (send, write, download, shell, browser) still pass through the
    # approval gate. Keep entries to one line each; the model copies them verbatim.
    _AGENT_COMMANDS = (
        # files & documents
        "scan files <path>",
        "read file <path>",
        "ask file <path> about <question>",
        "summarize file <path>",
        "extract text <path>",
        "extract tables <path>",
        "analyze spreadsheet <path>",
        "process file <path>",
        "file info <path>",
        "search files <query> <path>",
        "convert file <source> to <destination>",
        "organize folder <path>",
        "index file <path>",
        # knowledge base
        "recall <query>",
        "ask knowledge <question>",
        "knowledge list",
        "knowledge stats",
        # notes / vault memory
        "notes search <query>",
        "read note <name>",
        "save note <title> : <body>",
        "remember <key> = <value>",
        # web & research
        "web search <query>",
        "weather <location>",
        "research <topic>",
        "research report <topic>",
        "open url <https url>",
        "download <url>",
        "inspect page <url>",
        # vision / desktop
        "read screen <question>",
        "describe image <path>",
        "look at webcam <question>",
        "open app <path-or-app>",
        "screenshot <output.png>",
        "play music <file-folder-or-url>",
        # email (state-changing steps are approval-gated)
        "email digest",
        "email api search gmail <query>",
        "email api draft gmail to <addr> subject <subject> body <body>",
        "email api send gmail to <addr> subject <subject> body <body>",
        # tasks, reminders, status
        "briefing",
        "tasks",
        "reminders due",
        "reminder add <YYYY-MM-DD> <text>",
        "run command <command>",
        "memory",
        "audit",
    )

    def _agent_reference(self) -> str:
        return "\n".join(f"- {command}" for command in self._AGENT_COMMANDS)

    def _build_agent_brain(self):
        """Return a sync ``decide(prompt) -> str`` backed by the strongest available model.

        Prefers the smart tier for reasoning, then the fast planner. Returns a brain that
        yields '' when no LLM is configured, so the loop ends with a clear message instead
        of looping blindly on the heuristic router.
        """
        planner = self.smart_planner or self.planner
        provider = planner.provider if planner else None
        answer = getattr(provider, "answer", None)
        if answer is None:
            return lambda _prompt: ""
        profile = self.context.memory.get_profile()

        def decide(prompt: str) -> str:
            return answer(prompt, profile, None, None) or ""

        return decide

    async def run_agent(self, goal: str, on_step=None) -> ToolResult:
        """Public entry for autonomous runs with an optional per-step callback (used by the
        web UI to stream the live trace). Mirrors the 'agent run <goal>' command path."""
        return await self._run_agent(goal, on_step=on_step)

    async def _run_agent(self, goal: str, on_step=None) -> ToolResult:
        goal = goal.strip()
        if not goal:
            return ToolResult.failure("Use: agent run <goal>  (e.g. 'agent run summarize the README and index it')")

        agent_id = self.control_room.start(f"agent: {goal}")
        agent = AutonomousAgent(
            decide=self._build_agent_brain(),
            execute=lambda command: self.handle(command, _allow_planner=False),
            command_reference=self._agent_reference(),
            max_steps=6,
        )
        try:
            result = await agent.run(goal, on_step=on_step)
        except Exception as exc:  # defensive — keep the control room consistent
            self.control_room.finish(agent_id, str(exc), ok=False)
            return ToolResult.failure(f"Autonomous run crashed: {exc}", goal=goal)
        self.control_room.finish(agent_id, result.final_answer, ok=result.status != "failed")

        run = self.context.agent_runs.record_run(result)
        payload = dict(
            goal=goal,
            answer=result.final_answer,
            steps=[step.__dict__ for step in result.steps],
            agent=run,
        )
        if result.status == "failed":
            return ToolResult.failure(result.final_answer, **payload)
        return ToolResult.success(result.final_answer, **payload)

    def _agent_runs(self) -> ToolResult:
        runs = self.context.agent_runs.all_runs()
        return ToolResult.success(f"{len(runs)} autonomous run(s) on record.", agent_runs=runs)

    def _agent_last(self) -> ToolResult:
        latest = self.context.agent_runs.latest()
        if latest is None:
            return ToolResult.success("No autonomous agent runs yet.", agent=None)
        return ToolResult.success(
            f"Latest agent run: {latest['status']} ({latest['ok_count']} ok, "
            f"{latest['failed_count']} failed across {latest['step_count']} step(s)).",
            agent=latest,
        )

    def _schedule_add(self, kind: str, expression: str) -> ToolResult:
        # Expression: "<schedule> :: <command-or-goal>", e.g. "daily at 08:00 :: briefing".
        if "::" not in expression:
            return ToolResult.failure(
                "Use: schedule <when> :: <command>   or   schedule agent <when> :: <goal>\n"
                "e.g. 'schedule daily at 08:00 :: briefing' or 'schedule agent every 2 hours :: triage my unread email'"
            )
        when, spec = (part.strip() for part in expression.split("::", 1))
        try:
            job = self.context.scheduler.add(kind, spec, when, datetime.now().astimezone())
        except ScheduleError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(
            f"Scheduled {kind} #{job.id}: {job.schedule.describe()} -> {job.spec}",
            job=job.to_dict(),
        )

    def _schedule_remove(self, raw: str) -> ToolResult:
        try:
            job_id = int(raw.strip().lstrip("#"))
        except ValueError:
            return ToolResult.failure("Use: schedule remove <id>")
        if self.context.scheduler.remove(job_id):
            return ToolResult.success(f"Removed scheduled job #{job_id}.")
        return ToolResult.failure(f"No scheduled job #{job_id}.")

    def _schedule_list(self) -> ToolResult:
        jobs = [job.to_dict() for job in self.context.scheduler.list_jobs()]
        if not jobs:
            return ToolResult.success("No scheduled jobs. Add one with 'schedule <when> :: <command>'.", jobs=[])
        return ToolResult.success(f"{len(jobs)} scheduled job(s).", jobs=jobs)

    async def run_due_schedules(self, now: datetime | None = None) -> ToolResult:
        """Run every job whose schedule is due. Called by the background ticker and the
        'schedule run due' command. Each job runs through handle()/run_agent so risky steps
        still hit the approval gate."""
        moment = now or datetime.now().astimezone()
        due = self.context.scheduler.due_jobs(moment)
        ran = []
        for job in due:
            try:
                if job.kind == "agent":
                    result = await self._run_agent(job.spec)
                else:
                    result = await self.handle(job.spec, _allow_planner=False)
                status = "ok" if result.ok else "failed"
                message = result.message
            except Exception as exc:
                status, message = "failed", str(exc)
            self.context.scheduler.mark_ran(job.id, datetime.now().astimezone(), status)
            ran.append({"id": job.id, "kind": job.kind, "spec": job.spec, "status": status, "message": message})
        if not ran:
            return ToolResult.success("No scheduled jobs are due.", ran=[])
        ok = sum(1 for item in ran if item["status"] == "ok")
        return ToolResult.success(f"Ran {len(ran)} due job(s): {ok} ok.", ran=ran)

    def _file_processor(self) -> FileProcessor:
        # Built lazily from existing tools — no AgentContext field needed.
        if self._file_processor_cache is None:
            self._file_processor_cache = FileProcessor(self.context.files, self.context.transcribe)
        return self._file_processor_cache

    def _weather_tool(self) -> WeatherTool:
        if self._weather_tool_cache is None:
            # Reuse the shared approval gate so weather is MEDIUM-gated like other network reads.
            self._weather_tool_cache = WeatherTool(approval_gate=self.context.web.approval_gate)
        return self._weather_tool_cache

    @staticmethod
    def _split_process_intent(rest: str) -> tuple[str, str | None]:
        """Split 'process file <path>' from an optional trailing '... as <intent>'.

        Accepts 'report.csv as stats' or 'report.csv : summarize'; otherwise the
        whole string is the path and the processor picks the default operation.
        """
        match = re.search(r"\s+(?:as|for|:)\s+([\w ]+)$", rest, re.IGNORECASE)
        if match:
            return rest[: match.start()].strip().strip("'\""), match.group(1).strip()
        return rest.strip().strip("'\""), None

    def _extract_text_any(self, path: str) -> ToolResult:
        target = path.strip().strip("'\"")
        if not target:
            return ToolResult.failure("Use: extract text <path>")
        suffix = Path(target).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            return self.context.transcribe.ocr_image(target)
        if suffix in MEDIA_EXTENSIONS:
            return self.context.transcribe.transcribe_media(target)
        return self.context.files.extract_document_text(target)

    def _index_file(self, path: str) -> ToolResult:
        target = path.strip().strip("'\"")
        if not target:
            return ToolResult.failure("Use: index file <path>")
        extracted = self._extract_text_any(target)
        if not extracted.ok:
            return extracted
        text = str(extracted.data.get("text", ""))
        outcome = self.context.knowledge.add(target, text)
        if not outcome.get("ok"):
            return ToolResult.failure(f"Nothing to index from {target}: {outcome.get('reason')}.")
        return ToolResult.success(
            f"Indexed {target} into the knowledge base (#{outcome['id']}).",
            document=outcome,
        )

    def _research(self, topic: str) -> ToolResult:
        cleaned = topic.strip().strip("'\"")
        if not cleaned:
            return ToolResult.failure("Use: research <topic>")
        gathered = self.context.research.gather(cleaned)
        if not gathered.ok:
            return gathered
        text = str(gathered.data.get("text", ""))
        summary_result = self.context.files.summarize_text(text, source=f"research: {cleaned}", sentences=6)
        summary = str(summary_result.data.get("summary", "")) if summary_result.ok else ""
        indexed = self.context.knowledge.add(f"research: {cleaned}", text)
        return ToolResult.success(
            f"Researched '{cleaned}' across {len(gathered.data.get('sources', []))} source(s) and indexed the findings.",
            topic=cleaned,
            summary=summary,
            sources=gathered.data.get("sources", []),
            indexed=indexed,
        )

    def _research_report(self, topic: str) -> ToolResult:
        cleaned = topic.strip().strip("'\"")
        if not cleaned:
            return ToolResult.failure("Use: research report <topic>")
        report = self.context.research.report(cleaned)
        if not report.ok:
            return report
        text = str(report.data.get("report", ""))
        indexed = self.context.knowledge.add(f"research report: {cleaned}", text)
        report.data["indexed"] = indexed
        return report

    def _save_research_report(self, expression: str) -> ToolResult:
        match = re.search(r"\s+to\s+", expression, re.IGNORECASE)
        if not match:
            return ToolResult.failure("Use: save research report <topic> to <path|obsidian>")
        topic = expression[: match.start()].strip().strip("'\"")
        destination = expression[match.end() :].strip().strip("'\"")
        if not topic or not destination:
            return ToolResult.failure("Use: save research report <topic> to <path|obsidian>")

        report = self._research_report(topic)
        if not report.ok:
            return report
        markdown = str(report.data.get("report", ""))
        if destination.lower() in {"obsidian", "vault", "notes"}:
            title = f"Research Report - {topic}"
            saved = self.context.obsidian.save_note(title, markdown, folder="Research Reports")
            if not saved.ok:
                return saved
            report.data["saved"] = saved.data
            return ToolResult.success(
                f"Saved research report for '{topic}' to Obsidian.",
                **report.data,
            )

        saved = self.context.files.write_text(destination, markdown, description="research report")
        if not saved.ok:
            return saved
        report.data["saved"] = saved.data
        return ToolResult.success(
            f"Saved research report for '{topic}' to {saved.data['destination']}.",
            **report.data,
        )

    def _knowledge_search(self, query: str) -> ToolResult:
        cleaned = query.strip().strip("'\"")
        if not cleaned:
            return ToolResult.failure("Use: recall <query>")
        results = self.context.knowledge.search(cleaned)
        return ToolResult.success(
            f"Found {len(results)} relevant document(s) for '{cleaned}'.",
            query=cleaned,
            results=results,
        )

    def _ask_file(self, expression: str) -> ToolResult:
        cleaned = expression.strip().strip("'\"")
        match = re.search(r"\s+(?:about|for|on)\s+", cleaned, re.IGNORECASE)
        if match:
            path = cleaned[: match.start()].strip().strip("'\"")
            question = cleaned[match.end() :].strip().strip("'\"")
        else:
            split_at = cleaned.find("?")
            if split_at > 0:
                path = cleaned[:split_at].strip().strip("'\"")
                question = cleaned[split_at + 1 :].strip().strip("'\"")
            else:
                return ToolResult.failure("Use: ask file <path> about <question>")
        if not path or not question:
            return ToolResult.failure("Use: ask file <path> about <question>")
        return self.context.files.answer_question(path, question)

    def _knowledge_answer(self, question: str) -> ToolResult:
        cleaned = question.strip().strip("'\"?")
        if not cleaned:
            return ToolResult.failure("Use: ask knowledge <question>")
        answer = self.context.knowledge.answer(cleaned)
        if not answer.get("ok"):
            return ToolResult.failure(
                f"I could not answer from indexed knowledge: {answer.get('reason', 'no relevant text')}.",
                question=cleaned,
            )
        return ToolResult.success(
            f"Answered from {len(answer.get('sources', []))} indexed source(s).",
            question=cleaned,
            answer=answer.get("answer", ""),
            excerpts=answer.get("excerpts", []),
            sources=answer.get("sources", []),
        )

    def _knowledge_forget(self, raw: str) -> ToolResult:
        try:
            doc_id = int(raw.strip())
        except ValueError:
            return ToolResult.failure("Use: knowledge forget <id> (a number from 'knowledge list').")
        removed = self.context.knowledge.forget(doc_id)
        return ToolResult.success(
            f"Removed document #{doc_id}." if removed else f"No indexed document #{doc_id}.",
            removed=removed,
            id=doc_id,
        )

    def _knowledge_export(self, raw: str) -> ToolResult:
        destination = raw.strip().strip("'\"")
        if not destination:
            return ToolResult.failure("Use: knowledge export <path>")
        markdown = self.context.knowledge.export_markdown()
        saved = self.context.files.write_text(destination, markdown, description="knowledge export")
        if not saved.ok:
            return saved
        return ToolResult.success(
            f"Exported knowledge base to {saved.data['destination']}.",
            export=saved.data,
            stats=self.context.knowledge.stats(),
        )

    def _summarize_any(self, path: str) -> ToolResult:
        target = path.strip().strip("'\"")
        suffix = Path(target).suffix.lower()
        extracted = self._extract_text_any(target)
        if not extracted.ok:
            # Documents have a direct summarizer; fall back to it if extraction failed.
            if suffix not in IMAGE_EXTENSIONS and suffix not in MEDIA_EXTENSIONS:
                return self.context.files.summarize(target)
            return extracted
        text = str(extracted.data.get("text", ""))
        result = self.context.files.summarize_text(text, source=target)
        if result.ok:
            if suffix in IMAGE_EXTENSIONS:
                result.data["extracted_from"] = "ocr"
            elif suffix in MEDIA_EXTENSIONS:
                result.data["extracted_from"] = "transcription"
            # Auto-index what we summarized, so it is recallable later.
            result.data["indexed"] = self._auto_index(target, text)
        return result

    def _auto_index(self, source: str, text: str) -> bool:
        """Best-effort: add summarized/read text to the knowledge base for later recall."""
        if not text or len(text.strip()) < 200:
            return False
        try:
            outcome = self.context.knowledge.add(source, text)
        except OSError:
            return False
        return bool(outcome.get("ok"))

    def _read_screen(self, question: str = "") -> ToolResult:
        handle = tempfile.NamedTemporaryFile(prefix="laptop_agent_screen_", suffix=".png", delete=False)
        handle.close()
        shot = self.context.desktop.screenshot(handle.name)
        if not shot.ok:
            return shot
        path = str(shot.data["path"])
        prompt = question.strip() or (
            "You are J.A.R.V.I.S, an assistant that runs as a dark desktop app window titled 'J.A.R.V.I.S' on this "
            "very screen. Concisely describe what the user is working on. If you see your own app window (a dark chat "
            "with an amber accent), call it out as yourself ('that dark window is me'). Focus on what matters, not every pixel."
        )
        described = self._describe_with_vision(path, prompt)
        if described is not None:
            return ToolResult.success(described, screenshot=path, vision=True)
        # No vision model available — fall back to OCR text.
        ocr = self.context.transcribe.ocr_image(path)
        if not ocr.ok:
            return ToolResult.failure(
                "I captured the screen but can't read it: no vision model is configured and OCR is unavailable. "
                + ocr.message,
                screenshot=path,
            )
        return ToolResult.success(
            f"Read {ocr.data.get('char_count', 0)} character(s) of text from the screen.",
            screenshot=path,
            text=ocr.data.get("text", ""),
            char_count=ocr.data.get("char_count", 0),
        )

    def _webcam_look(self, question: str = "") -> ToolResult:
        """Capture a webcam frame and describe it with the vision model (OCR fallback)."""
        shot = self.context.webcam.capture()
        if not shot.ok:
            return shot
        path = str(shot.data["path"])
        prompt = question.strip() or (
            "You are J.A.R.V.I.S looking through the user's webcam. Concisely describe who and "
            "what you see — the person, their setting, and anything notable. Be natural, not clinical."
        )
        described = self._describe_with_vision(path, prompt)
        if described is not None:
            return ToolResult.success(described, webcam=path, vision=True)
        ocr = self.context.transcribe.ocr_image(path)
        if ocr.ok and ocr.data.get("char_count"):
            return ToolResult.success(
                f"I captured a webcam frame and read {ocr.data.get('char_count', 0)} character(s) of text from it.",
                webcam=path, text=ocr.data.get("text", ""),
            )
        return ToolResult.failure(
            "I captured a webcam frame but can't interpret it: no vision model is configured. "
            "Set OPENAI_VISION_MODEL to describe what the camera sees.",
            webcam=path,
        )

    def _describe_image(self, path: str, question: str = "") -> ToolResult:
        target = path.strip().strip("'\"")
        if not target or not Path(target).is_file():
            return ToolResult.failure(f"Image not found: {target}")
        prompt = question.strip() or "Describe this image in detail. What is shown?"
        described = self._describe_with_vision(target, prompt)
        if described is not None:
            return ToolResult.success(described, path=target, vision=True)
        ocr = self.context.transcribe.ocr_image(target)
        if ocr.ok:
            return ToolResult.success(
                f"Read {ocr.data.get('char_count', 0)} character(s) of text from the image.",
                path=target, text=ocr.data.get("text", ""), char_count=ocr.data.get("char_count", 0),
            )
        return ToolResult.failure(
            "I couldn't read that image: no vision model is configured and OCR is unavailable. " + ocr.message,
            path=target,
        )

    def _describe_with_vision(self, path: str, prompt: str) -> str | None:
        if self.vision_planner is None:
            return None
        describe = getattr(self.vision_planner.provider, "describe_image", None)
        if describe is None:
            return None
        return describe(path, prompt)

    @staticmethod
    def _humanize(result: ToolResult) -> str:
        """Turn a tool result into a friendly sentence locally (no LLM round-trip)."""
        if not result.ok:
            return result.message
        data = result.data
        if data.get("digest"):  # already an LLM-written summary; leave it as-is
            return result.message
        if data.get("answer"):
            return str(data["answer"])
        if data.get("summary"):
            return str(data["summary"])
        if "files" in data and "root" in data:
            return f"I found {len(data['files'])} file(s) in {data['root']}."
        if isinstance(data.get("results"), list):
            results = data["results"]
            query = str(data.get("query", "")).strip()
            if not results:
                return f"I couldn't find anything for '{query}'." if query else result.message
            first = results[0] if isinstance(results[0], dict) else {}
            if "url" in first:
                lines = [f"{i + 1}. {r.get('title', '')} — {r.get('url', '')}" for i, r in enumerate(results[:5])]
                return "Here are the top results:\n" + "\n".join(lines)
            if "source" in first:
                lines = [f"- {r.get('source', '')}: {str(r.get('snippet', '')).strip()[:140]}" for r in results[:5]]
                return f"I found {len(results)} match(es):\n" + "\n".join(lines)
        if isinstance(data.get("messages"), list):
            messages = data["messages"]
            if not messages:
                return "Your inbox has no messages matching that."
            lines = []
            for index, mail in enumerate(messages[:8], start=1):
                sender = str(mail.get("from", "")).split("<")[0].strip().strip('"') or str(mail.get("from", ""))
                subject = str(mail.get("subject", "")).strip() or "(no subject)"
                snippet = " ".join(str(mail.get("snippet", "")).split())[:110]
                lines.append(f"{index}. **{sender}** — {subject}" + (f"\n   {snippet}" if snippet else ""))
            more = f"\n\n…and {len(messages) - 8} more." if len(messages) > 8 else ""
            return f"Here are your {len(messages)} most recent message(s):\n\n" + "\n".join(lines) + more
        if isinstance(data.get("columns"), list) and "row_count" in data:
            columns = data["columns"]
            lines = []
            for column in columns[:12]:
                if not isinstance(column, dict):
                    continue
                if column.get("type") == "number":
                    lines.append(
                        f"- {column.get('name')}: number "
                        f"(min {column.get('min')}, max {column.get('max')}, mean {column.get('mean')})"
                    )
                else:
                    samples = ", ".join(str(value) for value in column.get("samples", [])[:3])
                    detail = f"{column.get('unique', 0)} unique" + (f"; e.g. {samples}" if samples else "")
                    lines.append(f"- {column.get('name')}: {column.get('type')} ({detail})")
            more = f"\n…and {len(columns) - 12} more column(s)." if len(columns) > 12 else ""
            return result.message + "\n" + "\n".join(lines) + more
        if "text" in data and "char_count" in data:
            text = str(data.get("text", "")).strip()
            return ("Here's what I read:\n" + text[:700]) if text else result.message
        if "returncode" in data and "command" in data:
            stdout = str(data.get("stdout") or "").strip()
            stderr = str(data.get("stderr") or "").strip()
            output = stdout or stderr
            if output:
                return result.message + "\n" + output[:900]
            return result.message
        if data.get("dashboard"):
            board = data["dashboard"]
            return f"Latest run: {board.get('ok_count', 0)} ok, {board.get('failed_count', 0)} failed across {board.get('task_count', 0)} task(s)."
        if data.get("workflow"):
            workflow = data["workflow"]
            if isinstance(workflow, dict):
                return f"Latest workflow: {workflow.get('ok_count', 0)} ok, {workflow.get('failed_count', 0)} failed across {workflow.get('step_count', 0)} step(s)."
        if data.get("autopilot"):
            autopilot = data["autopilot"]
            if isinstance(autopilot, dict):
                return (
                    f"Autopilot {autopilot.get('status')}: "
                    f"{autopilot.get('ok_count', 0)} ok, {autopilot.get('failed_count', 0)} failed, "
                    f"{autopilot.get('blocked_count', 0)} blocked."
                )
        if isinstance(data.get("reminders"), list):
            reminders = data["reminders"]
            if not reminders:
                return result.message
            lines = [
                f"- #{item.get('id')} {item.get('due_at')}: {item.get('message')}"
                for item in reminders[:8]
                if isinstance(item, dict)
            ]
            more = f"\n...and {len(reminders) - 8} more." if len(reminders) > 8 else ""
            return result.message + "\n" + "\n".join(lines) + more
        if "documents" in data and isinstance(data["documents"], list):
            docs = data["documents"]
            if not docs:
                return "Your knowledge base is empty. Index a file to get started."
            return f"You have {len(docs)} document(s) indexed: " + ", ".join(str(d.get("source", "")) for d in docs[:6]) + "."
        if isinstance(data.get("memory"), dict):
            profile = data["memory"].get("profile", {})
            if profile:
                return "Here's what I remember: " + ", ".join(f"{k} = {v}" for k, v in profile.items()) + "."
            return "I don't have anything saved about you yet."
        return result.message

    def _remember(self, expression: str) -> ToolResult:
        if "=" not in expression:
            natural = re.match(r"(?:that\s+)?(?:my\s+)?([\w -]{1,40})\s+is\s+(.+)$", expression.strip(), re.IGNORECASE)
            if natural:
                key = re.sub(r"\s+", "_", natural.group(1).strip().lower())
                value = natural.group(2).strip()
                self.context.memory.set_profile_value(key, value)
                self._mirror_to_vault(f"{key.replace('_', ' ')}: {value}")
                return ToolResult.success(f"Remembered profile value: {key}")
            self.context.memory.add_note(expression.strip())
            self._mirror_to_vault(expression.strip())
            return ToolResult.success("Saved note.")
        key, value = expression.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return ToolResult.failure("Use: remember <key> = <value>")
        self.context.memory.set_profile_value(key, value)
        self._mirror_to_vault(f"{key}: {value}")
        return ToolResult.success(f"Remembered profile value: {key}")

    def _mirror_to_vault(self, text: str) -> None:
        if self.context.obsidian.available():
            try:
                self.context.obsidian.append_memory(text)
            except OSError:
                pass

    def _email_digest(self, query: str = "UNSEEN") -> ToolResult:
        """Read the inbox and summarize it into a short, grouped digest."""
        inbox = self.context.email.search_inbox(query)
        if not inbox.ok:
            return inbox
        messages = inbox.data.get("messages", [])
        if not messages:
            return ToolResult.success("Your inbox is all caught up — no unread messages.")
        # Compact the messages for the model.
        lines = []
        for mail in messages[:25]:
            sender = str(mail.get("from", "")).strip()
            subject = str(mail.get("subject", "")).strip()
            snippet = " ".join(str(mail.get("snippet", "")).split())[:160]
            lines.append(f"- From: {sender} | Subject: {subject} | {snippet}")
        listing = "\n".join(lines)
        # Inbox triage benefits from a stronger model than the fast router — use the
        # smart tier when available so the classification is reliable.
        tier_planner = self.smart_planner or self.planner
        provider = tier_planner.provider if tier_planner else None
        answer = getattr(provider, "answer", None)
        if answer is None:
            # No LLM — fall back to the readable list.
            return ToolResult.success(self._humanize(inbox), messages=messages)
        prompt = (
            f"You are my inbox triage assistant. Here are {len(messages)} emails:\n{listing}\n\n"
            "Classify them into a tight, skimmable Markdown digest. Use these section headers in this order, "
            "omitting any that have no emails:\n"
            "**Needs attention** (time-sensitive or needs a reply/action), **Finance**, **Security & accounts** "
            "(logins, passwords, verification — and flag anything that looks like phishing or a scam), "
            "**Work & job alerts**, **Personal & social**, **Promotions & newsletters**.\n"
            "Under each header, give one bullet per email: sender — subject — why it matters (a few words). "
            "Put every email in exactly one section. Finish with a one-line **Bottom line** of what to handle first."
        )
        digest = answer(prompt, self.context.memory.get_profile(), None, getattr(self, "_history", None))
        if not digest:
            return ToolResult.success(self._humanize(inbox), messages=messages)
        return ToolResult.success(digest, messages=messages, digest=True)

    def _email(self, expression: str) -> ToolResult:
        draft_result = self._parse_email(expression)
        if not draft_result.ok:
            return draft_result
        return self.context.email.open_draft(draft_result.data["draft"])

    @staticmethod
    def _parse_email(expression: str) -> ToolResult:
        lowered = expression.lower()
        if not lowered.startswith("to ") or " subject " not in lowered or " body " not in lowered:
            return ToolResult.failure("Use: email to <addr> subject <subject> body <body>")

        subject_index = lowered.index(" subject ")
        body_index = lowered.index(" body ")
        to = expression[3:subject_index].strip()
        subject = expression[subject_index + len(" subject ") : body_index].strip()
        body = expression[body_index + len(" body ") :].strip()
        if not to or not subject or not body:
            return ToolResult.failure("Email address, subject, and body are required.")
        return ToolResult.success("Parsed email draft.", draft=EmailDraft(to=to, subject=subject, body=body))

    async def _run_many(self, expression: str, retry_of: int | None = None) -> ToolResult:
        commands = [item.strip() for item in expression.split(";;") if item.strip()]
        if not commands:
            return ToolResult.failure("Use: multi <command 1> ;; <command 2>")
        results = await asyncio.gather(*(self._run_tracked_subtask(command) for command in commands), return_exceptions=True)
        payload = []
        records = []
        for index, (command, result) in enumerate(zip(commands, results)):
            if isinstance(result, Exception):
                payload.append({"command": command, "ok": False, "message": str(result), "data": {}})
                records.append(TaskRecord(index=index, command=command, status="failed", message=str(result)))
            else:
                payload.append({"command": command, "ok": result.ok, "message": result.message, "data": result.data})
                records.append(
                    TaskRecord(
                        index=index,
                        command=command,
                        status="ok" if result.ok else "failed",
                        message=result.message,
                    )
                )
        dashboard = self.context.tasks.record_run(records, retry_of=retry_of)
        verb = "Retried" if retry_of is not None else "Ran"
        return ToolResult.success(f"{verb} {len(payload)} subtasks.", results=payload, dashboard=dashboard)

    async def _run_tracked_subtask(self, command: str) -> ToolResult:
        agent_id = self.control_room.start(command)
        try:
            result = await self.handle(command)
        except Exception as exc:
            self.control_room.finish(agent_id, str(exc), ok=False)
            raise
        self.control_room.finish(agent_id, result.message, ok=result.ok)
        return result

    async def _retry_failed_tasks(self) -> ToolResult:
        plan = self.context.tasks.retry_plan()
        commands = [str(command) for command in plan.get("commands", []) if str(command).strip()]
        if not plan.get("run"):
            return ToolResult.failure("No task run to retry. Use 'multi <cmd> ;; <cmd>' first.")
        if not commands:
            return ToolResult.success("No failed subtasks to retry.", retry=plan)
        return await self._run_many(" ;; ".join(commands), retry_of=int(plan["run"]))

    async def _run_workflow(self, expression: str) -> ToolResult:
        commands = [item.strip() for item in expression.split(";;") if item.strip()]
        if not commands:
            return ToolResult.failure("Use: workflow <command 1> ;; <command 2>")
        if any(command.lower().startswith("workflow ") for command in commands):
            return ToolResult.failure("Workflow steps cannot start another workflow.")

        payload = []
        records: list[WorkflowStep] = []
        stopped_at = None
        for index, command in enumerate(commands):
            agent_id = self.control_room.start(command)
            try:
                result = await self.handle(command)
            except Exception as exc:
                self.control_room.finish(agent_id, str(exc), ok=False)
                result = ToolResult.failure(str(exc))
            else:
                self.control_room.finish(agent_id, result.message, ok=result.ok)
            payload.append({"command": command, "ok": result.ok, "message": result.message, "data": result.data})
            records.append(
                WorkflowStep(
                    index=index,
                    command=command,
                    status="ok" if result.ok else "failed",
                    message=result.message,
                )
            )
            if not result.ok:
                stopped_at = index
                for pending_index, pending_command in enumerate(commands[index + 1 :], start=index + 1):
                    records.append(
                        WorkflowStep(
                            index=pending_index,
                            command=pending_command,
                            status="pending",
                            message="Not run because an earlier step failed.",
                        )
                    )
                break

        dashboard = self.context.workflows.record_run(records, stopped_at=stopped_at)
        if stopped_at is None:
            return ToolResult.success(
                f"Workflow completed: {len(records)} step(s) ok.",
                results=payload,
                workflow=dashboard,
            )
        return ToolResult.failure(
            f"Workflow stopped at step {stopped_at + 1}: {records[-1].message}",
            results=payload,
            workflow=dashboard,
        )

    def _workflow_status(self) -> ToolResult:
        dashboard = self.context.workflows.latest()
        if dashboard is None:
            return ToolResult.success("No workflow runs yet. Use 'workflow <cmd> ;; <cmd>'.", workflow=None)
        retry_hint = " Use 'workflow retry failed' to resume from the failed step." if dashboard.get("retry_available") else ""
        return ToolResult.success(
            f"Latest workflow: {dashboard['ok_count']} ok, {dashboard['failed_count']} failed.{retry_hint}",
            workflow=dashboard,
        )

    async def _workflow_retry_failed(self) -> ToolResult:
        commands = self.context.workflows.retry_commands()
        if not self.context.workflows.latest():
            return ToolResult.failure("No workflow run to retry. Use 'workflow <cmd> ;; <cmd>' first.")
        if not commands:
            return ToolResult.success("No failed workflow steps to retry.", workflow=self.context.workflows.latest())
        return await self._run_workflow(" ;; ".join(commands))

    def _agent_control_room(self) -> ToolResult:
        snapshot = self.control_room.snapshot()
        summary = snapshot["summary"]
        return ToolResult.success(
            (
                "Agent control room: "
                f"{summary['working']} working, {summary['idle']} idle, "
                f"{summary['unavailable']} unavailable."
            ),
            control_room=snapshot,
        )

    def _agent_detail(self, raw: str) -> ToolResult:
        agent_id = raw.strip().lower()
        if not agent_id:
            return ToolResult.failure("Use: agent <id>")
        detail = self.control_room.detail(agent_id)
        if detail is None:
            return ToolResult.failure(
                f"No agent named '{agent_id}'.",
                available=[agent["id"] for agent in self.control_room.snapshot()["agents"]],
            )
        lines = [
            f"**{detail['name']}** — {detail['role']}",
            f"Status: {detail['status']} · completed {detail.get('completed', 0)} · failed {detail.get('failed', 0)}",
        ]
        history = detail.get("history", [])
        if history:
            lines.append("\nRecent activity:")
            lines.extend(f"- {'✓' if item.get('ok') else '✗'} {item.get('task', '')}" for item in history[:6])
        else:
            lines.append("\nNo activity yet.")
        return ToolResult.success("\n".join(lines), agent=detail)

    @staticmethod
    def _conversation_fallback(text: str) -> ToolResult:
        return ToolResult.success(
            "I can chat at a basic level in this MVP, but I do not have an LLM provider connected yet. "
            "Use 'help' for tool commands, or plug a model into AgentOrchestrator for open-ended conversation.",
            heard=text,
        )
