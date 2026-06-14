from __future__ import annotations

import asyncio
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from laptop_agent.audit import AuditLogger
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.planner import Planner
from laptop_agent.tasks import TaskRecord, TaskTracker
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailDraft, EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.music import MusicTool
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


class AgentOrchestrator:
    def __init__(self, context: AgentContext, planner: Planner | None = None) -> None:
        self.context = context
        self.planner = planner

    async def handle(self, text: str) -> ToolResult:
        command = text.strip()
        lowered = command.lower()
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

        if lowered in {"read screen", "screen text", "what is on my screen", "what's on my screen"}:
            return self._read_screen()

        if lowered in {"tasks", "task dashboard", "show tasks"}:
            dashboard = self.context.tasks.latest()
            if dashboard is None:
                return ToolResult.success("No parallel task runs yet. Use 'multi <cmd> ;; <cmd>'.", dashboard=None)
            return ToolResult.success(
                f"Latest run: {dashboard['ok_count']} ok, {dashboard['failed_count']} failed.",
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

        if lowered.startswith("multi "):
            return await self._run_many(command[len("multi ") :])

        if self.planner is not None:
            planned = self.planner.plan(command, self.help_text(), self.context.memory.get_profile())
            if planned.is_command and planned.command and planned.command.strip().lower() != lowered:
                result = await self.handle(planned.command)
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
                return ToolResult.success(
                    planned.response,
                    planner={
                        "source_text": command,
                        "confidence": planned.confidence,
                        "explanation": planned.explanation,
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
                "  read screen",
                "  index file <path>",
                "  recall <query>",
                "  knowledge list",
                "  knowledge forget <id>",
                "  search files <query> <path>",
                "  web search <query>",
                "  research <topic>",
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
        if suffix not in IMAGE_EXTENSIONS and suffix not in MEDIA_EXTENSIONS:
            return self.context.files.summarize(target)
        extracted = self._extract_text_any(target)
        if not extracted.ok:
            return extracted
        result = self.context.files.summarize_text(str(extracted.data.get("text", "")), source=target)
        if result.ok:
            result.data["extracted_from"] = "ocr" if suffix in IMAGE_EXTENSIONS else "transcription"
        return result

    def _read_screen(self) -> ToolResult:
        handle = tempfile.NamedTemporaryFile(prefix="laptop_agent_screen_", suffix=".png", delete=False)
        handle.close()
        shot = self.context.desktop.screenshot(handle.name)
        if not shot.ok:
            return shot
        ocr = self.context.transcribe.ocr_image(str(shot.data["path"]))
        if not ocr.ok:
            return ToolResult.failure(
                ocr.message,
                screenshot=shot.data.get("path"),
                hint="Screenshot captured, but text extraction needs the OCR extra.",
            )
        return ToolResult.success(
            f"Read {ocr.data.get('char_count', 0)} character(s) of text from the screen.",
            screenshot=shot.data.get("path"),
            text=ocr.data.get("text", ""),
            char_count=ocr.data.get("char_count", 0),
        )

    def _remember(self, expression: str) -> ToolResult:
        if "=" not in expression:
            natural = re.match(r"(?:that\s+)?(?:my\s+)?([\w -]{1,40})\s+is\s+(.+)$", expression.strip(), re.IGNORECASE)
            if natural:
                key = re.sub(r"\s+", "_", natural.group(1).strip().lower())
                value = natural.group(2).strip()
                self.context.memory.set_profile_value(key, value)
                return ToolResult.success(f"Remembered profile value: {key}")
            self.context.memory.add_note(expression.strip())
            return ToolResult.success("Saved note.")
        key, value = expression.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return ToolResult.failure("Use: remember <key> = <value>")
        self.context.memory.set_profile_value(key, value)
        return ToolResult.success(f"Remembered profile value: {key}")

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

    async def _run_many(self, expression: str) -> ToolResult:
        commands = [item.strip() for item in expression.split(";;") if item.strip()]
        if not commands:
            return ToolResult.failure("Use: multi <command 1> ;; <command 2>")
        results = await asyncio.gather(*(self.handle(command) for command in commands), return_exceptions=True)
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
        dashboard = self.context.tasks.record_run(records)
        return ToolResult.success(f"Ran {len(payload)} subtasks.", results=payload, dashboard=dashboard)

    @staticmethod
    def _conversation_fallback(text: str) -> ToolResult:
        return ToolResult.success(
            "I can chat at a basic level in this MVP, but I do not have an LLM provider connected yet. "
            "Use 'help' for tool commands, or plug a model into AgentOrchestrator for open-ended conversation.",
            heard=text,
        )
