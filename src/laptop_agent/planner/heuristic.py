from __future__ import annotations

import re

from laptop_agent.planner.core import PlanDecision


class HeuristicPlannerProvider:
    def plan(self, text: str, available_commands: str, memory_profile: dict[str, object]) -> PlanDecision:
        del available_commands, memory_profile
        raw = text.strip()
        lowered = raw.lower()

        if lowered in {"what can you do", "what can you do?", "commands", "show commands"}:
            return self._command("help", "User asked for available capabilities.", 0.95)

        if "audit" in lowered and any(word in lowered for word in ("show", "open", "recent", "history", "log")):
            return self._command("audit", "User asked to see recent audit history.", 0.9)

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

        transcribe = self._transcribe(raw)
        if transcribe:
            return transcribe

        ocr = self._ocr(raw)
        if ocr:
            return ocr

        read_file = self._read_file(raw)
        if read_file:
            return read_file

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
            r"\b(?:summarize|summarise|tldr|give me a summary of)\s+(?:the\s+)?(?:file\s+)?(.+\.(?:txt|md|markdown|tex|csv|tsv|pdf|docx))$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        return self._command(f"summarize file {path}", "User wants an extractive document summary.", 0.82)

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
