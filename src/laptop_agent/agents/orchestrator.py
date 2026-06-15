from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from laptop_agent.agents.control_room import AgentControlRoom
from laptop_agent.audit import AuditLogger
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.planner import HeuristicPlannerProvider, Planner
from laptop_agent.tasks import TaskRecord, TaskTracker
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailDraft, EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.obsidian import ObsidianVault
from laptop_agent.tools.research import ResearchTool
from laptop_agent.tools.transcribe import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, TranscribeTool
from laptop_agent.tools.web import WebTool
from laptop_agent.tools.websearch import WebSearchTool


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
    transcribe: TranscribeTool
    audit: AuditLogger
    tasks: TaskTracker
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
        # Fast deterministic router tried before any LLM, so common requests
        # route instantly and reliably with zero network latency.
        self.router = Planner(HeuristicPlannerProvider())

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
        return self.planner.plan(command, help_text, profile, history)

    async def handle(
        self,
        text: str,
        _allow_planner: bool = True,
        history: list[dict[str, str]] | None = None,
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

        if lowered in {"agents", "agent control", "control room", "agent dashboard"}:
            return self._agent_control_room()

        if lowered.startswith("agent "):
            return self._agent_detail(command[len("agent ") :].strip())

        if lowered.startswith("scan files "):
            return self.context.files.scan(command[len("scan files ") :].strip() or ".")

        if lowered.startswith("read file "):
            return self.context.files.read_text(command[len("read file ") :].strip())

        if lowered.startswith("summarize file "):
            return self._summarize_any(command[len("summarize file ") :].strip())

        if lowered.startswith("extract text "):
            return self._extract_text_any(command[len("extract text ") :].strip())

        if lowered.startswith("index file "):
            return self._index_file(command[len("index file ") :].strip())

        if lowered in {"knowledge list", "knowledge"}:
            documents = self.context.knowledge.list_documents()
            return ToolResult.success(f"{len(documents)} document(s) indexed.", documents=documents)

        if lowered.startswith("knowledge forget "):
            return self._knowledge_forget(command[len("knowledge forget ") :].strip())

        if lowered in {"knowledge clear", "forget knowledge"}:
            removed = self.context.knowledge.clear()
            return ToolResult.success(f"Cleared {removed} indexed document(s).", removed=removed)

        if lowered.startswith("knowledge search "):
            return self._knowledge_search(command[len("knowledge search ") :].strip())

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

        if lowered in {"tasks", "task dashboard", "show tasks"}:
            dashboard = self.context.tasks.latest()
            if dashboard is None:
                return ToolResult.success("No parallel task runs yet. Use 'multi <cmd> ;; <cmd>'.", dashboard=None)
            retry_hint = " Use 'multi retry failed' to rerun failed subtasks." if dashboard.get("retry_available") else ""
            return ToolResult.success(
                f"Latest run: {dashboard['ok_count']} ok, {dashboard['failed_count']} failed.{retry_hint}",
                dashboard=dashboard,
            )

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

        if lowered.startswith("play music "):
            return self.context.music.play(command[len("play music ") :].strip())

        if lowered.startswith("media "):
            return self.context.music.media_key(command[len("media ") :].strip())

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
                response = planned.response
                model_used = "fast"
                # Escalate by task complexity: fast -> smart -> ultra.
                tier_planner, tier_label = self._pick_chat_model(self._complexity(command))
                if tier_planner is not None:
                    answer = getattr(tier_planner.provider, "answer", None)
                    if answer is not None:
                        better = answer(command, self.context.memory.get_profile(), None, history_turns)
                        if better:
                            response = better
                            model_used = tier_label
                return ToolResult.success(
                    response,
                    planner={
                        "source_text": command,
                        "confidence": planned.confidence,
                        "explanation": planned.explanation,
                        "model": model_used,
                    },
                )

        return self._conversation_fallback(command)

    @staticmethod
    def help_text() -> str:
        return "\n".join(
            [
                "Commands:",
                "  remember <key> = <value>",
                "  memory",
                "  audit",
                "  agents | agent <id>",
                "  scan files <path>",
                "  read file <path>",
                "  summarize file <path>  (text, PDF, DOCX, images, audio, video)",
                "  extract text <path>",
                "  file info <path>",
                "  extract tables <path>",
                "  convert file <source> to <destination>",
                "  organize folder <path> [apply]",
                "  ocr image <path>",
                "  transcribe <audio-or-video-path>",
                "  read screen [question]   (vision: looks at your screen)",
                "  describe image <path>    (vision)",
                "  index file <path>",
                "  recall <query>",
                "  knowledge list",
                "  knowledge forget <id>",
                "  notes status | notes list | notes search <query>",
                "  read note <name> | save note <title> : <body>",
                "  remember note <text>",
                "  search files <query> <path>",
                "  web search <query>",
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
                "  play music <file-folder-or-url>",
                "  media playpause|next|previous|stop",
                "  email search <query>",
                "  email unread",
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
            ]
        )

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
        if "text" in data and "char_count" in data:
            text = str(data.get("text", "")).strip()
            return ("Here's what I read:\n" + text[:700]) if text else result.message
        if data.get("dashboard"):
            board = data["dashboard"]
            return f"Latest run: {board.get('ok_count', 0)} ok, {board.get('failed_count', 0)} failed across {board.get('task_count', 0)} task(s)."
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
