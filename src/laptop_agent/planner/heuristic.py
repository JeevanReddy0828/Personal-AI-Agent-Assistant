from __future__ import annotations

import re

from laptop_agent.planner.core import PlanDecision


class HeuristicPlannerProvider:
    def plan(self, text: str, available_commands: str, memory_profile: dict[str, object], history=None) -> PlanDecision:
        del available_commands, memory_profile, history
        raw = text.strip()
        lowered = raw.lower()

        if lowered in {"what can you do", "what can you do?", "commands", "show commands"}:
            return self._command("help", "User asked for available capabilities.", 0.95)

        if "audit" in lowered and any(word in lowered for word in ("show", "open", "recent", "history", "log")):
            return self._command("audit", "User asked to see recent audit history.", 0.9)

        if re.search(r"\b(?:briefing|daily brief|morning brief|status brief|catch me up)\b", lowered):
            return self._command("briefing", "User wants a compact assistant status briefing.", 0.86)

        # Explicit autonomous-agent phrasing wins over keyword fallbacks below, so
        # "autonomously summarize X" runs the loop instead of a one-shot summarize.
        agent = self._agent(raw)
        if agent:
            return agent

        casual = self._casual(lowered)
        if casual:
            return casual

        if any(phrase in lowered for phrase in ("read my screen", "read the screen", "what is on my screen", "what's on my screen", "screen text")):
            return self._command("read screen", "User wants on-screen text captured and read.", 0.85)

        if any(phrase in lowered for phrase in ("look at me", "what do you see", "use the webcam", "use my webcam", "look through the camera", "look at the camera")):
            return self._command("look at webcam", "User wants the webcam captured and described.", 0.84)

        if lowered in {"tasks", "show tasks", "task dashboard", "show task dashboard", "show me the tasks"}:
            return self._command("tasks", "User wants the parallel task dashboard.", 0.85)

        workflow = self._workflow(raw)
        if workflow:
            return workflow

        autopilot = self._autopilot(raw)
        if autopilot:
            return autopilot

        reminder = self._reminder(raw)
        if reminder:
            return reminder

        terminal = self._terminal(raw)
        if terminal:
            return terminal

        remember = self._remember(raw)
        if remember:
            return remember

        email = self._email(raw)
        if email:
            return email

        email_search = self._email_search(raw)
        if email_search:
            return email_search

        email_oauth = self._email_oauth(raw)
        if email_oauth:
            return email_oauth

        ask_file = self._ask_file(raw)
        if ask_file:
            return ask_file

        file_search = self._file_search(raw)
        if file_search:
            return file_search

        file_scan = self._file_scan(raw)
        if file_scan:
            return file_scan

        summarize_file = self._summarize_file(raw)
        if summarize_file:
            return summarize_file

        organize = self._organize(raw)
        if organize:
            return organize

        knowledge = self._knowledge(raw)
        if knowledge:
            return knowledge

        transcribe = self._transcribe(raw)
        if transcribe:
            return transcribe

        ocr = self._ocr(raw)
        if ocr:
            return ocr

        read_file = self._read_file(raw)
        if read_file:
            return read_file

        research_report = self._research_report(raw)
        if research_report:
            return research_report

        research = self._research(raw)
        if research:
            return research

        web_search = self._web_search(raw)
        if web_search:
            return web_search

        open_url = self._open_url(raw)
        if open_url:
            return open_url

        forms = self._forms(raw)
        if forms:
            return forms

        fill_preview = self._fill_preview(raw)
        if fill_preview:
            return fill_preview

        fill_form = self._fill_form(raw)
        if fill_form:
            return fill_form

        music = self._music(raw)
        if music:
            return music

        job = self._job_application(raw)
        if job:
            return job

        if any(phrase in lowered for phrase in ("hello", "hi ", "hey ", "how are you")):
            return PlanDecision(
                action="chat",
                confidence=0.6,
                explanation="Basic conversational greeting.",
                response="I am here and ready. I can help with files, browser tasks, email drafts, music, audit logs, and planned job-application workflows.",
            )

        return PlanDecision(
            action="chat",
            confidence=0.25,
            explanation="No high-confidence tool route found.",
            response="I understand the request, but I do not know the right tool route yet. Try 'help', or phrase it as a command like 'scan files .'",
        )

    def _casual(self, lowered: str) -> PlanDecision | None:
        if re.search(r"\b(file|files|what.*here)\b", lowered) and re.search(r"\b(here|this folder|this directory|current (folder|directory))\b", lowered):
            return self._command("scan files .", "User wants the files in the current folder.", 0.84)
        if re.search(r"\b(summari[sz]e|gist|overview|tl;?dr)\b.*\breadme\b", lowered) or re.search(r"\breadme\b.*\b(summari[sz]e|gist|overview)\b", lowered):
            return self._command("summarize file README.md", "User wants the README summarized.", 0.84)
        if re.search(r"\bwhat\b.*\b(remember|know)\b.*\b(about )?me\b", lowered) or lowered in {"my profile", "show my profile"}:
            return self._command("memory", "User wants to see what is remembered about them.", 0.84)
        if re.search(r"\b(my |the )?task", lowered) and re.search(r"\b(show|list|how|status|recent|going|doing)\b", lowered):
            return self._command("tasks", "User wants the task dashboard.", 0.8)
        if re.search(r"\b(look at|read|see|check|what.?s on|view|describe)\b.*\bscreen\b", lowered) or "my screen" in lowered:
            return self._command("read screen", "User wants the agent to look at the screen.", 0.82)
        return None

    @staticmethod
    def _command(command: str, explanation: str, confidence: float) -> PlanDecision:
        return PlanDecision(action="command", command=command, confidence=confidence, explanation=explanation)

    def _remember(self, text: str) -> PlanDecision | None:
        match = re.search(r"\bremember\s+(?:that\s+)?(?:my\s+)?([\w -]{1,40})\s+(?:is|=)\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        key = re.sub(r"\s+", "_", match.group(1).strip().lower())
        value = match.group(2).strip()
        return self._command(f"remember {key} = {value}", "User wants to store a profile detail.", 0.9)

    def _reminder(self, text: str) -> PlanDecision | None:
        lowered = text.lower().strip()
        if lowered in {"reminders", "show reminders", "list reminders", "my reminders"}:
            return self._command("reminders", "User wants to list active reminders.", 0.86)
        if lowered in {"reminders due", "due reminders", "what reminders are due", "show due reminders"}:
            return self._command("reminders due", "User wants due reminders.", 0.86)
        done = re.search(r"\b(?:complete|finish|mark done|mark complete)\s+reminder\s+#?(\d+)\b", text, re.IGNORECASE)
        if done:
            return self._command(f"reminder done {done.group(1)}", "User wants to complete a reminder.", 0.84)
        add = re.search(
            r"\bremind me\s+(?:to\s+)?(.+?)\s+(?:at|on)\s+(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)$",
            text,
            re.IGNORECASE,
        )
        if add:
            message = add.group(1).strip().strip("'\"")
            due = add.group(2).strip()
            return self._command(f"reminder add {due} {message}", "User wants to create a reminder.", 0.86)
        return None

    def _workflow(self, text: str) -> PlanDecision | None:
        lowered = text.lower().strip()
        if lowered in {"workflow status", "workflow dashboard", "show workflows", "show workflow"}:
            return self._command("workflow status", "User wants the latest workflow dashboard.", 0.85)
        if lowered in {"workflow retry failed", "retry failed workflow", "resume failed workflow"}:
            return self._command("workflow retry failed", "User wants to resume a failed workflow.", 0.85)
        match = re.search(r"\b(?:run|start|execute)\s+(?:a\s+)?workflow\s*[:\-]\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        expression = match.group(1).strip()
        if not expression or ";;" not in expression:
            return None
        return self._command(f"workflow {expression}", "User wants to run a sequential workflow.", 0.82)

    def _autopilot(self, text: str) -> PlanDecision | None:
        lowered = text.lower().strip()
        if lowered in {"autopilot status", "autonomous status", "show autopilot status"}:
            return self._command("autopilot status", "User wants the latest autopilot run.", 0.85)
        match = re.search(r"\b(?:run|start|use)\s+autopilot\s+(?:for|on)?\s*(.+)$", text, re.IGNORECASE)
        if not match:
            match = re.search(r"\bautopilot\s*[:\-]\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        goal = match.group(1).strip().strip("'\"")
        if not goal:
            return None
        return self._command(f"autopilot {goal}", "User wants unattended safe local work.", 0.82)

    def _agent(self, text: str) -> PlanDecision | None:
        lowered = text.lower().strip()
        if lowered in {"agent runs", "agent history", "autonomous runs"}:
            return self._command("agent runs", "User wants the autonomous agent run history.", 0.85)
        if lowered in {"agent last", "agent status"}:
            return self._command("agent last", "User wants the latest autonomous agent run.", 0.85)
        # Explicit "autonomously <goal>" / "agent, <goal>" style triggers only, so normal
        # requests like "do something vague" are never hijacked into a tool-using loop.
        match = re.search(
            r"\b(?:autonomously|on your own|by yourself)\s+(.+)$", text, re.IGNORECASE
        )
        if not match:
            match = re.search(r"\bagent\s*[,:\-]\s+(.+)$", text, re.IGNORECASE)
        if not match:
            match = re.search(r"\b(?:take over|go ahead) and\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        goal = match.group(1).strip().strip("'\"")
        if not goal:
            return None
        return self._command(f"agent run {goal}", "User wants the autonomous agent to pursue a goal.", 0.8)

    def _terminal(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:run|execute)\s+(?:this\s+)?(?:terminal\s+|shell\s+)?command\s*[:\-]?\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            match = re.search(r"\b(?:terminal|shell)\s*[:\-]\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        command = match.group(1).strip().strip("'\"")
        if not command:
            return None
        return self._command(f"run command {command}", "User explicitly asked to run a terminal command.", 0.78)

    def _file_search(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:search|find|look for)\s+(?:files?\s+)?(?:for\s+)?(.+?)\s+(?:in|under|inside)\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        query = match.group(1).strip().strip("'\"")
        root = match.group(2).strip().strip("'\"")
        return self._command(f"search files {query} {root}", "User wants to search text files.", 0.85)

    def _file_scan(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:scan|list|show)\s+files?\s+(?:in|under|inside)?\s*(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        root = match.group(1).strip().strip("'\"") or "."
        return self._command(f"scan files {root}", "User wants to scan a folder.", 0.85)

    def _summarize_file(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:summarize|summarise|tldr|give me a summary of)\s+(?:the\s+)?(?:file\s+|audio\s+|video\s+|image\s+|recording\s+)?(.+\.(?:txt|md|markdown|tex|csv|tsv|pdf|docx|png|jpg|jpeg|gif|bmp|tiff|tif|webp|mp3|wav|m4a|flac|aac|ogg|opus|wma|mp4|mkv|mov|avi|webm|m4v))$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        return self._command(f"summarize file {path}", "User wants an extractive summary of a document or media file.", 0.82)

    def _organize(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:organize|organise|tidy|clean up|sort)\s+(?:the\s+)?(?:folder|directory|files?\s+in)\s+(.+?)(\s+(?:and\s+)?(?:apply|for real|do it))?$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        root = match.group(1).strip().strip("'\"")
        suffix = " apply" if match.group(2) else ""
        return self._command(f"organize folder {root}{suffix}", "User wants files organized by type.", 0.78)

    def _knowledge(self, text: str) -> PlanDecision | None:
        answer_match = re.search(
            r"\b(?:ask|answer from|answer using)\s+(?:my\s+)?(?:knowledge|knowledge base|indexed documents?)\s*(?:about|for|on)?\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if answer_match:
            question = answer_match.group(1).strip().strip("'\"?")
            if question:
                return self._command(f"ask knowledge {question}", "User wants an answer synthesized from indexed knowledge.", 0.82)
        if re.search(r"\b(?:knowledge|indexed documents?)\b", text, re.IGNORECASE) and re.search(
            r"\b(?:stats|status|summary|inventory)\b",
            text,
            re.IGNORECASE,
        ):
            return self._command("knowledge stats", "User wants a knowledge-base inventory.", 0.82)
        export_match = re.search(
            r"\b(?:export|save|write)\s+(?:the\s+)?(?:knowledge|knowledge base|indexed documents?)\s+(?:to|as)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if export_match:
            destination = export_match.group(1).strip().strip("'\"?")
            if destination:
                return self._command(
                    f"knowledge export {destination}",
                    "User wants the knowledge-base inventory written to a file.",
                    0.82,
                )
        index_match = re.search(
            r"\b(?:index|add to knowledge|learn from)\s+(?:the\s+)?(?:file\s+|document\s+)?(.+\.[a-z0-9]{1,5})$",
            text,
            re.IGNORECASE,
        )
        if index_match:
            path = index_match.group(1).strip().strip("'\"")
            return self._command(f"index file {path}", "User wants a file indexed into the knowledge base.", 0.8)
        recall_match = re.search(
            r"\b(?:recall|what do (?:i|we) know about|search (?:my )?(?:knowledge|notes|documents) (?:for|about))\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if recall_match:
            query = recall_match.group(1).strip().strip("'\"?")
            return self._command(f"recall {query}", "User wants to search the indexed knowledge base.", 0.8)
        return None

    def _transcribe(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:transcribe|transcription of|get a transcript of)\s+(?:the\s+)?(?:audio\s+|video\s+|media\s+)?(?:file\s+)?(.+\.(?:mp3|wav|m4a|flac|aac|ogg|opus|wma|mp4|mkv|mov|avi|webm|m4v))$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        return self._command(f"transcribe {path}", "User wants audio/video transcribed to text.", 0.82)

    def _ocr(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:ocr|read text from|extract text from|get text from)\s+(?:the\s+)?(?:image\s+|picture\s+|screenshot\s+)?(?:file\s+)?(.+\.(?:png|jpg|jpeg|gif|bmp|tiff|tif|webp))$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        return self._command(f"ocr image {path}", "User wants text extracted from an image.", 0.82)

    def _read_file(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:read|open text from)\s+(?:file\s+)?(.+\.(?:txt|md|markdown|tex|csv|json|yaml|yml|pdf|docx))$", text, re.IGNORECASE)
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        return self._command(f"read file {path}", "User wants to read a supported document.", 0.8)

    def _ask_file(self, text: str) -> PlanDecision | None:
        extensions = r"(?:txt|md|markdown|tex|csv|json|yaml|yml|pdf|docx)"
        match = re.search(
            rf"\b(?:ask|question)\s+(?:the\s+)?(?:file\s+)?(.+\.{extensions})\s+(?:about|on|for)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            match = re.search(
                rf"\bwhat\s+(?:does|do)\s+(.+\.{extensions})\s+say\s+about\s+(.+)$",
                text,
                re.IGNORECASE,
            )
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        question = match.group(2).strip().strip("'\"?")
        if not path or not question:
            return None
        return self._command(f"ask file {path} about {question}", "User wants a focused answer from a file.", 0.82)

    def _research(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:research|do research on|look into|investigate|read up on)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        topic = match.group(1).strip().strip("'\"?")
        if not topic:
            return None
        return self._command(f"research {topic}", "User wants an autonomous web research workflow.", 0.8)

    def _research_report(self, text: str) -> PlanDecision | None:
        save_match = re.search(
            r"\b(?:save|write)\s+(?:a\s+)?research report\s+(?:on|about|for)\s+(.+?)\s+to\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if save_match:
            topic = save_match.group(1).strip().strip("'\"?")
            destination = save_match.group(2).strip().strip("'\"?")
            if topic and destination:
                return self._command(
                    f"save research report {topic} to {destination}",
                    "User wants a generated research report saved.",
                    0.82,
                )

        match = re.search(
            r"\b(?:research report|write (?:a\s+)?research report|create (?:a\s+)?research report|generate (?:a\s+)?research report)\s+(?:on|about|for)?\s*(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        topic = match.group(1).strip().strip("'\"?")
        if not topic:
            return None
        return self._command(f"research report {topic}", "User wants a written research report.", 0.82)

    def _web_search(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:search the web for|search online for|google|look up|web search(?: for)?|search the internet for)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        query = match.group(1).strip().strip("'\"?")
        query = re.sub(r"\b(?:online|on the web|on the internet)\s*$", "", query, flags=re.IGNORECASE).strip()
        if not query:
            return None
        return self._command(f"web search {query}", "User wants to search the web.", 0.8)

    def _open_url(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:open|go to|browse|visit)\s+(?:url|website|site)?\s*(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)", text, re.IGNORECASE)
        if not match:
            return None
        return self._command(f"open url {match.group(1)}", "User wants to open a website.", 0.85)

    def _forms(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:inspect|scan|check|read)\s+(?:the\s+)?forms?\s+(?:at|on|in)?\s*(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)", text, re.IGNORECASE)
        if not match:
            return None
        return self._command(f"inspect forms {match.group(1)}", "User wants form fields inspected without submitting.", 0.82)

    def _fill_preview(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:preview|prepare|review)\s+(?:a\s+)?(?:form\s+)?fill(?:ing)?\s+(?:for|at|on)?\s*(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        return self._command(f"preview form fill {match.group(1)}", "User wants a no-submit fill preview.", 0.82)

    def _fill_form(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\bfill\s+(?:the\s+)?form\s+(?:for|at|on)?\s*(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        return self._command(f"fill form {match.group(1)}", "User wants approved browser filling without submission.", 0.78)

    def _music(self, text: str) -> PlanDecision | None:
        lowered = text.lower()
        if "pause" in lowered or "resume" in lowered:
            return self._command("media playpause", "User wants media playback toggled.", 0.8)
        if "next song" in lowered or "skip song" in lowered:
            return self._command("media next", "User wants the next media track.", 0.8)
        match = re.search(r"\bplay\s+(?:music\s+)?(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        target = match.group(1).strip().strip("'\"")
        return self._command(f"play music {target}", "User wants to play music from a target.", 0.75)

    def _job_application(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:apply|prepare application)\b.*?(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)", text, re.IGNORECASE)
        if not match:
            return None
        return self._command(f"plan apply job {match.group(1)}", "User wants a job application workflow prepared.", 0.8)

    def _email(self, text: str) -> PlanDecision | None:
        api_provider = self._email_api_provider(text.lower())
        api_match = re.search(
            r"\b(?:draft|write|prepare|send)\s+(?:an\s+)?email\s+(?:in|with|using|through)\s+(gmail|google|outlook|microsoft)\s+to\s+(\S+@\S+)\s+(?:about|subject)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if api_match:
            action_word = re.search(r"\b(send)\b", text, re.IGNORECASE)
            provider = self._email_api_provider(api_match.group(1).lower()) or api_provider or "gmail"
            to = api_match.group(2).strip()
            subject = api_match.group(3).strip()
            body = f"Draft email about: {subject}"
            verb = "send" if action_word else "draft"
            return self._command(
                f"email api {verb} {provider} to {to} subject {subject} body {body}",
                "User wants an OAuth-backed email draft/send workflow.",
                0.65 if verb == "draft" else 0.6,
            )

        match = re.search(
            r"\b(?:draft|write|prepare)\s+(?:an\s+)?email\s+to\s+(\S+@\S+)\s+(?:about|subject)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        to = match.group(1).strip()
        subject = match.group(2).strip()
        body = f"Draft email about: {subject}"
        return self._command(f"email to {to} subject {subject} body {body}", "User wants an email draft.", 0.65)

    def _email_search(self, text: str) -> PlanDecision | None:
        lowered = text.lower()
        api_provider = self._email_api_provider(lowered)
        if re.search(r"\b(?:summari[sz]e|digest|overview|recap|catch me up on|tldr)\b", lowered) and re.search(r"\b(inbox|emails?|mail)\b", lowered):
            return self._command("email digest", "User wants a summary of their inbox.", 0.82)
        if any(phrase in lowered for phrase in ("unread email", "unread emails", "new email", "new emails")):
            if api_provider:
                return self._command(f"email api unread {api_provider}", "User wants unread OAuth-backed mailbox messages.", 0.78)
            return self._command("email unread", "User wants unread inbox messages.", 0.78)
        match = re.search(r"\b(?:search|find|look for)\s+(?:emails?|inbox)\s+(?:for|about)?\s*(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        query = match.group(1).strip().strip("'\"") or "ALL"
        for provider_name in ("gmail", "google", "outlook", "microsoft"):
            query = re.sub(rf"\b(?:in|on|from)\s+{provider_name}\b", "", query, flags=re.IGNORECASE).strip()
        if api_provider:
            return self._command(f"email api search {api_provider} {query}", "User wants OAuth-backed mailbox search.", 0.76)
        return self._command(f"email search {query}", "User wants to search inbox messages.", 0.75)

    def _email_oauth(self, text: str) -> PlanDecision | None:
        lowered = text.lower()
        if "email" in lowered and "token" in lowered and any(word in lowered for word in ("status", "stored", "vault")):
            return self._command("email tokens status", "User wants stored email token status.", 0.76)
        match = re.search(r"\b(?:refresh|renew)\s+(?:the\s+)?(?:email\s+)?(?:oauth\s+)?token\s+(?:for\s+)?(gmail|google|outlook|microsoft)", text, re.IGNORECASE)
        if match:
            return self._command(f"email oauth refresh {match.group(1)}", "User wants to refresh a stored email OAuth token.", 0.76)
        match = re.search(r"\b(?:forget|remove|delete)\s+(?:the\s+)?(?:email\s+)?(?:oauth\s+)?token\s+(?:for\s+)?(gmail|google|outlook|microsoft)", text, re.IGNORECASE)
        if match:
            return self._command(f"email oauth forget {match.group(1)}", "User wants to remove a stored email OAuth token.", 0.75)
        return None

    @staticmethod
    def _email_api_provider(lowered: str) -> str | None:
        if "gmail" in lowered or "google" in lowered:
            return "gmail"
        if "outlook" in lowered or "microsoft mail" in lowered:
            return "outlook"
        return None
