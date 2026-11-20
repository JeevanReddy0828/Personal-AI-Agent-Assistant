"""Understanding a request: the model router, follow-ups, prose and split requests.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from laptop_agent.context import normalize_history, refers_back, topic_of
from laptop_agent.briefing import parse_format
from laptop_agent.planner.core import PlanDecision
from laptop_agent.planner.heuristic import (
    follows_a_file_change,
    asks_to_learn,
    SMALL_TALK,
    asks_to_arrange,
    asks_to_be_told,
    is_diagram_subject,
    asks_not_to_forget,
    is_negated,
    is_plain_question,
    strip_address,
)
from laptop_agent.timeparse import TimeParseError, _find_day, parse_when, spoken_to_digits
from laptop_agent.safety import ApprovalDenied
from laptop_agent.supervisor import Verdict, routed
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.windows import parse_placements
from laptop_agent.tools.calculator import looks_like_arithmetic
from laptop_agent.tools.clock import _requested_zone, asks_the_time, zone_question
from laptop_agent.tools.textcard import wants_text_rendered
from laptop_agent.tools.units import convert_again, looks_like_conversion
from laptop_agent.tools.chance import is_chance_request
from laptop_agent.tools.dates import resolve as resolve_date
from laptop_agent.tools.dates import answerable, until_moment
from laptop_agent.tools.breakdown import breakdown_request
from laptop_agent.tracing import current_trace
from laptop_agent.tools.document import page_target
from laptop_agent.tools.translate import parse_translation
from laptop_agent.agents.common import (
    _AMOUNT_AGAIN,
    _CLOCK_ANSWER,
    _CONVERTED,
    _CONVERT_AGAIN,
    _CURRENCY,
    _DAY_ANSWER,
    _DRAWN,
    _DRAW_AGAIN,
    _GAP_ANSWER,
    _JOINER,
    _KEEP_THEM,
    _NOT_AN_ANSWER,
    _NUMBER_AGAIN,
    _PERCENT_AGAIN,
    _PERCENT_ANSWER,
    _PRIME_ANSWER,
    _REMINDER_CREATION,
    _REQUEST_START,
    _SPLIT_THEM,
    _TIMER_PART,
    _TIME_AGAIN,
    _UNCLEAR_ITEMS,
    _UNIT_WORDS,
    _UNTIL_AGAIN,
    _UNTIL_ANSWER,
    _UNTIL_IN,
    _VERBATIM,
    _WEATHER_ANSWER,
    _WHOLE_ARGUMENT,
    _YES_AND_NO,
    _ZONES_ANSWER,
    _ZONE_AGAIN,
    _has_own_subject,
    _meaningful,
    _short_topic,
)


class Routing:

    def _route(
        self,
        command: str,
        profile: dict[str, object],
        history: list[dict[str, str]] | None = None,
    ):
        help_text = self.help_text()
        trace = current_trace()

        def decided(source: str, decision):
            # Every route passes through the image repair, not just the LLM one: the
            # instant router turns "draw me a picture of this" into `image this`, which is
            # the same defect from the other direction.
            decision = self._repair_image_command(command, decision, history)
            decision = self._repair_target_command(command, decision, history)
            decision = self._repair_diagram_command(command, decision, history)
            decision = self._repair_document_length(command, decision, history)
            if decision.is_command and is_negated(command):
                # "do not open youtube": the model, asked what it means, may still answer
                # `open url …`. A request not to do something never does it (Codex's review
                # of #191); it is answered instead.
                decision = PlanDecision(action="chat", confidence=0.55,
                                        explanation="A request not to do something; answered, not run.")
            elif (decision.is_command and asks_not_to_forget(command)
                  and not _REMINDER_CREATION.match(decision.command or "")):
                decision = PlanDecision(action="chat", confidence=0.55,
                                        explanation="'Don't forget' asks for a reminder, not this.")
            elif (decision.is_command and asks_to_be_told(command)
                  and _REMINDER_CREATION.match(decision.command or "")):
                decision = PlanDecision(action="chat", confidence=0.55,
                                        explanation="'Remind me how/what' asks to be told now.")
            if trace is not None:
                trace.route_done(source)
            return decision

        fast = self.router.plan(command, help_text, profile, history)
        if fast.is_command or self.planner is None:
            return decided("heuristic", fast)
        if type(self.planner.provider).__name__ == "HeuristicPlannerProvider":
            return decided("heuristic", fast)
        # A recent provider failure is already known. Keep the deterministic
        # router's answer and let the chat ladder try a healthy tier instead of
        # spending another request on the same failed endpoint.
        if (
            type(self.planner.provider).__name__ == "OpenAICompatiblePlannerProvider"
            and not self.model_status.should_attempt("fast")
        ):
            return decided("heuristic", fast)
        # When the instant router is already confident this is plain chat (e.g. a
        # greeting), skip the LLM routing round-trip — it would only confirm "this is
        # chat" and then the chat path makes a second LLM call to actually answer.
        # Cutting the redundant classify call roughly halves latency for small talk.
        if fast.is_chat and fast.response and fast.confidence >= 0.6:
            return decided("heuristic", fast)
        # A plain knowledge question needs no model to classify it. Skipping the routing
        # call also skips the ~1800-token command vocabulary that prompt carries, and
        # avoids the failure this replaced: measured on real turns, the router sent
        # ordinary questions to the `solve` research pipeline, where "how is a hash map
        # different from a b-tree index" took 82s and never streamed a token.
        if is_plain_question(command):
            return decided(
                "direct-chat",
                PlanDecision(
                    action="chat",
                    confidence=0.55,
                    explanation="A plain question with nothing to act on; answered without a routing call.",
                ),
            )
        planned = self._repair_shell_command(command, self.planner.plan(command, help_text, profile, history))
        # The model knows `undo file` but not what was just done: after "Reminder set", "undo that"
        # moved an unrelated file back (review of #287). Only straight after a file change of ours.
        if planned.is_command and (planned.command or "").lower().startswith("undo file") and not (
                follows_a_file_change(history)):
            planned = PlanDecision(action="chat", confidence=planned.confidence,
                                   explanation="An undo of a file change that was not the last thing done.")
        if planned.is_command and planned.command:
            # A command a model picked for the user's sentence is checked and recorded like an
            # agent's step; from a tier that is not trusted to act, it is answered, not run.
            verdict = routed(planned.command, "fast")
            self._record_verdict("router", planned.command, "fast", verdict)
            if verdict.verdict is Verdict.REFUSE:
                planned = PlanDecision(action="chat", confidence=planned.confidence,
                                       explanation="Picked by a model that is not trusted to act; answered instead.")
        return decided("llm", planned)

    # An assistant turn that opens with talk about itself describes no topic. Using one gave
    # the user: 'I read "this" as I can't generate or attach images directly...'.
    _META_REPLY = re.compile(r"^\s*(?:i\s+(?:can|cannot|can't|could|will|am|have|read|understand)\b|sorry\b|here is\b|certainly\b)", re.IGNORECASE)

    @classmethod
    def _referent_topic(cls, history: list[dict[str, str]] | None) -> str:
        """What the latest assistant turn was about, in a few words, skipping turns that only
        talk about the assistant."""
        for turn in reversed(history or []):
            if turn.get("role") != "assistant" or not turn.get("text"):
                continue
            topic = topic_of(str(turn["text"]))
            if topic and not cls._META_REPLY.match(topic):
                return topic
        return ""

    # Commands whose argument is a concrete target the user must have named. The router
    # fabricates these: "how do I start the app in a browser tab" came back as
    # `open url http://localhost:3000` — a port nobody mentioned, for a question that only
    # asked how something is done. The approval gate caught it, which is the gate working,
    # but a question should never become an action in the first place.
    _TARGET_COMMANDS = (
        "open url", "download", "read file", "index file", "process file", "summarize file",
        "analyze spreadsheet", "ask file", "extract text", "transcribe", "ocr image",
        "describe image", "describe video", "summarize youtube", "organize folder", "scan files",
        "file move", "file copy", "file rename", "file delete",
    )
    # The part of a target that identifies it: a host without www/TLD, or a filename stem.
    _TARGET_TOKEN = re.compile(r"[A-Za-z0-9_-]{3,}")

    @classmethod
    def _target_tokens(cls, target: str) -> set[str]:
        cleaned = re.sub(r"^[a-z]+://", "", target.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"^www\.", "", cleaned, flags=re.IGNORECASE)
        parts = cls._TARGET_TOKEN.findall(cleaned)
        # A file is identified by its stem, not its extension: "what is a csv file" must not
        # be taken as permission to open data.csv.
        skip = {
            "http", "https", "www", "com", "org", "net", "index", "file",
            "html", "htm", "php", "csv", "tsv", "txt", "pdf", "docx", "doc", "xlsx",
            "md", "json", "yaml", "yml", "toml", "ini", "log", "zip", "png", "jpg",
            "jpeg", "webp", "gif", "mp4", "mp3", "wav", "py", "js", "ts",
        }
        return {p.lower() for p in parts if p.lower() not in skip}

    # Asked for in so many words: a run verb, or a shell to do it in.
    _ASKS_TO_RUN = re.compile(
        r"\b(?:run|execute|exec)\b|\bin\s+(?:the\s+|a\s+)?(?:terminal|shell|powershell|cmd|command\s+prompt|bash)\b",
        re.IGNORECASE,
    )
    # A refusal anywhere; an explanation of a command anywhere unless the request itself
    # starts by asking to run ("run npm install and explain how to run it" - Codex's #193);
    # or a question, read after any courtesy in front of it. Read only at the very start,
    # "please explain how to run npm install" raised an approval card (Codex's review of #191).
    _NOT_RUNNING = re.compile(
        r"\b(?:don'?t|do\s+not|never|without|not|no\s+need\s+to)\s+(?:\w+\s+){0,2}(?:run|execute|exec)\b"
        r"|^(?!\s*(?:run|execute|exec)\b).*?(?:\bwhat\s+(?:does|would|will|did)\b[^.?!]*\bdo\b"
        r"|\bhow\s+(?:do\s+i|do\s+you|to|can\s+i|would\s+i|should\s+i)\s+(?:\w+\s+)?(?:run|execute|exec|use|start)\b)",
        re.IGNORECASE,
    )
    _QUESTION_START = re.compile(r"(?:what|why|how|explain|describe|is|are|does|should|can\s+i|could\s+i)\b",
                                 re.IGNORECASE)
    _COURTESY = re.compile(r"^(?:(?:please|kindly)\b[\s,]*|(?:can|could|would|will)\s+(?:you|u)\b[\s,]*)+",
                           re.IGNORECASE)

    def _repair_shell_command(self, text, planned):
        """A shell command the router wrote for a sentence that never asked for one.

        Measured on the real router: "change my desktop background to blue" became
        `run command in . :: reg add "HKCU\\Control Panel\\Colors" ...`, and no prompt wording
        stopped it without breaking real requests (pair log, 2026-10-05). The approval card
        would still have asked, but one careless click stood between a sentence and the
        registry. Kept when the user asked to run something or typed the command itself.
        """
        command = (planned.command or "") if planned.is_command else ""
        if not command.lower().startswith("run command "):
            return planned
        asked = self._COURTESY.sub("", strip_address(text)).strip()
        if self._NOT_RUNNING.search(asked) or self._QUESTION_START.match(asked):
            # A question about a command, or a request not to run one: answer it, run nothing.
            return PlanDecision(action="chat", confidence=0.55,
                                explanation="A shell command for a question or a refusal; answered instead.")
        typed = re.sub(r"^in\s+.+?\s+::\s+", "", command[len("run command "):].strip(), flags=re.IGNORECASE)
        # The command typed AS the request, not mentioned in it: "I saw git status in a
        # tutorial" raised an approval card for `git status` (Codex's #193).
        said = bool(typed) and asked.rstrip(" ?.!").casefold() == typed.casefold()
        if self._ASKS_TO_RUN.search(text) or said:
            return planned
        return PlanDecision(
            action="chat",
            confidence=0.55,
            explanation=_VERBATIM,
            response=("I didn't do that. It would have taken a shell command you didn't ask for, so "
                      "nothing was run and nothing on this computer changed. To run a command, say "
                      "\"run\" and the command."),
        )

    def _repair_target_command(self, text, planned, history):
        """Refuse a command whose target the user never mentioned.

        "open youtube" legitimately becomes `open url https://www.youtube.com` — the name is
        right there in the request. `open url http://localhost:3000` for a question about
        how to start the app is invented, and running it opens a browser the user did not
        ask for. Anything the user or the conversation actually named still passes.
        """
        command = (planned.command or "") if planned.is_command else ""
        lowered = command.lower()
        prefix = next((p for p in self._TARGET_COMMANDS if lowered.startswith(p + " ")), None)
        if prefix is None:
            return planned
        target = command[len(prefix) :].strip()
        tokens = self._target_tokens(target)
        if not tokens:
            return planned
        spoken = text.lower() + " " + " ".join(
            str(turn.get("text", "")).lower() for turn in (history or [])[-6:]
        )
        if prefix == "transcribe":
            # A shared verb ("record") is not a named file ("record 20s"). Require
            # the whole filename, including its extension, not any stem token.
            name = re.split(r"[/\\]", target.strip('"\''))[-1].lower()
            grounded = bool(name) and re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", spoken)
        else:
            # Its singular names it too: "what did i download today" is about ~/Downloads. Only a
            # longer word, so a short host ("news") is never named by "new", and only as a whole word:
            # "a documentary" does not name ~/Documents (Codex's review of #276).
            grounded = any(token in spoken or (len(token) > 5 and token.endswith("s")
                                               and re.search(rf"\b{re.escape(token[:-1])}\b", spoken))
                           for token in tokens)
        if grounded:
            return planned
        return PlanDecision(
            action="chat",
            confidence=0.55,
            explanation="The command named a target nobody mentioned; answer the question instead.",
        )

    # An explicit ask for a file. Without this, "write up our ERD as a pdf" would be
    # dragged back into the chat reply for containing the word ERD.
    _WANTS_A_FILE = re.compile(
        r"\b(?:pdf|word|docx|markdown|\.md|document|report|write[\s-]?up|file|download|export|save)\b",
        re.IGNORECASE,
    )

    def _repair_diagram_command(self, text, planned, history):
        """A diagram is drawn in the reply, not written to a file.

        The document tool landed after the diagram renderer, and the router started
        preferring it: "draw a flowchart of how a pull request gets merged" produced a
        Markdown file containing a table 3 times out of 4, and a diagram none of the
        time. The user asked for a picture of a process and got a download.

        Only a request that never names a file is redirected, so "write up our ERD as a
        pdf" still writes the pdf.
        """
        command = (planned.command or "") if planned.is_command else ""
        if not command.lower().startswith("document "):
            return planned
        if not is_diagram_subject(text) or self._WANTS_A_FILE.search(text):
            return planned
        return PlanDecision(
            action="chat",
            confidence=0.55,
            explanation="A diagram belongs in the reply as Mermaid, not in a generated file.",
        )

    def _repair_document_length(self, text, planned, history):
        """A length the user asked for survives routing.

        "write a one page pdf on how vaccines work" reached the LLM router, which answered
        `document how vaccines work as pdf`: "one page" was gone, and the PDF ran to two.
        """
        command = (planned.command or "") if planned.is_command else ""
        if not command.lower().startswith("document "):
            return planned
        asked = page_target(text)
        if asked is None or page_target(command) is not None:
            return planned
        length = "one page" if asked == 1 else f"{asked} pages"
        return replace(planned, command=f"document {length}: {command[len('document '):].strip()}")

    def _repair_image_command(self, text, planned, history):
        """The router invents image subjects, and sends diagrams to a diffusion model.

        Both reproduced: after a conversation about TCP congestion control, "create an
        image for this" routed to an entity-relationship diagram of users, orders and
        products — copied from the router's own few-shot example, not the conversation —
        and the picture that came back was unreadable.

        So a technical diagram never reaches image generation, and a back-reference takes
        its subject from the conversation rather than from whatever the router imagined."""
        command = (planned.command or "") if planned.is_command else ""
        if not command.lower().startswith("image "):
            return planned
        subject = command[len("image ") :].strip()
        resolved = False
        # Resolve the subject before judging it: the router's invention says nothing about
        # what the user actually pointed at.
        # "it" is only a back-reference when the USER'S OWN words name no subject. The
        # router's subject is never the test: it fabricates one every time, which is why
        # this guard exists at all. "can you generate an image with the current time on
        # it?" names its subject — the "it" is the image being made — and reading that as
        # a back-reference sent a first-message drawing request to chat, where a freshness
        # keyword web-searched it and the model then asked the user to confirm, twice,
        # before printing its own tool JSON.
        if refers_back(text) and not _has_own_subject(text):
            topic = self._referent_topic(history)
            if not topic:
                return PlanDecision(
                    action="chat",
                    confidence=0.55,
                    explanation="A back-reference with nothing to refer to.",
                )
            subject, resolved = topic, True
        if is_diagram_subject(subject) or is_diagram_subject(text):
            return PlanDecision(
                action="chat",
                confidence=0.55,
                explanation="A technical diagram belongs in the reply, not in a generated picture.",
            )
        # Same principle as the diagram guard, one step further: when the point of the
        # picture IS exact text, a diffusion model cannot spell it. "an image with the
        # current time on it" is drawn locally instead, so the time on it is the time.
        if wants_text_rendered(subject) or wants_text_rendered(text):
            return PlanDecision(
                action="command",
                command=f"timecard {subject}".strip(),
                confidence=0.9,
                explanation="The image is exact text, so draw it rather than generating it.",
            )
        if resolved:
            # A referent is prose, not an image prompt. Handing the sentence "TCP congestion
            # control is a fundamental mechanism that prevents network overload..." to a
            # diffusion model produced a picture of unreadable text. Ask for a concrete
            # prompt, and accept that some ideas simply cannot be drawn.
            prompt = self._visual_prompt(subject)
            if not prompt:
                return PlanDecision(
                    action="chat",
                    confidence=0.6,
                    explanation=_VERBATIM,
                    response=(
                        f"I read \"this\" as {_short_topic(subject)} — which is an idea rather than "
                        "a scene, and a text-to-image model can only draw scenes. It would give you "
                        "shapes that look like a diagram and say nothing.\n\nTell me a concrete "
                        "scene and I will draw it, or ask for a diagram and I will write one out."
                    ),
                )
            return PlanDecision(
                action="command",
                command=f"image {prompt}",
                confidence=planned.confidence,
                explanation="Back-reference resolved from the conversation, not the router.",
            )
        return planned

    def _visual_prompt(self, topic: str) -> str | None:
        """Turn a referent into a short, concrete image prompt, or None if nothing in it can
        be drawn. Injectable through the planner like every other model call, so the
        decision is unit-tested without the network."""
        provider = getattr(self.planner, "provider", None)
        answer = getattr(provider, "answer", None)
        if answer is None:
            return topic
        request = (
            "Rewrite this as a short, concrete prompt for a text-to-image model: a scene, "
            "object or place someone could photograph or illustrate. Maximum 15 words, no "
            "preamble. If it is an abstract idea, protocol, algorithm or process that a "
            "picture cannot meaningfully show, reply with exactly NONE.\n\n" + topic
        )
        try:
            reply = (answer(request, {}, max_tokens=60) or "").strip()
        except Exception:
            return topic
        first = reply.splitlines()[0].strip().strip('"').strip() if reply else ""
        if not first or first.upper().startswith("NONE"):
            return None
        return first[:200]

    # The direct prefixes are a command language - `weather austin`, `schedule daily at 8 ::
    # briefing` - and ordinary English starts sentences with the same words. Driving a
    # conversational corpus through handle() found them colliding: "split $120 between 4
    # people" arranged windows, "schedule a meeting with john tomorrow at 3pm" and "email
    # bob@example.com about lunch tomorrow" were answered with command syntax, "time for a
    # break" failed on a time zone called 'a break'. When the words after the prefix do not
    # fit its grammar, the router decides instead. Only the user's own text is checked: a
    # command a router built still reaches its tool and gets the tool's own usage message.
    # Not the modals: `solve should i use postgres or mysql` is a command, and so is
    # `solve is option b safer for this`.
    _PROSE_OPENERS = frozenset({
        "is", "was", "are", "were", "looks", "seems", "sucks", "shows", "says", "feels", "has",
        "had", "isn't", "wasn't",
    })
    _PROSE_PRONE = frozenset({
        "window", "windows", "split", "snap", "arrange", "schedule", "email", "time", "date", "watch", "unwatch",
        "clock", "weather", "news", "distance", "trip", "download", "forget", "remember", "brightness",
        "wallpaper", "settings", "processes",
        "research", "image", "document", "map", "workflow", "autopilot", "calculate", "calc", "keys",
        "compute", "recall", "agent", "terminal", "shell", "media", "timer", "alarm", "remind", "clipboard", "plugin",
        "translate", "workspace",
    })

    def _reads_as_prose(self, command: str, lowered: str) -> bool:
        verb, _, rest = lowered.partition(" ")
        rest = rest.strip()
        if verb not in self._PROSE_PRONE or not rest:
            return False
        first = rest.split(None, 1)[0]
        if first in self._PROSE_OPENERS:
            return True
        if verb in {"split", "arrange", "snap"}:
            return not asks_to_arrange(command)
        if verb == "windows":
            return not parse_placements(command)
        if verb == "remind":
            return asks_to_be_told(command)
        if verb == "translate":
            return parse_translation(rest) is None
        if verb == "plugin":
            # "plugin installation failed, how do i fix it" is a question: only a sentence naming a
            # loaded plugin is a plugin command (review of #292).
            return first not in self._plugins().names()
        if verb == "clipboard":
            # "clipboard copy paste not working" is a complaint, not text to put on the clipboard.
            words = rest.split()
            return words[0] not in {"copy", "ask"} or len(words) < 2 or words[1] in {
                "paste", "and", "or", "is", "are", "was", "isn't", "not", "doesn't", "does", "history", "manager",
                "for", "permission", "feature", "button", "works", "working", "broken", "keeps"}
        # A typo in the command form ("schedule briefing", "email hello") still gets the
        # tool's usage message; English is recognised by how it goes on.
        if verb == "schedule":
            return "::" not in rest and bool(re.match(
                r"(?:a|an|the|my|our|some|time|lunch|dinner|coffee|drinks|meetings?|calls?"
                r"|appointments?|interviews?)\b", rest))
        if verb == "email":
            if " subject " in rest or re.match(r"(?:digest|search|unread|api|oauth|tokens?)\b", rest):
                return False
            return bool(re.search(r"@|\b(?:about|saying|that|regarding|asking|telling|to say)\b", rest))
        if verb in {"time", "date", "clock"}:
            return not asks_the_time(command) and _requested_zone(rest)[0] is None
        if verb == "distance":
            return not re.search(r"\s(?:to|and)\s|->|→", f" {rest} ")
        if verb == "trip":
            return len([stop for stop in re.split(r"\s+(?:to|then)\s+|->|→|\|", rest) if stop.strip()]) < 2
        if verb == "download":
            return not re.search(r"https?://|www\.|\b[\w-]+\.[a-z]{2,}\b", rest)
        if verb == "forget":
            # "forget the timer" lets go of a timer; it is not a fact called "timer".
            return rest.rstrip(" .!") in {"it", "about it", "that", "this", "it then", "about that", "all that"} or bool(
                re.match(r"(?:about\s+)?(?:the|my|that)\s+(?:[a-z][\w'-]*\s+){0,3}(?:timers?|alarms?|reminders?)\b", rest))
        if verb == "remember":
            # "remember everything I told you" asks to learn from the chat; as a note it saved
            # "everything I told you" (review of #288).
            return asks_to_learn(command) or bool(
                re.match(r"(?:when|what|how|why|who|where|the time|that time|the day|me)\b", rest))
        if verb in {"calculate", "calc", "compute"}:
            # "calculate total revenue by region in sales.csv" is a breakdown, not prose: read as
            # prose it went to the router, came back unchanged and was answered by the chat model.
            return (not looks_like_arithmetic(rest) and len(re.findall(r"[a-z]{3,}", rest)) >= 2
                    and breakdown_request(f"{verb} {rest}") is None)
        if verb == "map":
            return first == "out"
        if verb == "workflow":
            return ";;" not in rest and rest not in {"status", "dashboard", "retry failed"}
        if verb == "media":
            return rest not in {"playpause", "next", "previous", "stop", "volumeup", "volumedown", "mute"} and not (
                re.fullmatch(r"volume \d{1,3}%?", rest))
        if verb == "timer":
            return not re.search(r"(?:\d|\ban?\b|\bhalf\b)\s*(?:" + _UNIT_WORDS + r")\b",
                                 spoken_to_digits(rest))
        if verb == "alarm":
            return not re.search(r"\d|\b(?:noon|midnight|morning|tomorrow|one|two|three|four|five|six|seven"
                                 r"|eight|nine|ten|eleven|twelve)\b", rest)
        return False

    def _command_verbs(self) -> frozenset[str]:
        if self._command_verbs_cache is None:
            self._command_verbs_cache = frozenset(
                entry.strip().split(" ", 1)[0].lower()
                for entry in self._AGENT_COMMANDS
                if entry and not entry.startswith("#")
            )
        return self._command_verbs_cache

    def _unclear_items_answer(self, unclear: re.Match[str], reply: str) -> str | None:
        """The answer to "Add eggs, milk and bread to your shopping list as 3 items? ...", made into
        the list command it means; None when the reply is not an answer to it."""
        name, plain = unclear.group("name"), " ".join(re.sub(r"[,.!]", " ", reply).split())
        if _SPLIT_THEM.fullmatch(plain):
            return f"list {name} add {unclear.group('split')}"
        if _KEEP_THEM.fullmatch(plain):
            said = [item for item in re.split(r"\s+and\s+", unclear.group("said")) if item.strip()]
            return f"list {name} add {', '.join(said)}"
        # "yes, no" says both: not items, and not an answer either.
        if (_YES_AND_NO.fullmatch(plain) or not re.search(r",|\s+and\s+", reply) or len(reply.split()) > 40 or _NOT_AN_ANSWER.match(reply)
                or self.router.plan(reply, "", {}).is_command):
            return None

        def items(text: str) -> list[str]:
            return [item.strip(" .") for item in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", text) if item.strip(" .")]

        # Said again with more separators than before: each piece is an item, joined with commas so
        # the list does not ask about the same words twice. No clearer than before ("eggs milk and
        # bread" once more), the list asks again instead of taking it as a "no" (Codex).
        again = items(reply)
        if len(again) > len(items(unclear.group("said"))):
            return f"list {name} add {', '.join(again)}"
        return f"list {name} add {reply}"

    def _follow_up(self, command: str, history_turns) -> str | None:
        """A short reply to a question this assistant just asked, made into the request it
        completes: "set a timer" -> "How long should the timer run?" -> "10 minutes" used to
        arrive as a bare "10 minutes" that nothing could act on. None when the last turn
        asked nothing, or the reply is a request, a question or a refusal of its own."""
        # The page and the CLI send {"role", "text"}; normalize_history reads either shape.
        # Reading "content" alone passed every unit test and did nothing in the real page.
        turns = normalize_history(history_turns)
        if len(turns) < 2 or turns[-1][0] != "assistant":
            return None
        asked = turns[-1][1]
        before = next((text.rstrip(".!?") for role, text in reversed(turns[:-1]) if role == "user"), "")
        reply = command.strip().rstrip(".!")
        # Ahead of the eight-word limit: tasks said again run longer ("call my mom and pay the rent
        # and buy some milk" - Codex). Ahead of the refusal check too: "no" is an answer here.
        unclear = _UNCLEAR_ITEMS.fullmatch(asked.split("\n", 1)[0].strip())
        if unclear and reply:
            return self._unclear_items_answer(unclear, reply)
        if not reply or len(reply.split()) > 8:
            return None
        # "again" after a coin, a die or a number runs the user's own request once more: "You rolled
        # a **14**." does not say it was a d20.
        if _DRAW_AGAIN.fullmatch(reply) and _DRAWN.fullmatch(asked.split("\n", 1)[0]):
            return next((text for role, text in reversed(turns[:-1]) if role == "user" and is_chance_request(text)),
                        None)
        # Ahead of the refusal check, which reads "what about meters" as a question of its own.
        again = self._asked_again(asked, reply)
        if again is not None:
            return again
        if _NOT_AN_ANSWER.match(reply):
            return None
        routed = self.router.plan(reply, "", {})
        if routed.is_command or routed.explanation == SMALL_TALK:
            return None
        now = datetime.now().astimezone()
        if asked.startswith("How long should the timer run?"):
            return f"timer {reply}" if _TIMER_PART.search(spoken_to_digits(reply)) else None
        alarm = re.match(r"When should the alarm go off(?: (?P<day>[^?]+))?\?", asked)
        if alarm:
            # "at 7" or a bare "7" goes on the day the question named: "... go off tomorrow?" -
            # unless the answer names its own: "Friday at 7" was set for tomorrow (Codex's review).
            when = f"at {reply}" if re.fullmatch(r"\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?", reply, re.IGNORECASE) else reply
            day = "" if _find_day(spoken_to_digits(reply).lower(), now) is not None else alarm.group("day") or ""
            return f"alarm {when} {day}".strip()
        # "create a list called books to read" -> "What should I add to your books to read list?" -> "dune".
        adding = re.match(r"What should I add to your (?P<name>.+?) list\?", asked)
        if adding:
            items = re.sub(r"^(?:add|put)\s+", "", reply, flags=re.IGNORECASE)
            return f"list {adding.group('name')} add {items}" if _meaningful(items) else None
        if asked.startswith("What should I remind you about") and before:
            subject = re.sub(r"^(?:to|that|about)\b\s*", "", reply, flags=re.IGNORECASE)
            return f"{before} to {subject}" if _meaningful(subject) else None
        if asked.startswith("I could not find a time in that.") and before:
            for candidate in (reply, f"at {reply}"):
                try:
                    if parse_when(spoken_to_digits(candidate), now, local=True) is not None:
                        return f"{before} {candidate}"
                except TimeParseError:
                    return None
            return None
        untold = re.match(r"You haven't told me (?:your (?P<key>.+?)|(?P<live>where you live)) yet\.", asked)
        if untold:
            value = re.sub(r"^(?:it'?s|it\s+is|i'?m|i\s+am|i\s+live\s+in|in|call\s+me)\b\s*", "", reply, flags=re.IGNORECASE)
            key = "city" if untold.group("live") else untold.group("key").replace(" ", "_")
            return f"remember {key} = {value}" if _meaningful(value) else None
        undated = re.match(r"I don't know when your (?P<what>.+?) is — ", asked)
        if undated:
            value = re.sub(r"^(?:it'?s|it\s+is)(?:\s+on)?\b\s*|^on\b\s*", "", reply, flags=re.IGNORECASE)
            if not _meaningful(value) or resolve_date(value, now, {}) is None:
                return None
            return f"remember {undated.group('what').replace(' ', '_')} = {value}"
        if asked.startswith("Which one? ") and before:
            ids = re.findall(r"#(\d+)", asked)
            action = self.router.plan(before, "", {}).command or ""
            verb = re.match(r"reminder (?:delete|done|snooze)", action)
            if not ids or not verb:
                return None
            ordinals = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2, "fourth": 3,
                        "4th": 3, "fifth": 4, "5th": 4, "last": -1, "latest": -1}
            spoken = re.sub(r"^(?:the\s+)?(.+?)(?:\s+one)?$", r"\1", reply.lower())
            picked = (ids[ordinals[spoken]] if spoken in ordinals
                      else spoken.lstrip("#") if spoken.lstrip("#") in ids else spoken)
            return f"{verb.group(0)} {picked}"
        return None

    def _asked_again(self, asked: str, reply: str) -> str | None:
        """A computed answer asked again with one thing changed, as the request it means -
        or None. The new request must parse as its own, so "what about dinner" after the time
        is not a time zone called 'dinner'."""
        # The page and the CLI send the turn back with its tool data after a line break.
        head = asked.split("\n", 1)[0]
        converted = _CONVERTED.fullmatch(head)
        amount = _AMOUNT_AGAIN.fullmatch(reply)
        # A sum has the same "X = **Y**" shape, so a conversion that does not parse goes on below.
        if converted and amount:
            candidate = convert_again(converted.group("left"), converted.group("right"), amount.group("n"),
                                      (amount.group("unit") or "").strip())
            if candidate is not None and looks_like_conversion(candidate):
                return candidate
        again = _CONVERT_AGAIN.fullmatch(reply)
        if converted and again:
            # The answer shows thousands with commas, which the parser reads as a decimal point.
            candidate = f"convert {converted.group('left').replace(',', '')} to {again.group('unit')}"
            if looks_like_conversion(candidate):
                return candidate
        again = _ZONE_AGAIN.fullmatch(reply)
        if _CLOCK_ANSWER.match(asked) and again:
            place = again.group("place").strip()
            zone, said = _requested_zone(f"in {place}")
            # The whole place, not a word in it: "the weather in london" names London.
            return f"time in {place}" if zone and said == place.lower() else None
        place = again.group("place").strip() if again else ""
        zones = _ZONES_ANSWER.fullmatch(head)
        if zones:
            # A new time keeps both zones; a new place keeps the time and where it was said.
            at, source = _TIME_AGAIN.fullmatch(reply), zones.group("a") or "here"
            if at:
                candidate = f"convert {at.group('at')} {source} to {zones.group('b') or 'here'}"
            elif place:
                candidate = f"convert {zones.group('at')} {source} to {place}"
            else:
                return None
            # The question reads its zones from the whole phrase, so "the weather in london" is none.
            return candidate if zone_question(candidate) is not None else None
        gap = _GAP_ANSWER.match(head)
        if gap and place:
            candidate = f"how far ahead is {place}" + (f" of {gap.group('b')}" if gap.group("b") else "")
            return candidate if zone_question(candidate) is not None else None
        percent, share = _PERCENT_ANSWER.match(head), _PERCENT_AGAIN.fullmatch(reply)
        if percent and share:
            rate, whole = share.group("p") or percent.group("p"), share.group("of") or percent.group("of")
            candidate = f"what's {rate}% of {whole.replace(',', '')}"
            return candidate if looks_like_arithmetic(candidate) else None
        number = _NUMBER_AGAIN.fullmatch(reply)
        if _PRIME_ANSWER.match(head) and number:
            return f"is {number.group('n').replace(',', '')} prime"
        if _WEATHER_ANSWER.match(head) and place:
            zone, said = _requested_zone(f"in {place}")
            # Only a place the zone table knows by that whole name: "and humidity?" or "what about the
            # weekend?" asks about the forecast above, which the chat model can read.
            return f"weather {place}" if zone and said == place.lower() else None
        holiday = _UNTIL_AGAIN.fullmatch(reply)
        if _DAY_ANSWER.match(head) and holiday:
            candidate = f"when is {holiday.group('what')}"
            return candidate if answerable(candidate, datetime.now().astimezone()) else None
        counted = _UNTIL_ANSWER.fullmatch(head)
        if counted is None:
            return None
        count, name = counted.group("count"), re.sub(r"^your\b", "my", counted.group("name"))
        hours = re.fullmatch(r"([\d,]+) hours?(?: \d+ minutes?)?", count)
        minutes = re.fullmatch(r"([\d,]+) minutes?", count)
        # Counted in a unit the default would not have used: kept for the next thing asked about.
        unit = ("weeks" if "week" in count else "hours" if hours and int(hours.group(1).replace(",", "")) >= 24
                else "minutes" if minutes and int(minutes.group(1).replace(",", "")) >= 60 else "")
        unit_asked = _UNTIL_IN.fullmatch(reply)
        if unit_asked:
            unit, what = unit_asked.group("unit").lower(), name
        elif again := _UNTIL_AGAIN.fullmatch(reply):
            what = again.group("what")
        else:
            return None
        candidate = f"how many {unit} until {what}" if unit else f"how long until {what}"
        now = datetime.now().astimezone()
        if answerable(candidate, now) or until_moment(what, unit, now, self.context.memory.get_profile()) is not None:
            return candidate
        return None

    def _split_requests(self, command: str) -> list[str] | None:
        """Two requests said in one breath, each its own command - or None.

        "set a timer for 5 minutes and remind me to call mom at 6pm" set the timer and
        silently dropped the reminder. A sentence is split only where every part both
        starts like a request and routes to a command on its own, so "remind me to buy milk
        and eggs at 6pm" and "add milk and eggs to my list" stay whole ("eggs at 6pm" is
        not a request), and of the ways to split it the one with the most parts wins.
        """
        joins = list(_JOINER.finditer(command))
        if not joins or len(joins) > 4 or "::" in command or ";;" in command:
            return None
        if command.split(None, 1)[0].lower() in _WHOLE_ARGUMENT:
            return None
        # A briefing format is a list joined by "and": split, "set my briefing to weather and news"
        # saved a briefing of weather alone and then read out the news. So in a briefing change a
        # part that is only a section ("news") belongs to the format - but a real second request
        # ("and set a timer for 10 minutes") still splits off; refusing every split swallowed it.
        changes = ("briefing set ", "briefing add ", "briefing remove ")
        whole = self.router.plan(command, "", {})
        in_format = command.lower().startswith(changes) or whole.is_command and (whole.command or "").startswith(changes)

        def section_only(part: str) -> bool:
            sections, unknown = parse_format(part)
            return bool(sections) and not unknown
        routes: dict[str, bool] = {}

        def routed(part: str) -> bool:
            if part not in routes:
                routes[part] = self.router.plan(part, "", {}).is_command
            return routes[part]

        best: list[str] | None = None
        for mask in range(1, 2 ** len(joins)):
            cuts = [join for bit, join in enumerate(joins) if mask >> bit & 1]
            edges = [0] + [edge for join in cuts for edge in (join.start(), join.end())] + [len(command)]
            parts = [command[edges[i]: edges[i + 1]].strip(" ,") for i in range(0, len(edges), 2)]
            if any(not part for part in parts):
                continue
            if any(not _REQUEST_START.match(part) for part in parts[1:]):
                continue
            if in_format and any(section_only(part) for part in parts[1:]):
                continue
            if all(routed(part) for part in parts) and (best is None or len(parts) > len(best)):
                best = parts
        return best

    async def _run_each(self, parts: list[str], history_turns) -> ToolResult:
        """Each request of a split sentence, handled exactly as if said on its own."""
        results: list[tuple[str, ToolResult]] = []
        for part in parts:
            try:
                result = await self._handle(part, True, history_turns, None, _whole=False)
            except ApprovalDenied as exc:
                result = ToolResult.failure(f"Not approved — {exc}")
            results.append((part, result))
        data: dict[str, object] = {}
        for _part, result in results:
            data.update({key: value for key, value in (result.data or {}).items() if key != "planner"})
        ran = [((result.data or {}).get("planner") or {}).get("planned_command") or part for part, result in results]
        data["parts"] = [{"request": part, "command": command, "ok": result.ok, "message": result.message}
                         for (part, result), command in zip(results, ran)]
        data["planner"] = {"planned_command": " ;; ".join(ran), "source": "split"}
        return ToolResult(ok=all(result.ok for _part, result in results),
                          message="\n\n".join(result.message for _part, result in results), data=data)

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
        # "what's bitcoin at" was answered from the model's memory of a price.
        r"\b(?:bitcoin|btc|ethereum|dogecoin|crypto(?:currency)?|nasdaq|dow\s+jones|s&p\s*500)\b",
        r"\b20(?:2[4-9]|3\d)\b",  # years 2024-2039 (recent/future vs. training cutoff)
        r"\belection\b",
        r"\bwho is (?:the )?president\b",
        r"\brelease date\b",
        r"\bwhen (?:is|does|will|did)\b.*\b(?:release|come out|launch|start|happen)\b",
        r"\bwar\b.*\b(?:end|ended|over|still|update|status|now|going|latest)\b",
        r"\b(?:update|news|latest) on\b",
        # Money in another currency: a rate moves daily and a model only has an old one.
        # "5 pounds to kg" is a weight, and has no currency on its other side.
        rf"\b{_CURRENCY}\b.{{0,30}}\b(?:to|in|into|=)\b.{{0,15}}\b{_CURRENCY}\b",
    )

    # Not questions about the world: how the user feels, the assistant itself, and the
    # user's own day. "i'm feeling sad today", "who are you" and "what's on my calendar
    # today" were each answered from a web search for the sentence, citing whatever came back.
    _NOT_FRESH = re.compile(
        r"^\s*(?:i'?m|i\s+am|i\s+feel|i\s+felt|i'?ve\s+been|i\s+was|i\s+had|i\s+have\s+been|feeling)\b"
        r"|\bwho\s+(?:are|r)\s+(?:you|u)\b|\bwho\s+(?:made|created|built|designed)\s+you\b"
        r"|\bwhat\s+are\s+you\b|\bhow\s+are\s+you\b"
        # About the user's own day: "what did i do today" ran a web search. "what did i miss" may be news.
        r"|\b(?:what|where|when|who)\s+did\s+i\s+(?!miss\b)"
        # "who is my doctor" is about the user, not the news, told or not.
        r"|\bwho(?:'s|s|\s+is|\s+are|\s+was)\s+my\b"
        r"|\bmy\s+(?:calendar|schedule|agenda|day|week|plans?|reminders?|meetings?|appointments?"
        r"|tasks?|to-?dos?|inbox|emails?|notes?|jobs?|resume)\b"
        # News the user is sharing, not asking for. "good news, i got the job" was searched for
        # on the web, and the reply explained that the results did not mention the user's job.
        r"|^\s*(?:(?:that'?s|this\s+is|what|such)\s+)?(?:some\s+|(?:really\s+)?(?:good|great|bad|sad|terrible"
        r"|awful|wonderful|amazing|exciting|big|happy|fantastic|horrible)\s+)news\b\s*(?:$|[,.!:;—–-]|is\b|for\s+you\b)"
        r"|^\s*i(?:'ve|\s+have|\s+got)?(?:\s+got)?\s+(?:some\s+)?(?:big\s+|good\s+|bad\s+|great\s+|exciting\s+)?news\b",
        re.IGNORECASE,
    )

    def _routed_reply(self, result: ToolResult, command: str, planned) -> ToolResult:
        """A routed tool's result in plain language, with what the router made of the sentence."""
        # Formatted locally - instant, with no second network round-trip, so natural-language
        # requests stay fast.
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

    async def _run_routed(self, command: str, history_turns: list[dict[str, str]]) -> ToolResult:
        """Run the command the planner resolved, lighting up the specialist it delegated to so
        the control room reflects the resolved tool, not just the Planner."""
        agent_id = self.control_room.start(command)
        trace = current_trace()
        if trace is not None:
            trace.kind = "command"
            trace.verb = command.strip().split(" ", 1)[0].lower()
            trace.tool_started()
        try:
            # _allow_planner=False stops a planned command from re-triggering the planner,
            # which would let an LLM loop or double-call itself.
            result = await self.handle(command, _allow_planner=False, history=history_turns)
        except BaseException as exc:
            # handle() turns every Exception into a result; what still escapes - Stop, a session
            # that ended, a refused approval - left this specialist working.
            self.control_room.finish(agent_id, str(exc) or "Stopped", ok=False)
            raise
        if trace is not None:
            trace.tool_done()
        self.control_room.finish(agent_id, result.message, ok=result.ok)
        return result
