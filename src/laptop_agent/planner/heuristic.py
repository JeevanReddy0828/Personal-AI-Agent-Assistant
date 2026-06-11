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

        file_search = self._file_search(raw)
        if file_search:
            return file_search

        file_scan = self._file_scan(raw)
        if file_scan:
            return file_scan

        read_file = self._read_file(raw)
        if read_file:
            return read_file

        open_url = self._open_url(raw)
        if open_url:
            return open_url

        music = self._music(raw)
        if music:
            return music

        job = self._job_application(raw)
        if job:
            return job

        email = self._email(raw)
        if email:
            return email

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

    def _read_file(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:read|summarize|open text from)\s+(?:file\s+)?(.+\.(?:txt|md|markdown|tex|csv|json|yaml|yml|pdf|docx))$", text, re.IGNORECASE)
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        return self._command(f"read file {path}", "User wants to read a supported document.", 0.8)

    def _open_url(self, text: str) -> PlanDecision | None:
        match = re.search(r"\b(?:open|go to|browse|visit)\s+(?:url|website|site)?\s*(https?://\S+|[\w.-]+\.[a-z]{2,}(?:/\S*)?)", text, re.IGNORECASE)
        if not match:
            return None
        return self._command(f"open url {match.group(1)}", "User wants to open a website.", 0.85)

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
