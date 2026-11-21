"""Research, solving, knowledge, vision, memory, notes and email.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from laptop_agent.access import is_personal
from laptop_agent.agents.common import NL
from laptop_agent.briefing import PROFILE_KEY
from laptop_agent.context import (
    ADVISOR_BUDGET,
    accepts_keyword,
    build_context,
    normalize_history,
    refers_back,
    resolve_reference,
)
from laptop_agent.model_status import BROKEN
from laptop_agent.progress import report_progress
from laptop_agent.safety import ApprovalDenied, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.calculator import looks_like_arithmetic
from laptop_agent.tools.email import EmailDraft
from laptop_agent.tools.learn import LearnTool
from laptop_agent.tools.plugins import PluginTool
from laptop_agent.tools.transcribe import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
from laptop_agent.tools.video import prepare_clip
from laptop_agent.tools.windows import parse_placements


class KnowledgeActions:
    def _advice_research(self, query: str) -> tuple[str, list]:
        """Best-effort web grounding for the advisor: returns (context_text, sources).
        Empty when research is unavailable or the approval gate blocks the lookup."""
        if self.context.research is None:
            return "", []
        try:
            gathered = self.context.research.gather(query)
        except ApprovalDenied:
            return "", []
        if not gathered.ok:
            return "", []
        return str(gathered.data.get("text", "")), list(gathered.data.get("sources", []))

    def _solve(self, problem: str, history: list[dict[str, str]] | None = None) -> ToolResult:
        cleaned = problem.strip().strip("'\"").lstrip("-").strip()
        if not cleaned:
            return ToolResult.failure(
                "Use: solve <problem or decision>  (e.g. 'solve should I rewrite the auth layer now or later')"
            )
        # A sum is not a dilemma. `solve - 67458363*37834872` spent 40s producing framing,
        # options and a recommendation, and never reached 2,552,278,529,434,536.
        if looks_like_arithmetic(cleaned):
            return self._calculator.compute(cleaned)
        agent_id = self.control_room.start(f"advisor: {cleaned}")
        session = build_context(history or [], cleaned, budget=ADVISOR_BUDGET)
        # A follow-up ("is option B safer for this?") is researched in its standalone
        # form ("… (referring to: Postgres vs MySQL)"), not as the bare fragment.
        result = self._problem_solver().solve(cleaned, conversation=session.text, research_query=session.query)
        self.control_room.finish(agent_id, result.analysis[:200], ok=result.ok)
        if not result.ok:
            return ToolResult.failure(result.analysis, problem=cleaned)
        indexed = self.context.knowledge.add(f"advice: {cleaned}", result.analysis)
        return ToolResult.success(
            result.analysis,
            problem=cleaned,
            used_research=result.used_research,
            sources=result.sources,
            indexed=indexed,
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
        sources = gathered.data.get("sources", []) or []
        # The summary was already in `data` and the message announced a count instead.
        headline = f"**{cleaned}** — from {len(sources)} source(s)"
        body = summary.strip() or "No summary could be drawn from the sources."
        cited = NL.join(f"- {str(s)[:110]}" for s in sources[:5])
        return ToolResult.success(
            NL.join([headline, "", body] + (["", "**Sources**", cited] if cited else [])),
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

    @staticmethod
    def _standalone_question(question: str, history: list[dict[str, str]] | None) -> str:
        """A follow-up rewritten to name what it refers to, for retrieval only.

        Every other path already does this — the router, the chat tiers, the agent and
        the advisor all search on `session.query` rather than the bare fragment. The
        knowledge base did not, so "ask knowledge which models does it use" queried the
        index with the pronoun, ranked on "models" alone, and answered out of an NVIDIA
        RAG scrape that says "models" 36 times. The README says "model" 26 times and
        "models" once, so the document that actually answers the question placed nowhere.
        """
        if not history or not refers_back(question):
            return question
        rewritten, _turn = resolve_reference(question, normalize_history(history))
        return rewritten or question

    def _plugins(self) -> PluginTool:
        with self._plugins_lock:
            if self._plugins_cache is None:
                self._plugins_cache = PluginTool(self.context.web.approval_gate, self.data_dir / "plugins")
            return self._plugins_cache

    def _knowledge_answer(self, question: str, history: list[dict[str, str]] | None = None) -> ToolResult:
        cleaned = question.strip().strip("'\"?")
        if not cleaned:
            return ToolResult.failure("Use: ask knowledge <question>")
        answer = self.context.knowledge.answer(cleaned, retrieval_query=self._standalone_question(cleaned, history))
        if not answer.get("ok"):
            return ToolResult.failure(
                f"I could not answer from indexed knowledge: {answer.get('reason', 'no relevant text')}.",
                question=cleaned,
            )
        text = str(answer.get("answer", "")).strip()
        sources = answer.get("sources", [])
        # The answer belongs in the message. _humanize only runs for planner-routed
        # commands, so typing `ask knowledge ...` showed nothing but a count.
        lines = [text] if text else ["I found related material but could not summarise it."]
        if sources:
            lines.append("")
            lines.append("**From:** " + " · ".join(str(s)[:70] for s in sources[:4]))
        return ToolResult.success(
            "\n".join(lines),
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
        why: list[tuple[str, str]] = []
        described = self._describe_with_vision(path, prompt, "Looking at the screen", why)
        if described is not None:
            return ToolResult.success(described, screenshot=path, vision=True)
        return self._read_text_instead(path, "the screen", self._vision_gap(why), screenshot=path)

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
        why: list[tuple[str, str]] = []
        described = self._describe_with_vision(path, prompt, "Looking through the webcam", why)
        if described is not None:
            return ToolResult.success(described, webcam=path, vision=True)
        return self._read_text_instead(path, "the webcam frame", self._vision_gap(why), webcam=path)

    def _describe_image(self, path: str, question: str = "") -> ToolResult:
        target = path.strip().strip("'\"")
        if not target or not Path(target).is_file():
            return ToolResult.failure(f"Image not found: {target}")
        prompt = question.strip() or "Describe this image in detail. What is shown?"
        why: list[tuple[str, str]] = []
        described = self._describe_with_vision(target, prompt, why=why)
        if described is not None:
            return ToolResult.success(described, path=target, vision=True)
        return self._read_text_instead(target, "the image", self._vision_gap(why), path=target)

    def _read_text_instead(self, source: str, what: str, gap: str, **data: object) -> ToolResult:
        """The text in a picture the vision model could not look at, shown rather than counted,
        with the reason it could not look.

        A phone photo answered "Read 239 character(s) of text from the image." and showed none
        of it, and the next one said "no vision model is configured" while one was configured
        and had been retired (HTTP 410). The text goes in `ocr_text`, not `text`: a routed
        reply rebuilds its message from `text`, which would drop the reason again."""
        ocr = self.context.transcribe.ocr_image(source)
        text = str(ocr.data.get("text") or "").strip() if ocr.ok else ""
        if text:
            return ToolResult.success(
                f"I couldn't look at {what} itself ({gap}), so I read the text from {what} instead.\n\n{ocr.message}",
                ocr_text=text,
                vision=False,
                **data,
            )
        if ocr.ok:
            return ToolResult.failure(f"I couldn't look at {what} ({gap}), and it has no readable text.", **data)
        return ToolResult.failure(
            f"I couldn't look at {what} ({gap}), and reading its text failed too: {ocr.message}", **data
        )

    def _vision_gap(self, why: list[tuple[str, str]]) -> str:
        """Why the vision model gave no description, in words the user can act on."""
        if self.vision_planner is None:
            return "no vision model is configured; set OPENAI_VISION_MODEL"
        if not why:
            return "the vision model gave no answer"
        kind, detail = why[-1]
        if kind == BROKEN:
            return f"the vision model cannot be used: {detail}; set OPENAI_VISION_MODEL to a current model"
        return f"the vision model is unavailable right now: {detail}"

    def _describe_video(self, arguments: str) -> ToolResult:
        """`describe video <path> [:: question]` - a clip, watched by the video model."""
        target, _, question = arguments.partition("::")
        target = target.strip().strip("'\"")
        source = Path(target)
        if not target or not source.is_file():
            return ToolResult.failure(f"Video not found: {target}")
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            return ToolResult.failure(f"That isn't a video file I know ({', '.join(sorted(VIDEO_EXTENSIONS))}).")
        describe = getattr(getattr(self.video_planner, "provider", None), "describe_video", None)
        if describe is None:
            return ToolResult.failure(
                "I can't watch video yet: set OPENAI_VIDEO_MODEL to a model that takes a clip "
                "(for example nvidia/nemotron-3.5-super-vl-120b-a12b) and restart."
            )
        with tempfile.TemporaryDirectory(prefix="jarvis_video_") as scratch:
            clip, why = prepare_clip(source, Path(scratch))
            if clip is None:
                return ToolResult.failure(why, path=str(source))
            # The clip leaves this computer for the hosted model, so it is asked about first.
            self.context.web.approval_gate.require(
                ApprovalRequest(
                    action=f"Send a video to the hosted model: {source.name}",
                    risk=RiskLevel.MEDIUM,
                    reason="describe video uploads the clip to the video model's provider.",
                    preview=f"{source.name} ({clip.stat().st_size // 1024} KB)",
                )
            )
            report_progress("Watching the video")
            described = describe(str(clip), question.strip() or "Describe what happens in this video, in order.")
        if described is None:
            return ToolResult.failure(
                "The video model did not answer. It may be busy; try again in a minute.", path=str(source)
            )
        return ToolResult.success(described, path=str(source), vision=True, video=True)

    def _describe_with_vision(
        self, path: str, prompt: str, status: str = "Looking at the image", why: list[tuple[str, str]] | None = None
    ) -> str | None:
        if self.vision_planner is None:
            return None
        describe = getattr(self.vision_planner.provider, "describe_image", None)
        if describe is None:
            return None
        report_progress(status)
        if why is not None and accepts_keyword(describe, "on_failure"):
            return describe(path, prompt, on_failure=lambda kind, detail: why.append((kind, detail)))
        return describe(path, prompt)

    @staticmethod
    def _memory_text(memory: dict) -> str:
        """What is remembered about the user, facts and notes both.

        Notes were saved ("Saved note.") and then never shown: "what do you know about me"
        answered "I don't have anything saved about you yet" straight after "remember that
        I parked on level 3".
        """
        profile = memory.get("profile") or {}
        notes = [str(note) for note in (memory.get("notes") or []) if str(note).strip()]
        if not profile and not notes:
            return "I don't have anything saved about you yet."
        parts = []
        # One run-on "k = v, k = v, …" sentence stopped being readable the moment there
        # were more than a few facts, and it is read aloud in voice mode too.
        if len(profile) > 3 or (profile and notes):
            lines = [f"- **{k.replace('_', ' ')}** — {v}" for k, v in sorted(profile.items())]
            parts.append("Here's what I remember about you:\n" + "\n".join(lines))
        elif profile:
            parts.append(
                "Here's what I remember: "
                + ", ".join(f"your {k.replace('_', ' ')} is {v}" for k, v in sorted(profile.items()))
                + "."
            )
        if notes:
            shown = notes[-12:]
            parts.append(
                "Things you asked me to remember:\n"
                + "\n".join(f"- {note}" for note in shown)
                + (f"\n…and {len(notes) - len(shown)} older." if len(notes) > len(shown) else "")
            )
        return "\n\n".join(parts)

    def _facts(self) -> dict[str, object]:
        """The profile as the chat model sees it, with the user's notes folded in.

        The model was given the profile alone, so "where did I park" after "remember that I
        parked on level 3" was answered by a model that had never been told.
        """
        facts: dict[str, object] = dict(self.context.memory.get_profile())
        notes = [str(note) for note in (self.context.memory.dump().get("notes") or []) if str(note).strip()]
        if notes:
            facts["things_the_user_asked_me_to_remember"] = " | ".join(notes[-15:])[-1500:]
        return facts

    @staticmethod
    def _briefing_by_remember(key: str) -> ToolResult | None:
        """A personal account may remember anything but the briefing's format, which `briefing set`
        refuses it: the fact is the format, so remembering it was the same change by another door."""
        if is_personal() and re.sub(r"[^a-z]+", " ", key.lower()).strip() == PROFILE_KEY:
            return ToolResult.failure("Changing the briefing needs a developer account.")
        return None

    def _learn(self) -> LearnTool:
        if self._learn_cache is None:

            def save(facts: dict[str, str]) -> None:
                self.context.memory.set_profile_values(facts)
                self._mirror_to_vault("\n".join(f"{key.replace('_', ' ')}: {value}" for key, value in facts.items()))

            # A short JSON extraction: the tier ladder (smart, fast, OpenRouter) and a small budget,
            # not the document writer's smart-only 6,000 thinking tokens.
            # A personal account cannot set the briefing's format here either: it is `briefing set`'s.
            self._learn_cache = LearnTool(
                self._build_agent_brain(answer_max_tokens=1200),
                self.context.web.approval_gate,
                save,
                allowed=lambda key: self._briefing_by_remember(key) is None,
            )
        return self._learn_cache

    def _remember(self, expression: str) -> ToolResult:
        # "remember that I parked on level 3" is a note about parking, not "that I parked...".
        text = re.sub(r"^\s*that\s+", "", expression.strip(), flags=re.IGNORECASE).strip().rstrip(".!")
        if "=" not in text:
            # The key may carry an apostrophe: "my wife's birthday is june 5" missed this and
            # became an anonymous note, so "when is my wife's birthday" had no fact to find.
            natural = re.match(r"(?:my\s+|the\s+)?([\w' -]{1,40}?)\s+(?:is|are)\s+(.+)$", text, re.IGNORECASE)
            if natural and not re.match(
                r"(?:i|we|you|he|she|they|it|there|this|that)\b", natural.group(1), re.IGNORECASE
            ):
                key = re.sub(r"\s+", "_", natural.group(1).strip().lower())
                value = natural.group(2).strip()
                if refused := self._briefing_by_remember(key):
                    return refused
                self.context.memory.set_profile_value(key, value)
                self._mirror_to_vault(f"{key.replace('_', ' ')}: {value}")
                return ToolResult.success(f"Got it — your {key.replace('_', ' ')} is {value}.", remembered=key)
            if not text:
                return ToolResult.failure("What should I remember?")
            self.context.memory.add_note(text)
            self._mirror_to_vault(text)
            return ToolResult.success(f"Got it, I'll remember that: {text}", note=text)
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            return ToolResult.failure("Use: remember <key> = <value>")
        if refused := self._briefing_by_remember(key):
            return refused
        self.context.memory.set_profile_value(key, value)
        self._mirror_to_vault(f"{key}: {value}")
        return ToolResult.success(f"Got it — your {key.replace('_', ' ')} is {value}.", remembered=key)

    def _forget(self, raw: str) -> ToolResult:
        key = raw.strip().strip("'\"?.")
        for prefix in ("my ", "that ", "about me ", "the "):
            if key.lower().startswith(prefix):
                key = key[len(prefix) :].strip()
        if not key:
            return ToolResult.failure("Use: forget <key>  (for example: forget favourite editor)")
        # "forget my last note", "delete the last thing i told you".
        if re.fullmatch(
            r"(?:last|latest|newest|most\s+recent)\s+(?:note|thing(?:\s+i\s+(?:told|said)(?:\s+you)?)?)",
            key,
            re.IGNORECASE,
        ):
            last = self.context.memory.forget_last_note()
            return (
                ToolResult.success(f"Forgotten: {last}", forgot=[last])
                if last is not None
                else ToolResult.failure("There are no notes to forget.")
            )
        removed = self.context.memory.forget_profile_value(key)
        if removed is not None:
            return ToolResult.success(f"Forgotten: {removed}", forgot=removed)
        # A note is forgotten by its words: "forget that I parked on level 3".
        dropped = self.context.memory.forget_notes(key)
        if dropped:
            return ToolResult.success("Forgotten: " + "; ".join(dropped), forgot=dropped)
        profile = self.context.memory.get_profile()
        if not profile:
            return ToolResult.failure("There is nothing saved about you yet.")
        # Name what is actually there rather than just refusing — the stored key is often
        # not spelled the way it gets asked for.
        return ToolResult.failure(
            f"I have nothing saved under '{key}'. I do know: " + ", ".join(sorted(profile)) + ".",
            profile=profile,
        )

    def _arrange_windows(self, command: str) -> ToolResult:
        """ "put WhatsApp on the left and Chrome on the right" -> two placements.

        The phrasing is parsed here rather than by the router because it arrives by voice:
        said out loud it came out as "left side WhatsApp right side Chrome", with the
        position before the name and no conjunction between the two halves.
        """
        placements = parse_placements(command)
        if not placements:
            return ToolResult.failure(
                "Tell me which window and where — for example: put WhatsApp on the left "
                "and Chrome on the right. I can use left, right, top, bottom, the four "
                "corners, thirds, centre or full screen. Say 'windows' to see what is open."
            )
        return self.context.windows.arrange(placements)

    def _mirror_to_vault(self, text: str) -> None:
        if self.context.obsidian.available():
            try:
                self.context.obsidian.append_memory(text)
            except OSError:
                pass

    def _ask_vault(self, question: str) -> ToolResult:
        """Answer a question from the Obsidian vault using link-aware retrieval: pull
        the best note plus its linked neighbours, then ground the model on that bundle.
        Falls back to returning the assembled context when no model is available."""
        question = question.strip()
        if not question:
            return ToolResult.failure("Use: ask vault <question>")
        bundle = self.context.obsidian.context_for(question)
        if not bundle.ok:
            return bundle
        notes = list(bundle.data.get("notes", []))
        context = str(bundle.data.get("context", ""))
        prompt = (
            "Answer the question using ONLY the vault notes below. Cite note titles in "
            "square brackets like [Note]. If the notes don't contain the answer, say so plainly.\n\n"
            f"NOTES:\n{context}\n\nQUESTION: {question}\n\nAnswer:"
        )
        answer = self._build_agent_brain()(prompt).strip()
        if not answer:  # no LLM configured — hand back the linked context itself
            return ToolResult.success(
                f"From {len(notes)} linked note(s) ({', '.join(notes)}):\n\n{context}",
                question=question,
                notes=notes,
                answered=False,
            )
        return ToolResult.success(
            answer, question=question, notes=notes, primary=bundle.data.get("primary"), answered=True
        )

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
