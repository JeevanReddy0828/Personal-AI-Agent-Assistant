from __future__ import annotations

from laptop_agent.cancellation import check_cancelled, OperationCancelled

import asyncio
import difflib
import errno
import html
import hashlib
import json
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

NL = chr(10)

from laptop_agent.advisor import ProblemSolver
from laptop_agent.agents.control_room import AgentControlRoom
from laptop_agent.audit import AuditLogger
from laptop_agent.autopilot import AutopilotPlanner, AutopilotStep, AutopilotTracker, parse_autopilot_steps
from laptop_agent.context import (
    ADVISOR_BUDGET,
    AGENT_BUDGET,
    accepts_context_query,
    accepts_keyword,
    build_context,
    context_block,
    normalize_history,
    refers_back,
    register_summarizer,
    resolve_reference,
    topic_of,
)
from laptop_agent.copilot import JobCopilot, ats_score, extract_keywords
from laptop_agent.jobs import JobTracker, normalize_stage
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore, list_name
from laptop_agent.metrics import battery_status, system_metrics
from laptop_agent.model_status import ModelStatus
from laptop_agent.planner import HeuristicPlannerProvider, Planner
from laptop_agent.planner.core import PlanDecision
from laptop_agent.planner.heuristic import (
    SMALL_TALK,
    fact_question,
    is_diagram_subject,
    is_plain_question,
    nameless_list_edit,
)
from laptop_agent.reasoning import AgentRunTracker, AutonomousAgent
from laptop_agent.reminders import ReminderStore
from laptop_agent.timeparse import TimeParseError, describe, parse_when, spoken_to_digits
from laptop_agent.safety import ApprovalDenied, ApprovalRequest, RiskLevel
from laptop_agent.scheduler import ScheduleError, SchedulerStore, parse_days, parse_schedule
from laptop_agent.tasks import TaskRecord, TaskTracker
from laptop_agent.tools.base import ToolResult, reserve_new_path
from laptop_agent.tools.windows import WindowTool, parse_placements
from laptop_agent.failures import FAILURES, record_failure
from laptop_agent.tools.calculator import CalculatorTool, looks_like_arithmetic
from laptop_agent.tools.clock import ClockTool, _requested_zone, asks_the_time, prompt_stamp
from laptop_agent.tools.textcard import wants_text_rendered
from laptop_agent.tools.units import UnitTool, looks_like_conversion
from laptop_agent.tools.chance import draw
from laptop_agent.tools.dates import RELATIVE_DAYS, date_question, describe_day, resolve as resolve_date
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailDraft, EmailTool
from laptop_agent.tools.file_processor import FileProcessor
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.jobright import JobrightTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.obsidian import ObsidianVault
from laptop_agent.tools.research import ResearchTool
from laptop_agent.tools.terminal import TerminalTool
from laptop_agent.tools.transcribe import IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, TranscribeTool
from laptop_agent.tools.travel import TravelTool
from laptop_agent.config import load_config
from laptop_agent.tracing import TraceStore, TurnTrace, begin_trace, current_trace, end_trace
from laptop_agent.tools.document import DocumentTool
from laptop_agent.tools.imagegen import ImageTool
from laptop_agent.tools.news import NewsTool
from laptop_agent.tools.weather import WeatherTool, clean_place
from laptop_agent.tools.web import WebTool
from laptop_agent.tools.webcam import WebcamTool
from laptop_agent.tools.websearch import WebSearchTool
from laptop_agent.tools.youtube import YouTubeTool
from laptop_agent.workflows import WorkflowStep, WorkflowTracker


@dataclass(frozen=True)
class AgentContext:
    memory: MemoryStore
    files: FileTool
    web: WebTool
    websearch: WebSearchTool
    browser: BrowserAutomationTool
    desktop: DesktopTool
    windows: WindowTool
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
    jobs: JobTracker
    jobright: JobrightTool


# Marks a decision whose text is ours, not the model's, so the chat ladder cannot
# replace it. The model, asked to answer this itself, claimed the app cannot generate
# images at all — which is wrong, and worse than the nonsense it replaced.
_VERBATIM = "verbatim-reply"


def _short_topic(text: str, words: int = 8) -> str:
    """A few words of a referent, for quoting back without repeating a paragraph."""
    parts = (text or "").strip().rstrip(".").split()
    return " ".join(parts[:words]) + ("..." if len(parts) > words else "")




# Words that carry no subject on their own. A request built only from these is pointing
# at something earlier; one with a real noun in it is describing what to draw.
_SUBJECT_FILLER = {
    "it", "this", "that", "these", "those", "them", "one", "ones", "thing", "stuff",
    "the", "a", "an", "of", "for", "on", "in", "with", "about", "from", "my", "your",
    "me", "you", "please", "again", "same", "above", "below", "here", "there",
    "image", "picture", "photo", "drawing", "pic", "and", "to", "as", "like", "show",
    "can", "could", "would", "will", "make", "draw", "create", "generate", "give",
    "render", "produce", "another", "new", "some", "based",
}


def _has_own_subject(text: str) -> bool:
    """Whether the user's own words name something to draw, rather than point at it."""
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    return any(word not in _SUBJECT_FILLER for word in words)


# What a turn with no model to answer it says, when none is configured at all.
_NO_MODEL_REPLY = (
    "I can't answer open questions yet — no language model is connected. I can still set "
    "reminders, timers and alarms, keep your lists, check the weather and the news, do exact "
    "maths, tell the time anywhere, and work with your files. Add an OPENAI_API_KEY to .env to "
    "talk about anything (see the README)."
)


# The longest thing a person types in a chat box. An attached file is the right home for
# anything bigger, and the file tools read it without pushing it through a model prompt.
MAX_COMMAND_CHARS = 24_000


def _reminder_message(text: str, start: int, end: int) -> str:
    """What is left of a reminder once the time words are cut out of it.

    "to call mom at 6pm" -> "call mom". The span comes from the parser rather than a
    guess, so a time in the middle ("call mom at 6pm about the invoice") loses only the
    time and keeps both halves of the sentence.
    """
    joined = re.sub(r"\s+", " ", text[:start] + " " + text[end:]).strip(" ,.;:-")
    joined = re.sub(r"^(?:to|that|about|for|me\s+to)\s+", "", joined, flags=re.IGNORECASE)
    joined = re.sub(r"\s+(?:at|on|by|around|about|this|next)$", "", joined, flags=re.IGNORECASE)
    return joined.strip(" ,.;:-")


# Disfluencies a dictated reminder arrives with: "uh remind me to uh call mom at six" was
# filed as "uh call mom", with no time at all because "six" was a word.
_FILLERS = re.compile(r"\b(?:u+h+|u+m+|uhm+|erm+|hmm+)\b[,.]?\s*", re.IGNORECASE)


def _spoken_request(expression: str) -> str:
    """A reminder as said out loud, made readable: fillers out, spoken numbers as digits,
    and a doubled letter in a hurried "6ppm" or "7amm" read as the time it plainly is."""
    cleaned = _FILLERS.sub("", (expression or "").strip().strip("'\"")).strip()
    cleaned = re.sub(r"(?<=\d)(\s*)(?:p{2,}m+|pm{2,}|a{2,}m+|am{2,})(?![a-z])",
                     lambda m: m.group(1) + ("pm" if m.group(0).strip()[:1].lower() == "p" else "am"),
                     cleaned, flags=re.IGNORECASE)
    return spoken_to_digits(cleaned)


_CURRENCY = (r"(?:usd|eur|gbp|inr|jpy|cny|rmb|cad|aud|chf|mxn|aed|sgd|nzd|hkd|krw|brl|zar|dollars?|bucks|euros?"
             r"|rupees?|yen|yuan|pesos?|dirhams?|francs?|pounds?|reais|ringgit|baht|rand|bitcoins?|btc|ethereum|eth)")

# Where one request ends and the next begins, in speech.
_JOINER = re.compile(r"\s*,?\s+(?:and\s+then|and\s+also|and|then)\s+", re.IGNORECASE)
# A second request in a sentence starts with its own verb or question word; "hotels in
# paris" after "search for flights and" does not, and is the first request's object.
_REQUEST_START = re.compile(
    r"\s*(?:please\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|will\s+you\s+)?(?:set|remind|add|put|play|pause|"
    r"stop|turn|open|launch|start|what(?:'s|s)?|how(?:'s|s)?|when(?:'s|s)?|where(?:'s|s)?|who(?:'s|s)?|is|are|do|does|"
    r"tell|show|read|check|search|find|look|email|send|wake|cancel|delete|remove|clear|snooze|take|make|create|"
    r"draw|write|convert|calculate|give|get|schedule|book|flip|roll|pick|mute|unmute|skip|resume|volume|lower|"
    r"raise|increase|decrease|remember|forget|note|download|summari[sz]e|translate|define|change|update|"
    r"weather|news|mark|list|go|navigate|close|save|run)\b",
    re.IGNORECASE,
)
# A reply that is not an answer to the question just asked: a refusal, or a question of
# its own. A lone "Will" is a name, so the modals only count with words after them.
_NOT_AN_ANSWER = re.compile(
    r"\s*(?:no|nope|nah|not|never|skip|cancel|stop|nevermind|idk|none|nothing|hm+|um+|uh+)\b"
    r"|\s*(?:i\s+don'?t|don'?t|forget|why|what|how|who|when|where|which|can|could|would|will|is|are|do|does)\s+\w",
    re.IGNORECASE,
)
_FILLER_REPLY = frozenset({"it's", "its", "it", "is", "the", "a", "an", "my", "to", "at", "in", "on", "of", "that",
                           "this", "about", "for", "and", "so", "well", "ok", "okay", "yes", "yeah", "sure"})


def _meaningful(value: str) -> bool:
    """Whether a reply carries an answer: "it's" and "to" alone do not - they were filed as
    a name and as a reminder."""
    words = re.findall(r"[\w'-]+", value.lower())
    return bool(words) and any(word not in _FILLER_REPLY for word in words)


# Direct commands whose argument is free text: an "and" inside it belongs to it.
_WHOLE_ARGUMENT = frozenset({
    "email", "send", "remember", "note", "document", "image", "research", "solve", "ask", "agent", "autopilot",
    "workflow", "multi", "schedule", "run", "terminal", "shell", "write", "draft", "summarize", "translate",
})
_HOW_LONG_LEFT = re.compile(r"(?:how\s+much\s+longer|how\s+much\s+time(?:\s+is)?\s+left|how\s+long\s+(?:is\s+)?left"
                            r"|time\s+left|how\s+long\s+to\s+go)[\s?.!]*")

# "shopping list", "the grocery list" - a list named on its own.
_BARE_LIST = re.compile(r"(?:(?:show|read|open|check)\s+(?:me\s+)?)?(?:(?:my|the|our)\s+)?"
                        r"(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*)?)\s+list[\s?.!]*")
# A list asked for without its name.
_WHICH_LIST = re.compile(
    r"(?:what(?:'s|s|\s+is)\s+on\s+(?:the|my|our)\s+list|(?:show|read)\s+(?:me\s+)?(?:the|my|our)\s+list"
    r"|what\s+do\s+(?:i|we)\s+need\s+(?:to\s+(?:buy|get|pick\s+up)|from\s+the\s+(?:store|shop|supermarket"
    r"|grocery\s+store)))(?:\s+today)?[\s?.!]*"
)


# A repeat said in a reminder: "every day at 8am", "daily", "every 30 minutes".
_WEEKDAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_DAY_NAMES = "(?:" + "|".join(_WEEKDAY_NAMES) + ")"
# A plural day ("on mondays") repeats; a singular one ("on monday") is a date.
_REPEAT = re.compile(
    r"\b(?:every\s+(?:\d+\s+(?:minutes?|hours?)|day|morning|evening|night|hour|weekdays?|weekends?|week|month"
    rf"|{_DAY_NAMES}(?:\s*(?:,|and|&)\s*{_DAY_NAMES})*)"
    rf"|(?:on\s+)?(?:weekdays|weekends|{_DAY_NAMES}s(?:\s*(?:,|and|&)\s*{_DAY_NAMES}s)*)"
    r"|daily|hourly|each\s+day|everyday)\b",
    re.IGNORECASE,
)
_UNIT_WORDS = r"seconds?|secs?|minutes?|mins?|hours?|hrs?|days?"
# The number must stand on its own: "1e309 minutes" was read as 309 minutes.
_TIMER_PART = re.compile(rf"(?P<n>(?<![\w.])\d+(?:\.\d+)?|\ban?|\bhalf\s+an?)[\s-]*(?P<unit>{_UNIT_WORDS})\b",
                         re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MAX_TIMER_SECONDS = 7 * 86400
# Words that sit where a timer's name goes without being one: "set a 5 minute timer".
_NOT_A_TIMER_NAME = {"minute", "minutes", "second", "seconds", "hour", "hours", "quick", "new", "another", "short",
                     "long", "countdown", "count", "please", "me", "you", "this", "that", "it"}


def _span_seconds(match: re.Match[str]) -> int:
    """"15 minute" -> 900, "half an hour" -> 1800, "1.5 hours" -> 5400."""
    number = match.group("n").lower()
    amount = 0.5 if number.startswith("half") else 1.0 if number in {"a", "an"} else float(number)
    return round(amount * _UNIT_SECONDS[match.group("unit").lower()[0]])


def _say_seconds(seconds: int) -> str:
    """900 -> "15 minutes", 5400 -> "1 hour 30 minutes", 90 -> "1 minute 30 seconds"."""
    parts = []
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count} {unit}{'s' if count != 1 else ''}")
    return " ".join(parts) or "0 seconds"


def _reminder_line(item: dict, now: datetime) -> str:
    """"- #3 today at 6:22 AM — check the oven", instead of a raw UTC timestamp."""
    try:
        due = datetime.fromisoformat(str(item.get("due_at", "")))
        when = describe(due, now) + (" (overdue)" if due <= now else "")
    except ValueError:
        when = str(item.get("due_at", ""))
    return f"- #{item.get('id')} {when} — {item.get('message')}"


def _readable_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


class AgentOrchestrator:
    def __init__(
        self,
        context: AgentContext,
        planner: Planner | None = None,
        smart_planner: Planner | None = None,
        vision_planner: Planner | None = None,
        ultra_planner: Planner | None = None,
        fallback_planner: Planner | None = None,
        data_dir: Path | None = None,
    ) -> None:
        self.context = context
        self.planner = planner
        # Optional higher-capability model for moderately complex questions.
        self.smart_planner = smart_planner
        # Optional top-tier model for the hardest questions (slow, large).
        self.ultra_planner = ultra_planner
        # Optional vision model used to look at images and the screen.
        self.vision_planner = vision_planner
        # Optional cross-provider fallback (e.g. OpenRouter) tried when every
        # primary tier is congested/unreachable, so chat keeps working.
        self.fallback_planner = fallback_planner
        self.control_room = AgentControlRoom.standard(obsidian_available=self.context.obsidian.available())
        # Tracks per-tier model reachability so chat can fall back fast<-smart<-ultra
        # when a tier is congested, and health can show "the advanced model is busy".
        # Where everything this orchestrator persists lives: traces, tier health,
        # generated images and documents.
        #
        # A parameter rather than `load_config()` at each use, because that reads the
        # PROCESS-WIDE config. Under the test runner every orchestrator then shared one
        # directory: a tier one test recorded as broken was still broken for the next, and
        # generated files landed in whatever directory the app itself uses. State that
        # ignores its caller's own config is shared state. One handle here means the next
        # store to be added needs no parameter of its own.
        self.data_dir = data_dir or load_config().data_dir
        # Persisted so a retired model id or a rejected key is still known after a
        # restart, rather than being rediscovered by failing a real chat turn. Only the
        # broken tiers are written; "busy" is ephemeral by nature.
        self.model_status = ModelStatus(self.data_dir / "model_status.json")
        # Per-turn latency traces (timings only, never prompts or replies), so a slow
        # turn can be explained instead of guessed at.
        self.traces = TraceStore(self.data_dir / "traces.json")
        # The failure log is a process-wide singleton (every tool, provider and request
        # handler writes to it), so it cannot take its path in a constructor like the two
        # above — it is pointed at this orchestrator's own directory instead. It used to
        # keep nothing: the traces here recorded four `image` turns failing after ~61s
        # each, and every reason had died with the process that caught it.
        FAILURES.attach(self.data_dir / "failures.json")
        self.autopilot_planner = AutopilotPlanner()
        # Fast deterministic router tried before any LLM, so common requests
        # route instantly and reliably with zero network latency.
        self.router = Planner(HeuristicPlannerProvider())
        self._file_processor_cache: FileProcessor | None = None
        self._weather_tool_cache: WeatherTool | None = None
        self._image_tool_cache: ImageTool | None = None
        self._news_tool_cache: NewsTool | None = None
        self._document_tool_cache: DocumentTool | None = None
        self._calculator_cache: CalculatorTool | None = None
        self._clock_cache: ClockTool | None = None
        self._command_verbs_cache: frozenset[str] | None = None
        self._youtube_tool_cache: YouTubeTool | None = None
        self._travel_tool_cache: TravelTool | None = None
        self._problem_solver_cache: ProblemSolver | None = None
        self._copilot_cache: JobCopilot | None = None
        self._resume_copilot_cache: JobCopilot | None = None
        self._repo_cache: dict[str, list[dict]] | None = None
        # The rolling summary of older turns is written by the fast tier in the background
        # (see context.py); with no model configured it simply never appears.
        register_summarizer(self._build_agent_brain((self.planner, self.smart_planner, self.fallback_planner), answer_max_tokens=400))

    @staticmethod
    def _complexity(text: str) -> int:
        """0 = simple (fast model), 1 = complex (smart), 2 = very complex (ultra)."""
        lowered = text.lower()
        words = len(text.split())
        deep = (
            "in depth", "in-depth", "step by step", "step-by-step", "comprehensive", "thorough", "rigorous",
            "deep dive", "detailed analysis", "prove", "derive", "full implementation", "design a system",
            "architecture", "from scratch", "think hard", "deeply", "use the big model", "ultra",
            # Logic / puzzle / multi-step reasoning cues -> the reasoning tier, which
            # thinks before answering (the non-reasoning tiers ramble on these).
            "puzzle", "riddle", "brain teaser", "logic puzzle", "logic problem",
            "measure exactly", "show your reasoning", "show your work", "show the steps",
            "find the flaw", "fallacy", "how many ways", "solve for",
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

    def _higher_chat_tiers(self, level: int) -> list[tuple[Planner, str]]:
        """The above-fast tiers to attempt for this complexity, strongest first.

        Lets a congested top tier degrade gracefully: try ultra, then smart, then
        the caller falls back to the fast tier. Empty for simple (level 0) chat."""
        ladder: list[tuple[Planner, str]] = []
        if level >= 2 and self.ultra_planner is not None:
            ladder.append((self.ultra_planner, "ultra"))
        if level >= 1 and self.smart_planner is not None:
            ladder.append((self.smart_planner, "smart"))
        return ladder

    def _recovery_chat_tiers(self, attempted: set[str]) -> list[tuple[Planner, str]]:
        """Usable primary tiers not already chosen by the complexity ladder.

        This handles a bad fast endpoint on simple chat: try smart once, then
        ultra, rather than repeatedly failing the same endpoint per message.
        """
        candidates = ((self.smart_planner, "smart"), (self.ultra_planner, "ultra"))
        return [
            (planner, label)
            for planner, label in candidates
            if planner is not None and label not in attempted and self.model_status.should_attempt(label)
        ]

    @staticmethod
    def _call_with_query(fn, command, profile, history, query, on_failure=None):
        """Call a provider's answer/stream_answer, passing the optional keywords only when
        the provider accepts them (test doubles and older providers do not).

        ``on_failure`` is how the tier says *why* it produced nothing. Without it every
        cause - a retired model, a rejected key, an unprovisioned id, an overloaded
        endpoint - arrives as the same empty string, and the ladder cannot tell a tier
        that needs a minute from one that needs a human."""
        extra: dict[str, object] = {}
        if query is not None and accepts_context_query(fn):
            extra["context_query"] = query
        if on_failure is not None and accepts_keyword(fn, "on_failure"):
            extra["on_failure"] = on_failure
        return fn(command, profile, None, history, **extra)

    def _record_tier(self, tier: str, reply: str, why: list[tuple[str, str]]) -> None:
        """Record how a tier behaved, keeping the reason it gave.

        A tier that answered is fine. A tier that did not is *busy* unless it said
        otherwise - an unexplained failure is the transient assumption, because guessing
        "broken" would stop trying a tier that was only having a bad minute.
        """
        if reply:
            self.model_status.record(tier, True)
            return
        kind, detail = why[-1] if why else ("", "")
        self.model_status.record(tier, False, reason=kind, detail=detail)

    @classmethod
    def _tier_reply(cls, provider, command, profile, history, on_token, query=None,
                    failures: list[tuple[str, str]] | None = None) -> str:
        """One tier's conversational reply: stream when a sink is given (and stream
        is supported), else a plain answer. Returns '' if the tier produced nothing
        (e.g. it was unreachable/congested), which signals the caller to fall back.
        ``query`` ranks the session context when ``command`` is a synthesized prompt."""
        check_cancelled()
        if provider is None:
            return ""
        # A local sink, so one provider shared by every request thread stays safe.
        sink = None if failures is None else (lambda kind, detail: failures.append((kind, detail)))
        streamer = getattr(provider, "stream_answer", None)
        if on_token is not None and streamer is not None:
            chunks: list[str] = []
            try:
                for token in cls._call_with_query(streamer, command, profile, history, query, sink):
                    check_cancelled()
                    chunks.append(token)
                    on_token(token)
            except (OSError, TimeoutError):
                check_cancelled()
                reset = getattr(on_token, "reset", None)
                if reset:
                    reset()
                return ""
            check_cancelled()
            return "".join(chunks).strip()
        answer_fn = getattr(provider, "answer", None)
        if answer_fn is not None:
            reply = (cls._call_with_query(answer_fn, command, profile, history, query, sink) or "").strip()
            check_cancelled()
            return reply
        return ""

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
            if trace is not None:
                trace.route_done(source)
            return decision

        fast = self.router.plan(command, help_text, profile)
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
        return decided("llm", self.planner.plan(command, help_text, profile, history))

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
        "describe image", "summarize youtube", "organize folder", "scan files",
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
        if any(token in spoken for token in tokens):
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

    async def handle(
        self,
        text: str,
        _allow_planner: bool = True,
        history: list[dict[str, str]] | None = None,
        on_token=None,
    ) -> ToolResult:
        """Route one turn, timing it. The inner leg of a planned command runs under the
        same trace, so a tool's own time is not counted as a second turn."""
        if current_trace() is not None:
            return await self._handle(text, _allow_planner, history, on_token)
        if not _allow_planner:
            # A top-level command with no routing: an agent step, an autopilot step, a
            # scheduled job. Untraced, but held to the same promise as a user's turn.
            try:
                return await self._handle(text, _allow_planner, history, on_token)
            except ApprovalDenied:
                raise
            except Exception as exc:
                return self._unexpected_failure(exc, text.strip().split(" ", 1)[0].lower())
        trace = TurnTrace()
        token = begin_trace(trace)
        try:
            result = await self._handle(text, _allow_planner, history, self._traced_tokens(on_token, trace))
            trace.finish(result.ok)
            return result
        except ApprovalDenied:
            trace.finish(False)
            raise
        except Exception as exc:
            trace.finish(False)
            return self._unexpected_failure(exc, trace.verb or "")
        finally:
            end_trace(token)
            if not trace.verb and trace.route_source in ("", "direct"):
                # A direct command prefix never reached the router. Record the tool name
                # only when the first word is a known command, so free-text never lands
                # in the trace file.
                trace.route_source = "direct"
                verb = (text or "").strip().split(" ", 1)[0].lower()
                if verb in self._command_verbs():
                    trace.kind, trace.verb = "command", verb
            try:
                self.traces.add(trace)
            except OSError:
                pass  # a trace is diagnostics; never fail a turn over one

    def _unexpected_failure(self, exc: Exception, verb: str) -> ToolResult:
        """The last line of defence: whatever a tool raised, the user gets an answer.

        Fuzzing every command prefix with hostile arguments found 21 ways to raise out of
        handle() - a NUL byte or a 3000-character name reaching pathlib, a sum too large to
        print - and each one ended the CLI session outright and showed the web page a raw
        "Error: ...". A tool bug still has to be fixed where it lives; this only guarantees
        an answer, and that the reason is written down instead of lost. Only the command
        word is recorded, never the user's text.
        """
        record_failure("orchestrator.handle", exc, verb=verb if verb in self._command_verbs() else "")
        # The commonest cause by far: a path the operating system refuses outright.
        unusable = (isinstance(exc, ValueError) and "null" in str(exc)) or (
            isinstance(exc, OSError) and (exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) == 206))
        if unusable:
            return ToolResult.failure("That isn't a file name I can use — it is too long or contains "
                                      "characters no file can have.", error=type(exc).__name__)
        detail = " ".join(str(exc).split())[:200] or type(exc).__name__
        return ToolResult.failure(
            f"Sorry — that failed with an unexpected error ({type(exc).__name__}: {detail}). "
            "I've logged it; ask me for `failures` to see the details.",
            error=type(exc).__name__,
        )

    async def _dispatch_meta(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for help, memory, audit, the daily briefing."""
        if lowered in {"help", "/help"}:
            return ToolResult.success(self.help_text())

        if lowered.startswith("remember note "):
            return self.context.obsidian.append_memory(command[len("remember note ") :].strip())

        if lowered.startswith("remember "):
            return self._remember(command[len("remember ") :])

        # "forget knowledge" clears the document index and belongs to _dispatch_knowledge,
        # which runs after this one.
        if lowered.startswith("forget ") and lowered != "forget knowledge":
            return self._forget(command[len("forget ") :])

        if lowered in {"memory", "show memory"}:
            memory = self.context.memory.dump()
            return ToolResult.success(self._memory_text(memory), memory=memory)

        if lowered in {"audit", "show audit"}:
            return ToolResult.success("Recent audit events.", events=self.context.audit.tail())

        if lowered in {"briefing", "daily briefing", "status briefing", "morning briefing"}:
            return self._briefing()
        return None

    async def _dispatch_jobs(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the job tracker, Jobright and resume tailoring."""
        if lowered in {"jobs", "job list", "list jobs", "job tracker"}:
            return self._jobs_list()

        if lowered in {"jobright", "jobright pull", "pull jobs", "pull jobright", "jobright sync"}:
            return await self._jobright_pull()

        if lowered.startswith("tailor job "):
            raw = command[len("tailor job ") :].strip().lstrip("#")
            return self.tailor_job(int(raw)) if raw.isdigit() else ToolResult.failure("Use: tailor job <id>")

        if lowered.startswith("resume file "):
            return self.set_resume_from_file(command[len("resume file ") :].strip())

        if lowered.startswith("job add "):
            return self._job_add(command[len("job add ") :].strip())

        if lowered.startswith("job stage "):
            return self._job_stage(command[len("job stage ") :].strip())

        if lowered.startswith("job remove ") or lowered.startswith("job delete "):
            return self._job_remove(command.split(None, 2)[2].strip() if len(command.split(None, 2)) > 2 else "")
        return None

    async def _dispatch_automation(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for autopilot, reminders, schedules and the agent loop."""
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

        if lowered in {"reminders next", "reminders next alarm", "reminders next timer"}:
            return self._next_reminder(lowered.split()[-1] if lowered.endswith(("alarm", "timer")) else "")

        if lowered.startswith("reminder add "):
            return self._reminder_add(command[len("reminder add ") :].strip())

        if lowered.startswith("remind me "):
            # "remind me of my wife's birthday" asks for a fact; it names no time to set.
            asked = fact_question(command)
            recalled = self._recall_fact(*asked) if asked else None
            if recalled is not None:
                return recalled
            return self._reminder_add(command[len("remind me ") :].strip())

        if lowered == "reminder done" or lowered.startswith("reminder done "):
            return self._reminder_done(command[len("reminder done") :].strip())

        if re.match(r"reminder (?:delete|cancel|remove)(?: |$)", lowered):
            return self._reminder_remove(command.split(" ", 2)[2] if len(command.split(" ", 2)) > 2 else "")

        if lowered == "reminder stop" or lowered.startswith("reminder stop "):
            return self._reminder_stop(command[len("reminder stop") :].strip())

        if lowered == "reminder snooze" or lowered.startswith("reminder snooze "):
            return self._reminder_snooze(command[len("reminder snooze") :].strip())

        if lowered == "timers":
            return self._timers()

        if lowered == "timer" or lowered.startswith("timer "):
            return self._timer(command[len("timer ") :])

        if lowered.startswith("alarm "):
            return self._alarm(command[len("alarm ") :])

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
            return await self._run_agent(command[len("agent run ") :].strip(), history=history_turns)

        if lowered in {"agents", "agent control", "control room", "agent dashboard"}:
            return self._agent_control_room()

        if lowered.startswith("agent "):
            return self._agent_detail(command[len("agent ") :].strip())
        return None

    async def _dispatch_files(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for reading, scanning and indexing files."""
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
        return None

    async def _dispatch_knowledge(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the knowledge base."""
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

        if lowered in {"knowledge reindex", "reindex knowledge", "knowledge backfill"}:
            return self._knowledge_reindex()

        if lowered in {"knowledge prune", "prune knowledge"}:
            outcome = self.context.knowledge.prune()
            removed = int(outcome.get("removed") or 0)
            if not removed:
                return ToolResult.success(
                    f"Nothing to prune — {outcome.get('remaining')} document(s) indexed.", **outcome
                )
            listed = "\n".join(f"- {source[:80]}" for source in list(outcome.get("sources") or [])[:12])
            return ToolResult.success(
                f"Pruned {removed} of my own generated document(s); {outcome.get('remaining')} remain. "
                f"Your own indexed files are never pruned.\n\n{listed}",
                **outcome,
            )

        if lowered.startswith("knowledge forget "):
            return self._knowledge_forget(command[len("knowledge forget ") :].strip())

        if lowered in {"knowledge clear", "forget knowledge"}:
            removed = self.context.knowledge.clear()
            return ToolResult.success(f"Cleared {removed} indexed document(s).", removed=removed)

        if lowered.startswith("knowledge search "):
            return self._knowledge_search(command[len("knowledge search ") :].strip())

        if lowered.startswith("ask knowledge "):
            return self._knowledge_answer(command[len("ask knowledge ") :].strip(), history_turns)

        if lowered.startswith("answer from knowledge "):
            return self._knowledge_answer(command[len("answer from knowledge ") :].strip(), history_turns)

        if lowered.startswith("recall "):
            return self._knowledge_search(command[len("recall ") :].strip())
        return None

    async def _dispatch_vault(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the Obsidian vault."""
        if lowered in {"notes", "vault", "notes status", "vault status", "obsidian", "obsidian status"}:
            return self.context.obsidian.status()

        if lowered in {"notes list", "list notes"}:
            return self.context.obsidian.list_notes()

        if lowered.startswith("notes search "):
            return self.context.obsidian.search(command[len("notes search ") :].strip())

        if lowered.startswith("note search "):
            return self.context.obsidian.search(command[len("note search ") :].strip())

        if lowered in {"notes audit", "vault audit", "audit notes", "audit vault"}:
            return self.context.obsidian.audit()

        if lowered.startswith("ask vault ") or lowered.startswith("ask notes "):
            return self._ask_vault(command.split(" ", 2)[2].strip() if len(command.split(" ", 2)) > 2 else "")

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
        return None

    async def _dispatch_file_intelligence(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for file inspection, conversion and extraction."""
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
        return None

    async def _dispatch_vision(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for screen, image and webcam vision."""
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
        return None

    async def _dispatch_tasks(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for task dashboards, workflows and folder operations."""
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
        return None

    async def _dispatch_research(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for research and the advisor."""
        if lowered.startswith("save research report "):
            return self._save_research_report(command[len("save research report ") :].strip())

        for verb in ("solve ", "advise me on ", "advise ", "strategize ", "strategise "):
            if lowered.startswith(verb):
                return self._solve(command[len(verb) :].strip(), history_turns)

        if lowered.startswith("research report "):
            return self._research_report(command[len("research report ") :].strip())

        if lowered.startswith("research "):
            return self._research(command[len("research ") :].strip())
        return None

    async def _dispatch_utilities(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for the clock, calculator, capabilities and diagnostics."""
        if lowered == "timecard" or lowered.startswith("timecard "):
            return self._time_card(command[len("timecard "):].strip() if " " in command else "")

        # One prefix, because the clock parses the zone out of the whole phrase itself —
        # the heuristic hands it `time what time is it in EST` verbatim.
        if lowered.startswith(("time ", "date ", "clock ")):
            return self._clock.now(command.split(" ", 1)[1])

        if lowered in {"time", "date", "clock", "what time is it", "what is the time",
                       "current time", "today", "what day is it", "datetime"}:
            return self._clock.now("")

        if lowered in {"capabilities", "what can you do"}:
            return self._capabilities()

        if lowered in {"failures", "errors", "what broke", "recent errors"}:
            return self._failure_report()

        if lowered.startswith(("calculate ", "calc ", "compute ")):
            rest = command.split(" ", 1)[1]
            return self._calculator.compute(rest)

        if lowered in {"latency", "traces", "why slow", "speed"}:
            return self._latency_report()
        return None

    async def _dispatch_generate(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for news, weather, documents and pictures."""
        if lowered == "news":
            return self._news_tool().headlines()

        if lowered.startswith("news "):
            # "news about nvidia" searched for "about nvidia" and titled the answer "Top
            # stories about about nvidia".
            topic = re.sub(r"^(?:about|on|regarding|for|from|in|re|of)\s+", "",
                           command[len("news ") :].strip(), flags=re.IGNORECASE)
            topic = re.sub(r"\b(?:today|tonight|now|right now|please|headlines?)\s*$", "", topic,
                           flags=re.IGNORECASE).strip(" ?.!,")
            return self._news_tool().headlines(topic) if topic else self._news_tool().headlines()

        if lowered.startswith("document "):
            return self._document_tool().create(command[len("document ") :].strip())

        if lowered.startswith("image "):
            return self._generate_image(command[len("image ") :].strip())

        if lowered in {"weather", "forecast", "weather here", "local weather", "weather forecast"}:
            return self._forecast("")

        if lowered.startswith("weather "):
            return self._forecast(command[len("weather ") :])
        return None

    # Profile keys that say where the user is: "remember my city is Austin" stores `city`.
    _HOME_KEYS = frozenset({
        "city", "location", "home city", "hometown", "home town", "town", "home", "where i live",
        "zip", "zip code", "postcode",
    })

    def _forecast(self, raw: str) -> ToolResult:
        """`weather [place]`. With no place, the forecast is for where the user is."""
        place = clean_place(raw) or self._home_place()
        if not place:
            return ToolResult.failure(
                "Where should I check? Name a city — or tell me once, \"remember my city is "
                "Austin\", and I'll use it from then on."
            )
        return self._weather_tool().forecast(place)

    def _home_place(self) -> str:
        """A remembered city, else the approximate location from the IP address."""
        for key, value in self.context.memory.get_profile().items():
            if re.sub(r"[^a-z]+", " ", str(key).lower()).strip() in self._HOME_KEYS and str(value).strip():
                return str(value).strip()
        located = self._travel_tool().here()
        return str(located.data.get("label") or "") if located.ok else ""

    async def _dispatch_travel(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for distance, trips, maps and places."""
        if lowered.startswith("distance "):
            rest = command[len("distance ") :].strip()
            match = re.search(r"\s+(?:to|and|->|→)\s+", rest, re.IGNORECASE)
            if not match:
                return ToolResult.failure("Use: distance <origin> to <destination>")
            return self._travel_tool().distance(rest[: match.start()].strip(), rest[match.end() :].strip())

        if lowered.startswith("trip "):
            rest = command[len("trip ") :].strip()
            stops = ([s.strip() for s in rest.split("|")] if "|" in rest
                     else re.split(r"\s+(?:to|->|→|then)\s+", rest, flags=re.IGNORECASE))
            return self._travel_tool().trip([s for s in stops if s.strip()])

        if lowered.startswith("around "):
            # Only claim "around <category>" for a known place category, so ordinary
            # messages that merely start with "around" (e.g. "around 3pm, remind me…")
            # fall through to normal routing instead of a stray places lookup.
            first = command[len("around ") :].strip().split(None, 1)[0] if command[len("around ") :].strip() else ""
            if TravelTool.knows_category(first):
                return self._travel_tool().around(first)

        if lowered in {"where am i", "where am i?", "my location", "locate me", "what's my location"}:
            return self._travel_tool().here()

        if lowered.startswith("map "):
            return self._travel_tool().map_query(command[len("map ") :].strip())

        if lowered.startswith("hotels near ") or lowered.startswith("hotels in "):
            place = command.split(None, 2)[2] if len(command.split(None, 2)) > 2 else ""
            return self._travel_tool().nearby("hotel", place.strip())

        if lowered.startswith("nearby "):
            rest = command[len("nearby ") :].strip()
            match = re.search(r"\s+(?:near|in|around)\s+", rest, re.IGNORECASE)
            if not match:
                return ToolResult.failure("Use: nearby <category> near <place>")
            return self._travel_tool().nearby(rest[: match.start()].strip(), rest[match.end() :].strip())
        return None

    async def _dispatch_web(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for YouTube, search, URLs, downloads and the browser."""
        if lowered.startswith("summarize youtube "):
            return self._youtube_summary(command[len("summarize youtube ") :].strip())

        if lowered.startswith("youtube summary "):
            return self._youtube_summary(command[len("youtube summary ") :].strip())

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
        return None

    async def _dispatch_desktop(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for apps, windows, screenshots, the shell and media keys."""
        if lowered in {"windows", "list windows", "show windows", "what windows are open"}:
            return self.context.windows.list_windows()

        if lowered in {"window", "split", "snap", "arrange"} or lowered.startswith(
            ("window ", "windows ", "split ", "snap ", "arrange ")
        ):
            return self._arrange_windows(command)

        if lowered.startswith("open app "):
            return self.context.desktop.open_app_or_file(command[len("open app ") :].strip())

        if lowered in {"screenshot", "take a screenshot", "take screenshot", "screen shot"}:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            return self.context.desktop.screenshot(str(self.data_dir / "screenshots" / f"screenshot-{stamp}.png"))

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

        level = re.fullmatch(r"media volume (\d{1,3})%?", lowered)
        if level:
            return self.context.music.set_volume(int(level.group(1)))

        if lowered.startswith("media "):
            return self.context.music.media_key(command[len("media ") :].strip())
        return None

    async def _dispatch_email(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for reading, drafting and sending mail."""

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
        return None

    async def _dispatch_batch(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for job applications and multi-command batches."""
        if lowered.startswith("plan apply job "):
            return await self.context.browser.prepare_job_application(
                command[len("plan apply job ") :].strip(),
                self.context.memory.get_profile(),
            )

        if lowered in {"multi retry failed", "retry failed tasks", "retry failed subtasks"}:
            return await self._retry_failed_tasks()

        if lowered.startswith("multi "):
            return await self._run_many(command[len("multi ") :])
        return None

    async def _dispatch_personal(self, command: str, lowered: str, history_turns) -> ToolResult | None:
        """Direct commands for lists, the calendar stand-in and this machine's own state.

        Last in the table on purpose: `list jobs`, `list notes` and `list windows` are
        exact commands of the groups above and must keep reaching them.
        """
        if lowered in {"lists", "my lists", "show lists", "show my lists"}:
            return self._lists()

        if lowered.startswith("list "):
            listed = self._list_command(command[len("list ") :].strip())
            if listed is not None:
                return listed

        # "shopping list", "the grocery list" - a list that exists, named on its own.
        bare = _BARE_LIST.fullmatch(lowered)
        if bare:
            known = self._known_list(bare.group("name"))
            if known in self.context.memory.lists():
                return self._list_command(known)

        # "what's on the list", "what do i need to buy"
        if _WHICH_LIST.fullmatch(lowered):
            lists = self.context.memory.lists()
            if re.search(r"\b(?:buy|get|pick\s+up|store|shop|supermarket)\b", lowered):
                return self._list_command("shopping show")
            if len(lists) == 1:
                return self._list_command(f"{next(iter(lists))} show")
            return self._lists()

        asked = fact_question(command)
        if asked is not None:
            recalled = self._recall_fact(*asked)
            if recalled is not None:
                return recalled

        drawn = draw(command)
        if drawn is not None:
            return drawn

        if lowered in {"calendar", "agenda", "my calendar", "my agenda"}:
            return self._calendar()

        if lowered.startswith("calendar add "):
            return self._calendar_add(command[len("calendar add ") :].strip())

        if lowered in {"system status", "status", "battery", "disk space", "computer status"}:
            return self._system_status()

        # Only when it reads as one: "convert this pdf to word" is not a unit conversion.
        if lowered.startswith("convert ") and looks_like_conversion(command):
            return UnitTool().convert(command)

        # "how much longer" means the timer, when one is running.
        if _HOW_LONG_LEFT.fullmatch(lowered) and any(due > datetime.now().astimezone() for due, _ in self._dated("timer")):
            return self._timers()

        edit = nameless_list_edit(command)
        if edit is not None:
            return self._nameless_list_edit(*edit)

        asked = date_question(command)
        if asked is not None:
            return self._date_answer(*asked)
        return None

    def _nameless_list_edit(self, verb: str, items: str) -> ToolResult:
        """"delete milk from my list" / "add eggs to the list", with no list named: the one
        list it can only mean, or a question naming the choices."""
        lists = self.context.memory.lists()
        if verb == "remove":
            wanted = items.strip().lower()
            holding = [name for name, entries in lists.items()
                       if any(wanted == entry.lower() or wanted in entry.lower() for entry in entries)]
            if len(holding) == 1:
                return self._list_command(f"{holding[0]} remove {items}")
            if not holding:
                return ToolResult.failure(f"{items} isn't on any of your lists.")
            return ToolResult.failure(f"{items} is on your {' and '.join(holding)} lists — which one?")
        if len(lists) == 1:
            return self._list_command(f"{next(iter(lists))} add {items}")
        if not lists:
            return ToolResult.failure(f"Which list should {items} go on? For example \"add {items} to my shopping list\".")
        return ToolResult.failure(f"Which list — {', '.join(sorted(lists))}? For example \"add {items} to my "
                                  f"{sorted(lists)[0]} list\".")

    def _date_answer(self, kind: str, what: str, other: str = "") -> ToolResult | None:
        """How many days until something, what day it falls on, or the days between two.

        None when the thing is not a date this can find ("when is the next train"), so the
        question goes on to something that can answer it.
        """
        now = datetime.now().astimezone()
        today = now.date()
        profile = self.context.memory.get_profile()
        if kind == "between":
            first, second = resolve_date(what, now, profile), resolve_date(other, now, profile)
            if first is None or second is None:
                return None
            days = abs((second[0] - first[0]).days)
            return ToolResult.success(
                f"**{days} days** between {first[1]} ({first[0]:%A %d %B %Y}) and {second[1]} "
                f"({second[0]:%A %d %B %Y}).".replace(" 0", " "), days=days)
        upcoming = re.fullmatch(r"\s*(?:my\s+|the\s+)?(?:next\s+)?(reminder|alarm|timer)s?\s*", what, re.IGNORECASE)
        if kind != "between" and upcoming:
            # "when is my next reminder" is not a date anyone told us.
            return self._next_reminder("" if upcoming.group(1).lower() == "reminder" else upcoming.group(1).lower())
        found = resolve_date(what, now, profile)
        if found is None:
            # "when is my dentist appointment" - a reminder may say.
            words = [w for w in re.findall(r"[a-z0-9']+", what.lower()) if w not in {"my", "our", "the", "a", "an"}]
            reminder = next((item for item in self.context.reminders.list()
                             if words and all(w in str(item.get("message", "")).lower() for w in words)), None)
            if reminder is not None:
                due = datetime.fromisoformat(str(reminder["due_at"]))
                return ToolResult.success(f"You have a reminder for that: {reminder['message']} — "
                                          f"{describe(due, now)}.", reminder=reminder)
            if re.match(r"\s*(?:my|our)\s+", what, re.IGNORECASE):
                yours = re.sub(r"^(?:my|our)\b", "your", what.strip(), flags=re.IGNORECASE)
                return ToolResult.success(
                    f"I don't know when {yours} is — you haven't told me, and no reminder mentions it. "
                    f"Tell me with \"remember {what.strip()} is <date>\".")
            return None
        day, name = found
        if kind == "until":
            days = (day - today).days
            count = f"{days} day{'s' if days != 1 else ''}"
            if other == "weeks" and days >= 7:
                weeks, spare = divmod(days, 7)
                count = f"{weeks} week{'s' if weeks != 1 else ''}" + (
                    f" and {spare} day{'s' if spare != 1 else ''}" if spare else "")
            return ToolResult.success(f"**{count}** until {name} ({day:%A %d %B %Y}).".replace(" 0", " "),
                                      days=days, date=day.isoformat())
        if name.lower() in RELATIVE_DAYS:
            # "Tomorrow is on Sunday, 27 September — tomorrow" said it twice.
            stamp = day.strftime("%A, %d %B %Y").replace(" 0", " ")
            verb = "was" if day < today else "is"
            return ToolResult.success(f"{name[:1].upper() + name[1:]} {verb} **{stamp}**.", date=day.isoformat())
        return ToolResult.success(f"{name[:1].upper() + name[1:]} is on {describe_day(day, today)}.",
                                  date=day.isoformat())

    def _lists(self) -> ToolResult:
        lists = self.context.memory.lists()
        if not lists:
            return ToolResult.success("You have no lists yet. Try \"add milk to my shopping list\".", lists={})
        lines = [f"- **{name}** ({len(items)}): " + ", ".join(items[:6]) + ("…" if len(items) > 6 else "")
                 for name, items in sorted(lists.items())]
        return ToolResult.success("Your lists:\n" + "\n".join(lines), lists=lists)

    def _known_list(self, name: str) -> str:
        """The list meant: an existing one spelled nearly the same, else the name as said.
        "add milk to my shoping list" started a second list beside the shopping one."""
        wanted = list_name(name)
        existing = list(self.context.memory.lists())
        if wanted in existing:
            return wanted
        close = difflib.get_close_matches(wanted, existing, n=1, cutoff=0.8)
        return close[0] if close else wanted

    def _recall_fact(self, key: str, personal: bool) -> ToolResult | None:
        """A fact the user told me, read back - or, for a plainly personal one, the plain
        truth that they never did. None otherwise, so the question goes on elsewhere."""
        def shape(text: object) -> str:
            return re.sub(r"[\s_]+", " ", str(text).lower().replace("favourite", "favorite")).strip()

        wanted = ("city", "hometown", "home town", "location", "address") if key == "where i live" else (key,)
        profile = self.context.memory.get_profile()
        for want in wanted:
            for stored, value in profile.items():
                if shape(stored) == shape(want):
                    if key == "where i live":
                        return ToolResult.success(f"You live in {value}.", fact=stored)
                    return ToolResult.success(f"Your {shape(stored)} is {value}.", fact=stored)
        if not personal:
            return None
        if key == "where i live":
            return ToolResult.success("You haven't told me where you live yet. Say \"I live in <city>\" and I'll "
                                      "remember it.", fact=None)
        return ToolResult.success(f"You haven't told me your {shape(key)} yet. Say \"my {shape(key)} is …\" and "
                                  f"I'll remember it.", fact=None)

    def _list_command(self, rest: str) -> ToolResult | None:
        """`list <name> show|add <items>|remove <item>|clear`, or `list <existing name>`.

        None when the words are not a list command: "list" is an ordinary English verb, and
        "list files in downloads" or "list the planets" must keep reaching the router.
        """
        match = re.match(r"(?P<name>.+?)\s+(?P<verb>show|add|remove|clear)\b\s*(?P<items>.*)$", rest, re.IGNORECASE)
        name = (match.group("name") if match else rest).strip()
        if name:
            name = self._known_list(name)
        if not match and name not in self.context.memory.lists():
            return None
        if not name:
            return ToolResult.failure("Which list? Try \"what's on my shopping list\".")
        label = f"your {list_name(name)} list"
        if not match or match.group("verb").lower() == "show":
            items = self.context.memory.list_items(name)
            if not items:
                return ToolResult.success(f"{label.capitalize()} is empty.", items=[])
            return ToolResult.success(f"{label.capitalize()} ({len(items)}):\n" + "\n".join(f"- {item}" for item in items),
                                      items=items)
        verb, raw_items = match.group("verb").lower(), match.group("items").strip()
        if verb == "clear":
            removed = self.context.memory.clear_list(name)
            return ToolResult.success(f"Cleared {label} ({removed} item{'s' if removed != 1 else ''}).", removed=removed)
        if not raw_items:
            return ToolResult.failure(f"What should I {verb} {'to' if verb == 'add' else 'from'} {label}?")
        if verb == "remove":
            removed_item = self.context.memory.remove_from_list(name, raw_items)
            if removed_item is None:
                return ToolResult.failure(f"{raw_items} isn't on {label}.")
            return ToolResult.success(f"Removed {removed_item} from {label}.", removed=removed_item)
        items = [item.strip(" .") for item in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", raw_items) if item.strip(" .")]
        added = self.context.memory.add_to_list(name, items)
        total = len(self.context.memory.list_items(name))

        def said(words: list[str]) -> str:
            return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]

        if not added:
            return ToolResult.success(f"{said(items).capitalize()} {'is' if len(items) == 1 else 'are'} already on {label}.",
                                      items=self.context.memory.list_items(name))
        spoken = said(added)
        return ToolResult.success(f"Added {spoken} to {label} ({total} item{'s' if total != 1 else ''}).",
                                  added=added, items=self.context.memory.list_items(name))

    def _calendar(self) -> ToolResult:
        """No calendar is connected, and the answer says so instead of guessing.

        "what's on my calendar today" was answered from a web search for the sentence.
        The honest answer is that none is connected - followed by the nearest thing that
        is real here, the reminders.
        """
        now = datetime.now().astimezone()
        upcoming = self.context.reminders.list()[:8]
        lines = [_reminder_line(item, now) for item in upcoming]
        text = "I can't see your calendar — no calendar is connected here yet."
        if lines:
            text += " Here's what you've asked me to remind you about:\n" + "\n".join(lines)
        else:
            text += " You have no reminders set either; say \"remind me to … at …\" and I'll keep track."
        return ToolResult.success(text, reminders=upcoming, calendar_connected=False)

    def _calendar_add(self, text: str) -> ToolResult:
        """"add the dentist to my calendar next tuesday at 9": a reminder, said as one."""
        result = self._reminder_add(text)
        prefix = "I can't write to your calendar yet (none is connected), so "
        result.message = prefix + ("I set a reminder instead. " if result.ok
                                   else "I tried to set a reminder instead, but: ") + result.message
        return result

    def _system_status(self) -> ToolResult:
        """Battery, CPU, memory and disk, read from this machine rather than guessed."""
        import shutil

        metrics = system_metrics()
        battery = battery_status()
        lines = []
        if battery is not None:
            lines.append(f"- Battery: {battery['percent']}%{' (charging)' if battery['plugged'] else ''}")
        if metrics.get("cpu_percent") is not None:
            lines.append(f"- CPU: {metrics['cpu_percent']}%")
        if metrics.get("ram_percent") is not None:
            lines.append(f"- Memory: {metrics['ram_percent']}% used")
        try:
            disk = shutil.disk_usage(Path.home().anchor or "/")
            lines.append(f"- Disk: {disk.free / 1e9:.0f} GB free of {disk.total / 1e9:.0f} GB")
        except OSError as exc:
            record_failure("orchestrator.system_status", exc)
        for gpu in metrics.get("gpus") or []:
            lines.append(f"- GPU {gpu.get('name')}: {gpu.get('util_percent')}%")
        if battery is None:
            lines.append("- Battery: none reported (a desktop, or the reading is unavailable)")
        return ToolResult.success("**This computer right now**\n" + "\n".join(lines), battery=battery, **metrics)

    # Dispatch order, as data. A group returns a ToolResult or None; the first
    # non-None wins, exactly as the original if/elif chain did.
    _DISPATCH = (
        _dispatch_meta,
        _dispatch_jobs,
        _dispatch_automation,
        _dispatch_files,
        _dispatch_knowledge,
        _dispatch_vault,
        _dispatch_file_intelligence,
        _dispatch_vision,
        _dispatch_tasks,
        _dispatch_research,
        _dispatch_utilities,
        _dispatch_generate,
        _dispatch_travel,
        _dispatch_web,
        _dispatch_desktop,
        _dispatch_email,
        _dispatch_batch,
        _dispatch_personal,
    )

    @staticmethod
    def _traced_tokens(on_token, trace: TurnTrace):
        """Wrap the stream callback to capture time-to-first-token, preserving .reset."""
        if on_token is None:
            return None

        def traced(text):
            trace.first_token()
            return on_token(text)

        reset = getattr(on_token, "reset", None)
        if reset is not None:
            traced.reset = reset
        return traced

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
        "window", "windows", "split", "snap", "arrange", "schedule", "email", "time", "date",
        "clock", "weather", "news", "distance", "trip", "download", "forget", "remember",
        "research", "image", "document", "map", "workflow", "autopilot", "calculate", "calc",
        "compute", "recall", "agent", "terminal", "shell", "media", "timer", "alarm",
    })

    def _reads_as_prose(self, command: str, lowered: str) -> bool:
        verb, _, rest = lowered.partition(" ")
        rest = rest.strip()
        if verb not in self._PROSE_PRONE or not rest:
            return False
        first = rest.split(None, 1)[0]
        if first in self._PROSE_OPENERS:
            return True
        if verb in {"split", "windows", "arrange", "snap"}:
            return not parse_placements(command)
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
            return bool(re.match(r"(?:when|what|how|why|who|where|the time|that time|the day|me)\b", rest))
        if verb in {"calculate", "calc", "compute"}:
            return not looks_like_arithmetic(rest) and len(re.findall(r"[a-z]{3,}", rest)) >= 2
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
        if not reply or len(reply.split()) > 8 or _NOT_AN_ANSWER.match(reply):
            return None
        routed = self.router.plan(reply, "", {})
        if routed.is_command or routed.explanation == SMALL_TALK:
            return None
        now = datetime.now().astimezone()
        if asked.startswith("How long should the timer run?"):
            return f"timer {reply}" if _TIMER_PART.search(spoken_to_digits(reply)) else None
        if asked.startswith("When should the alarm go off?"):
            return f"alarm {reply}"
        if asked.startswith("What should I remind you about") and before:
            subject = re.sub(r"^(?:to|that|about)\b\s*", "", reply, flags=re.IGNORECASE)
            return f"{before} to {subject}" if _meaningful(subject) else None
        if asked.startswith("I could not find a time in that.") and before:
            for candidate in (reply, f"at {reply}"):
                try:
                    if parse_when(spoken_to_digits(candidate), now) is not None:
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

    async def _handle(
        self,
        text: str,
        _allow_planner: bool = True,
        history: list[dict[str, str]] | None = None,
        on_token=None,
        _whole: bool = True,
    ) -> ToolResult:
        check_cancelled()
        command = text.strip()
        lowered = command.lower()
        history_turns = history or []
        if not command:
            return ToolResult.success("Say something and I will route it.")
        # A 300,000-character message was accepted and spent 41.5s in the advisor before
        # answering. Nothing a person types is this long; a paste this big belongs in a
        # file, where `ask file` and `summarize file` handle it properly and cheaply.
        if len(command) > MAX_COMMAND_CHARS:
            return ToolResult.failure(
                f"That message is {len(command):,} characters, past the "
                f"{MAX_COMMAND_CHARS:,} I take in one turn. Save it to a file and ask me "
                f"about that — try `summarize file <path>` or `ask file <path> about …`.",
                length=len(command),
                limit=MAX_COMMAND_CHARS,
            )

        if _allow_planner and _whole:
            answered = self._follow_up(command, history_turns)
            if answered is not None:
                command, lowered = answered, answered.lower()
            groups = self._split_requests(command)
            if groups:
                return await self._run_each(groups, history_turns)

        # The dispatch is a table, not a 500-line chain. Each group returns a result or
        # None to mean 'not mine'; order is preserved exactly as it was, and the
        # shadowing test in tests/test_command_dispatch.py still reads every prefix.
        if not (_allow_planner and self._reads_as_prose(command, lowered)):
            for dispatch in self._DISPATCH:
                handled = await dispatch(self, command, lowered, history_turns)
                if handled is not None:
                    return handled

        if _allow_planner:
            planned = self._route(command, self.context.memory.get_profile(), history_turns)
            if planned.is_chat and planned.response and planned.explanation == _VERBATIM:
                return ToolResult.success(planned.response)
            if planned.is_command and planned.command and planned.command.strip().lower() != lowered:
                # _allow_planner=False stops a planned command from re-triggering
                # the planner, which would let an LLM loop or double-call itself.
                # Light up the specialist the planner delegated to, so the control
                # room reflects the resolved tool, not just the Planner.
                resolved_agent = self.control_room.start(planned.command)
                trace = current_trace()
                if trace is not None:
                    trace.kind = "command"
                    trace.verb = planned.command.strip().split(" ", 1)[0].lower()
                    trace.tool_started()
                result = await self.handle(planned.command, _allow_planner=False, history=history_turns)
                if trace is not None:
                    trace.tool_done()
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
            if planned.is_chat:
                # Time-sensitive questions ("latest", "did X end", a recent year, …) must
                # not be answered from stale model knowledge — search the web first and
                # answer grounded in the results. Falls back to normal chat if there is no
                # LLM or the search returns nothing.
                needs_fresh = planned.explanation != SMALL_TALK and self._needs_fresh_info(command)
                if needs_fresh:
                    grounded = self._grounded_news_answer(command, history_turns, on_token)
                    if grounded is not None:
                        return grounded
                response = planned.response or ""
                profile = self._facts()
                level = self._complexity(command)
                _, requested_label = self._pick_chat_model(level)
                model_used = "fast"
                # Escalate by complexity (fast -> smart -> ultra), but degrade
                # gracefully: if a higher tier is congested/unreachable it returns
                # nothing, so we try the next tier down rather than failing.
                answered = False
                attempted: set[str] = set()
                for tier_planner, tier_label in self._higher_chat_tiers(level):
                    if not self.model_status.should_attempt(tier_label):
                        continue
                    attempted.add(tier_label)
                    why: list[tuple[str, str]] = []
                    reply = self._tier_reply(tier_planner.provider, command, profile, history_turns,
                                             on_token, failures=why)
                    self._record_tier(tier_label, reply, why)
                    if reply:
                        response, model_used, answered = reply, tier_label, True
                        break
                if not answered:
                    # Fall back to fast when it is not in its brief failure cooldown.
                    # A planned non-streaming reply already came from that tier.
                    fast_provider = self.planner.provider if self.planner else None
                    real_fast = fast_provider is not None and type(fast_provider).__name__ != "HeuristicPlannerProvider"
                    fast_available = self.model_status.should_attempt("fast")
                    if real_fast and fast_available:
                        attempted.add("fast")
                    # A router that chose chat but left the text to the answerer (a
                    # follow-up on the conversation) is asked for the reply too, rather
                    # than being counted as a dead endpoint. So is the instant router's
                    # canned small talk: it is the fallback for when no model answers, never
                    # the model's answer. Taking it as one is how "translate hello to
                    # spanish" was answered "I am here and ready..." on every client that
                    # does not stream.
                    canned = planned.explanation == SMALL_TALK
                    deferred = real_fast and (not planned.response or canned) and planned.confidence > 0
                    if fast_provider is not None and fast_available and (on_token is not None or deferred):
                        why = []
                        reply = self._tier_reply(fast_provider, command, profile, history_turns,
                                                 on_token, failures=why)
                        if real_fast:
                            self._record_tier("fast", reply, why)
                        if reply:
                            response, model_used, answered = reply, "fast", True
                    elif real_fast and fast_available and planned.response and planned.confidence > 0 and not canned:
                        model_used, answered = "fast", True
                        self.model_status.record("fast", True)
                    elif real_fast and fast_available:
                        self.model_status.record("fast", False)
                if not answered:
                    # A failed fast tier should promote this turn to any healthy
                    # primary tier that has not already been tried.
                    for tier_planner, tier_label in self._recovery_chat_tiers(attempted):
                        attempted.add(tier_label)
                        why = []
                        reply = self._tier_reply(tier_planner.provider, command, profile, history_turns,
                                                 on_token, failures=why)
                        self._record_tier(tier_label, reply, why)
                        if reply:
                            response, model_used, answered = reply, tier_label, True
                            break
                if not answered and self.fallback_planner is not None and self.model_status.should_attempt("openrouter"):
                    # Cross-provider safety net is also cooled down after an error.
                    why = []
                    reply = self._tier_reply(self.fallback_planner.provider, command, profile,
                                             history_turns, on_token, failures=why)
                    self._record_tier("openrouter", reply, why)
                    if reply:
                        response, model_used, answered = reply, "openrouter", True
                if not answered and not response:
                    response = (
                        "I couldn't reach any configured language model just now. Please try again in a minute."
                        if self._has_language_model() else _NO_MODEL_REPLY
                    )
                    model_used = "unavailable"
                degraded = model_used != requested_label
                trace = current_trace()
                if trace is not None:
                    trace.kind = "chat"
                    trace.model, trace.requested_model, trace.degraded = model_used, requested_label, degraded
                if degraded:
                    # Be honest about the fallback rather than passing off a lesser
                    # model's answer as the requested one's.
                    if model_used == "openrouter":
                        note = "\n\n_(My usual models were busy, so I answered with a backup model.)_"
                    elif model_used == "ultra":
                        note = f"\n\n_(My {requested_label} model was busy, so I answered with my deep model.)_"
                    elif model_used == "smart":
                        note = f"\n\n_(My {requested_label} model was busy, so I answered with my balanced model.)_"
                    elif model_used == "unavailable":
                        note = ""
                    else:
                        note = f"\n\n_(My {requested_label} model was busy, so I answered with my faster model.)_"
                    response = response + note
                    if note and on_token is not None:
                        on_token(note)
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
                    degraded=degraded,
                    planner={
                        "source_text": command,
                        "confidence": planned.confidence,
                        "explanation": planned.explanation,
                        "model": model_used,
                        "requested_model": requested_label,
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
        r"|\bmy\s+(?:calendar|schedule|agenda|day|week|plans?|reminders?|meetings?|appointments?"
        r"|tasks?|to-?dos?|inbox|emails?|notes?|jobs?|resume)\b",
        re.IGNORECASE,
    )

    def _needs_fresh_info(self, text: str) -> bool:
        if self.context.websearch is None:
            return False
        # The time is on the clock, not on the web. Asked "what is the current date and
        # time in EST" this searched, scraped a stale page, and answered 1:00 PM while
        # the machine's own clock read 6:26 PM.
        if asks_the_time(text) or self._NOT_FRESH.search(text):
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
        # The prompt is synthesized; the session context is ranked on the user's words.
        if streamed_live:
            text = self._tier_reply(provider, prompt, profile, history_turns, on_token, query=command)
        if not text:
            text = (self._call_with_query(answer, prompt, profile, history_turns, command) or "").strip()
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

    def _time_card(self, request: str) -> ToolResult:
        """`timecard [zone]` — an image whose text is the actual time.

        A diffusion model cannot spell, so asking FLUX for "the current time" yields
        clock-shaped glyph soup. This draws the real thing.
        """
        from laptop_agent.tools.textcard import render_card

        clock = self._clock.now(request)
        if not clock.ok:
            return clock
        moment = datetime.fromisoformat(str(clock.data["iso"]))
        zone = str(clock.data.get("zone") or "")
        headline = moment.strftime("%I:%M %p").lstrip("0")
        # datetime.fromisoformat keeps the offset but loses the zone *name*, so %Z
        # returns "UTC-04:00" here and pairing it with the offset printed it twice. The
        # readable name is already in the clock's data; it goes in the footer.
        lines = [
            headline,
            moment.strftime("%A, %d %B %Y").replace(" 0", " "),
            str(clock.data.get("offset", "")),
        ]
        directory = self.data_dir / "images"
        path = reserve_new_path(directory, f"time-{int(moment.timestamp())}", ".png")
        drawn = render_card([line for line in lines if line], path, footer=zone)
        if not drawn.ok:
            # Honest failure beats a picture of nonsense: give them the time itself.
            return ToolResult.failure(f"{drawn.message}{NL}{NL}{clock.message}")
        url = f"/api/image?name={path.name}"
        return ToolResult.success(
            f"![The time is {headline}]({url}){NL}{NL}{clock.message}",
            image=str(path),
            name=path.name,
            url=url,
            **{k: v for k, v in clock.data.items() if k != "iso"},
            iso=clock.data["iso"],
        )

    @staticmethod
    def _failure_report() -> ToolResult:
        """`failures` — what has been caught and swallowed this session.

        Every `except` that returns a fallback also records here, because the two worst
        bugs found in this codebase both hid behind code that handled an error politely:
        a permanently 400-ing ultra tier reported itself as "busy", and a 503 from the
        chat endpoint was shown to the user as "the model returned an empty document"."""
        summary = FAILURES.summary()
        recent = FAILURES.recent(12)
        if not recent:
            return ToolResult.success("Nothing has failed this session.", **summary)
        lines = [
            f"**{summary['kept']} failure(s) recorded**, {summary['distinct']} distinct.",
            "",
            "| where | what | age | detail |",
            "|---|---|---|---|",
        ]
        for entry in recent:
            detail = str(entry.get("message", "")).replace("|", "/")[:80]
            lines.append(
                f"| `{entry['where']}` | {entry['kind']} | {entry['age_seconds']}s ago | {detail} |"
            )
        frequent = summary.get("most_frequent") or []
        if frequent:
            lines.append("")
            lines.append("**Most frequent:** " + " · ".join(
                f"{item['what']} ×{item['count']}" for item in frequent[:5]
            ))
        return ToolResult.success(NL.join(lines), **summary)

    @staticmethod
    def _capabilities() -> ToolResult:
        """A grouped tour rather than the raw command list.

        "what can you do" is the first thing anyone asks, and it used to return a hundred
        lines of command syntax. `help` still does, for reference."""
        text = NL.join([
            "I run on your laptop and I can act on it, not just talk about it. "
            "Plain language works for all of this — the commands are just shortcuts.",
            "",
            "**Day to day**",
            "Reminders, timers and alarms that ring on screen and out loud · shopping and to-do "
            "lists · unit conversions and date counting · I remember what you tell me.",
            "_Try:_ `set a pasta timer for 10 minutes` · `add milk and eggs to my shopping list` · "
            "`how many days until christmas` · `remember my wife's birthday is june 5`",
            "",
            "**Files and documents**",
            "Read, search, convert and organise your files · summarise a PDF, DOCX or "
            "spreadsheet · answer questions about one file · pull tables out · per-column stats.",
            "_Try:_ `summarize the readme` · `what files are here` · `analyze spreadsheet sales.csv`",
            "",
            "**See and hear**",
            "OCR an image or scan with layout preserved · transcribe audio and video · "
            "look at your screen or webcam · summarise a YouTube video.",
            "_Try:_ `read screen` · `ocr image receipt.png` · `summarize youtube <url>`",
            "",
            "**Make things**",
            "Draw a picture · write a real PDF, Word or Markdown document · draw a diagram "
            "or flowchart · build a table you can copy or export as CSV.",
            "_Try:_ `draw a red fox in snow` · `write a one page brief on X as a pdf` · "
            "`draw an ERD for a users and orders schema`",
            "",
            "**Know things**",
            "Real headlines and weather · web search and multi-source research · a searchable "
            "knowledge base of anything you index · your Obsidian vault as memory · exact arithmetic.",
            "_Try:_ `news` · `weather Hyderabad` · `research local-first AI agents` · "
            "`what is 67458363*37834872`",
            "",
            "**Do things**",
            "Open URLs and download files · open apps · run shell commands · send and search "
            "email · play a song or video on YouTube · driving distances, trips and maps.",
            "_Anything that changes the outside world asks you first._",
            "_Try:_ `play music despacito` · `distance Hyderabad to Kurnool` · `email unread`",
            "",
            "**Think ahead**",
            "Weigh a decision with researched options and a plan · run an autonomous "
            "multi-step goal · schedule recurring jobs · reminders · a daily briefing.",
            "_Try:_ `should I use Postgres or MongoDB for this` · `agent run tidy my downloads folder` · "
            "`daily at 08:00 :: briefing`",
            "",
            "Ask `help` for the full command list, or `latency` to see where time went.",
        ])
        return ToolResult.success(text, kind="capabilities")

    @staticmethod
    def help_text() -> str:
        return "\n".join(
            [
                "Commands:",
                "  remember <key> = <value>",
                "  forget <key>",
                "  knowledge prune",
                "  window <name> <position>",
                "  windows",
                "  memory",
                "  audit",
                "  briefing",
                "  jobs  ·  job add <company> [stage]  ·  job stage <id> <stage>  ·  job remove <id>",
                "  autopilot <goal>",
                "  autopilot workflow <safe command 1> ;; <safe command 2>",
                "  autopilot status",
                "  agent run <goal>   (autonomous multi-step: plans, acts, observes, repeats)",
                "  agent runs | agent last",
                "  schedule <when> :: <command>     (e.g. 'daily at 08:00 :: briefing')",
                "  schedule agent <when> :: <goal>  (run the autonomous agent on a schedule)",
                "  schedule list | schedule remove <id> | schedule run due",
                "  remind me <what> <when>  (\"to call mom at 6pm\", \"in 20 minutes\", \"every day at 8am\")",
                "  reminders | reminders due | reminders next [alarm|timer] | reminder done <id>",
                "  reminder delete <id|words|all>  ·  reminder stop <words>  (only what is going off now)",
                "  reminder snooze [id] [<n>m]",
                "  timer <duration> [label]  ·  alarm <time>  ·  timers  (running timers and the time left)",
                "  lists | list <name> show | list <name> add <items> | list <name> remove <item> | list <name> clear",
                "  calendar | calendar add <event and time>  (no calendar is connected: it sets a reminder)",
                "  system status  (battery, CPU, memory, disk)",
                "  convert <amount> <unit> to <unit>",
                "  how many days until <date|holiday|my ...> | when is <holiday|my ...>",
                "  flip a coin | roll a dice | pick a number between <a> and <b>",
                "  what's my <fact>  (read back from what the user told me)",
                "  screenshot",
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
                "  knowledge reindex  (embed documents stored before semantic search)",
                "  knowledge export <path>",
                "  knowledge forget <id>",
                "  notes status | notes list | notes search <query>",
                "  ask vault <question>  (link-aware answer from your notes)",
                "  notes audit  (orphans, broken links, notes missing a summary)",
                "  read note <name> | save note <title> : <body>",
                "  remember note <text>",
                "  search files <query> <path>",
                "  web search <query>",
                "  news [topic]  (real headlines from free feeds, with article text)",
                "  image <description>  (draw a picture; add landscape/portrait/wide/tall)",
                "  document <request> [as pdf|word|markdown]  (write and render a real file)",
                "  time | date | time in <place>  (this machine's clock, never the web)",
                "  calculate <expression>  (exact arithmetic: big integers, fractions, functions)",
                "  failures  (what has been caught and swallowed this session)",
                "  latency  (where recent turns spent their time)",
                "  weather <location>  (real current + 3-day forecast)",
                "  distance <origin> to <destination>  (driving miles + ETA)",
                "  trip <stop1> to <stop2> to <stop3> …  (multi-stop route + totals)",
                "  hotels near <place>  ·  nearby <category> near <place>",
                "  around <category>  ·  where am i  (IP location)  ·  map <place|A to B>",
                "  summarize youtube <url>  (transcript -> summary, asks indexed too)",
                "  research <topic>",
                "  research report <topic>",
                "  save research report <topic> to <path|obsidian>",
                "  solve <problem or decision>  (researches, weighs options, recommends a plan)",
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
                "  media playpause|next|previous|stop|volumeup|volumedown|mute",
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
        """Set a reminder from however it was said: "call mom at 6pm", "in 20 minutes".

        The time is resolved locally by `timeparse`, never by the model - asked for a date
        a model produces a plausible one, and a reminder that fires on the wrong day is
        worse than one that refuses. The resolved instant is always read back in local
        time, because that is what makes a misreading visible in the same breath.
        """
        cleaned = _spoken_request(expression)
        if not cleaned:
            return ToolResult.failure("What should I remind you about, and when?")
        # "remind me in 5" is minutes, the way it is said; it was refused as having no time.
        cleaned = re.sub(r"\bin\s+(\d{1,3})(?=\s*[.!]*$|\s+(?:to|about|that)\b)", r"in \1 minutes", cleaned,
                         flags=re.IGNORECASE)
        repeat = _REPEAT.search(cleaned)
        if repeat:
            return self._repeating_reminder(cleaned, repeat)
        return self._set_reminder(cleaned)

    def _set_reminder(self, cleaned: str, default_half: str = "", label: str = "",
                      what: str = "reminder") -> ToolResult:
        now = datetime.now().astimezone()
        try:
            when = parse_when(cleaned, now, default_half=default_half)
        except TimeParseError as exc:
            return ToolResult.failure(str(exc))
        if when is None:
            if what == "alarm":
                return ToolResult.failure("When should the alarm go off? Try \"wake me up at 7\".")
            if re.search(r"\byesterday\b|\blast\s+(?:night|week|month)\b", cleaned, re.IGNORECASE):
                return ToolResult.failure("That's already in the past. When should I remind you?")
            if re.search(r"\bin\s+\d+\s+(?:months?|years?)\b", cleaned, re.IGNORECASE):
                return ToolResult.failure("I set reminders minutes, hours, days or weeks ahead, or on a "
                                          "date — for example \"on 2027-03-01 at 9\".")
            return ToolResult.failure(
                "I could not find a time in that. Try \"remind me to call mom at 6pm\", "
                "\"tomorrow at 9\", \"in 20 minutes\" or a date like 2026-12-25 07:30."
            )
        message = label or _reminder_message(cleaned, when.start, when.end)
        if not message:
            return ToolResult.failure(f"What should I remind you about {describe(when.at, now)}?")
        try:
            outcome = self.context.reminders.add(when.at.isoformat(), message)
        except ValueError:
            return ToolResult.failure("Reminder date/time must look like YYYY-MM-DD HH:MM.")
        if not outcome.get("ok"):
            return ToolResult.failure(f"Could not add reminder: {outcome.get('reason', 'unknown error')}")
        reminder = outcome["reminder"]
        spoken = describe(when.at, now)
        # A time already gone is kept, not refused - an explicit past date is a legitimate
        # backfill - but it is never left to look like it was scheduled ahead.
        note = "" if when.at > now else " (that time has already passed, so it is due now)"
        if what == "timer":
            # "Pasta timer (10 minutes)" -> "Pasta timer set for 10 minutes."
            named = re.fullmatch(r"(?P<name>.+?) \((?P<amount>.+)\)", message)
            text = (f"{named.group('name')} set for {named.group('amount')}. It goes off {spoken}."
                    if named else f"Timer set. It goes off {spoken}.")
        elif what == "alarm":
            text = f"Alarm set for {spoken}."
        else:
            text = f"Reminder #{reminder['id']} set for {spoken}: {message}{note}"
        return ToolResult.success(text, reminder=reminder, due_local=when.at.isoformat(), due_spoken=spoken)

    def _timer(self, expression: str) -> ToolResult:
        """`timer 5 minutes [for the pasta]` - a reminder that is only a countdown.

        The label comes only from where a name is said - "a pasta timer", "for the pasta",
        "to check the oven". Taking whatever words were left over named a timer "Could you
        please" from "could you set a 15 minute timer please".
        """
        cleaned = _spoken_request(expression)
        # "an hour and a half", "2 and a half minutes"
        cleaned = re.sub(rf"\b(\d+|an?)\s+(?:and\s+a\s+half\s+({_UNIT_WORDS})|({_UNIT_WORDS})\s+and\s+a\s+half)\b",
                         lambda m: f"{'1' if m.group(1).lower() in {'a', 'an'} else m.group(1)}.5 "
                                   f"{m.group(2) or m.group(3)}", cleaned, flags=re.IGNORECASE)
        parts = list(_TIMER_PART.finditer(cleaned))
        if not parts:
            return ToolResult.failure("How long should the timer run? Say something like \"set a timer for 10 minutes\".")
        # "1 hour and 30 minutes": the spans that run on from the first one.
        seconds, end = _span_seconds(parts[0]), parts[0].end()
        for part in parts[1:]:
            if not re.fullmatch(r"\s*(?:,|and)?\s*", cleaned[end: part.start()], re.IGNORECASE):
                break
            seconds, end = seconds + _span_seconds(part), part.end()
        if seconds < 1:
            return ToolResult.failure("A timer needs at least a second.")
        if seconds > _MAX_TIMER_SECONDS:
            return ToolResult.failure("That's longer than a timer should run. Set a reminder for the day instead, "
                                      "for example \"remind me on 2027-03-01 to renew the lease\".")
        before, after = cleaned[: parts[0].start()], cleaned[end:]
        amount = _say_seconds(seconds)
        named = (re.search(r"\b(?:a|an|my|the)\s+(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*)?)\s+timer\b", before, re.I)
                 or re.match(r"\s*(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*)?)\s+timer\b", after, re.I)
                 or re.match(r"\s*(?:timer\s+)?for\s+(?:the\s+|my\s+|a\s+)?(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,2})"
                             r"(?:\s+please)?\s*[.!]*$", after, re.I))
        purpose = re.match(r"\s*(?:timer\s+)?to\s+(?P<what>[a-z][\w' -]{0,40}?)(?:\s+please)?\s*[.!]*$", after, re.I)
        name = named.group("name").lower() if named else ""
        if name in _NOT_A_TIMER_NAME:
            name = ""
        if name:
            label = f"{name.capitalize()} timer ({amount})"
        elif purpose:
            label = f"Timer to {purpose.group('what').strip()} ({amount})"
        else:
            label = f"Timer ({amount})"
        return self._set_reminder(f"in {seconds} seconds", label=label, what="timer")

    def _dated(self, kind: str = "") -> list[tuple[datetime, dict]]:
        """Active reminders with their due time, soonest first; only timers or alarms when
        `kind` names one."""
        dated = []
        for item in self.context.reminders.list():
            if kind and not re.search(rf"\b{kind}\b", str(item.get("message", "")), re.IGNORECASE):
                continue
            try:
                dated.append((datetime.fromisoformat(str(item.get("due_at", ""))), item))
            except ValueError:
                continue
        return sorted(dated, key=lambda pair: pair[0])

    def _next_reminder(self, kind: str = "") -> ToolResult:
        """"what's my next reminder", "when is my alarm". "when is my next reminder" was
        read as a date the user had never told us and answered "I don't know"."""
        now = datetime.now().astimezone()
        noun = kind or "reminder"
        upcoming = [(due, item) for due, item in self._dated(kind) if due > now]
        repeating = [job for job in self._repeating_reminders()
                     if not kind or re.search(rf"\b{kind}\b", job.spec, re.IGNORECASE)]
        parts = []
        if upcoming:
            due, item = upcoming[0]
            what = "" if str(item["message"]).lower() == kind else f": {item['message']}"
            parts.append(f"Your next {noun} is {describe(due, now)}{what}.")
        if repeating:
            parts.append("Repeating: " + "; ".join(
                f"{job.schedule.describe()} — {job.spec[len('reminder add now '):]}" for job in repeating) + ".")
        if not parts:
            return ToolResult.success(f"You have no {noun}s coming up.")
        return ToolResult.success(" ".join(parts), reminder=upcoming[0][1] if upcoming else None)

    def _timers(self) -> ToolResult:
        """Running timers and what is left on each - "how much time is left on my timer"."""
        now = datetime.now().astimezone()
        running = self._dated("timer")
        if not running:
            return ToolResult.success("No timer is running. Say \"set a timer for 10 minutes\" to start one.",
                                      timers=[])
        lines = []
        for due, item in sorted(running, key=lambda pair: pair[0]):
            name = re.sub(r"\s*\(.*\)$", "", str(item["message"]))
            left = (due - now).total_seconds()
            status = "going off now" if left <= 0 else f"**{_say_seconds(round(left))}** left"
            lines.append(f"{name}: {status} (at {due.astimezone(now.tzinfo).strftime('%I:%M:%S %p').lstrip('0')})")
        return ToolResult.success("\n".join(lines) if len(lines) > 1 else lines[0],
                                  timers=[item for _due, item in running])

    def _alarm(self, expression: str) -> ToolResult:
        """`alarm 7` / `alarm at 6:30am tomorrow`. A bare hour is in the morning."""
        cleaned = re.sub(r"^(?:for|to|at)\s+", "at ", _spoken_request(expression), flags=re.IGNORECASE)
        if re.fullmatch(r"\d{1,2}(?::\d{2})?(?:\s*(?:am|pm|a\.m\.|p\.m\.))?", cleaned, re.IGNORECASE):
            cleaned = "at " + cleaned
        # "every weekday at 7" was set once, for tomorrow, and the repeat was dropped unsaid.
        repeat = _REPEAT.search(cleaned)
        if repeat:
            return self._repeating_reminder(cleaned, repeat, label="Alarm", default_half="am")
        return self._set_reminder(cleaned, default_half="am", label="Alarm", what="alarm")

    def _repeating_reminder(self, cleaned: str, repeat: re.Match[str], label: str = "",
                            default_half: str = "") -> ToolResult:
        """"remind me every day at 8am to take my vitamins", on the scheduler.

        It used to become ONE reminder at 8am today reading "every day to take my vitamins".
        Weekdays, weekends and named days repeat too ("every monday", "on weekdays"); only
        "every week" and "every month" without a day are set once, and say so.
        """
        rule = repeat.group(0).lower()
        # "every monday" keeps its "monday" in what is parsed: dropped with the rest of the
        # rule, the one-off fallback below landed on today instead of the coming Monday.
        weekday = re.search(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", rule)
        body = (cleaned[: repeat.start()] + " " + (weekday.group(1) if weekday else "") + " "
                + cleaned[repeat.end():]).strip()
        now = datetime.now().astimezone()
        try:
            when = parse_when(body, now, default_half=default_half)
        except TimeParseError as exc:
            return ToolResult.failure(str(exc))
        message = label or (_reminder_message(body, when.start, when.end) if when else _reminder_message(body, 0, 0))
        if not message:
            return ToolResult.failure("What should I remind you about?")
        days = parse_days(re.sub(r"^(?:every|on)\s+", "", rule)) if re.search(
            r"week(?:day|end)|" + _DAY_NAMES, rule) else ()
        interval = re.match(r"every\s+(\d+)\s+(minutes?|hours?)", rule)
        if interval or rule in {"every hour", "hourly"}:
            schedule = f"every {interval.group(1)} {interval.group(2)}" if interval else "every hour"
        elif days or re.search(r"\b(?:day|daily|morning|evening|night|everyday)\b", rule):
            if when is not None:
                clock = when.at.astimezone(now.tzinfo)
            else:
                hour = 18 if "evening" in rule else 21 if "night" in rule else 9
                clock = now.replace(hour=hour, minute=0)
            on = ("weekdays" if days == (0, 1, 2, 3, 4) else "weekends" if days == (5, 6)
                  else " and ".join(f"{_WEEKDAY_NAMES[day]}s" for day in days) if days else "daily")
            schedule = f"{on} at {clock.hour:02d}:{clock.minute:02d}"
        else:
            if when:
                once = self._set_reminder(body, default_half=default_half, label=label,
                                          what="alarm" if label == "Alarm" else "reminder")
            else:
                once = ToolResult.failure("When should I remind you?")
            if once.ok:
                once.message = (once.message.rstrip(".") + ". I can repeat daily, on weekdays or on given "
                                f"days, or every few hours, but not {rule} yet, so this one is set once.")
            return once
        spec = f"reminder add now {message}"
        try:
            wanted = parse_schedule(schedule).to_dict()
        except ScheduleError as exc:
            return ToolResult.failure(str(exc))
        # Said twice, it rang twice: the same request makes the same job once.
        for existing in self._repeating_reminders():
            if existing.spec == spec and existing.schedule.to_dict() == wanted:
                return ToolResult.success(f"That's already set — {existing.schedule.describe()}: {message}.",
                                          job=existing.to_dict())
        try:
            job = self.context.scheduler.add("command", spec, schedule, now)
        except ScheduleError as exc:
            return ToolResult.failure(str(exc))
        if label == "Alarm":
            return ToolResult.success(f"Alarm set — {job.schedule.describe()}. Say \"cancel the alarm\" to stop it.",
                                      job=job.to_dict())
        return ToolResult.success(
            f"Repeating reminder set — {job.schedule.describe()}: {message}. "
            f"Say \"cancel the {message.split()[-1]} reminder\" to stop it.",
            job=job.to_dict(),
        )

    def _repeating_reminders(self) -> list:
        return [job for job in self.context.scheduler.list_jobs()
                if job.kind == "command" and job.spec.lower().startswith("reminder add now ")]

    def _reminders_list(self) -> ToolResult:
        now = datetime.now().astimezone()
        reminders = self.context.reminders.list()
        repeating = self._repeating_reminders()
        if not reminders and not repeating:
            return ToolResult.success("You have no reminders set.", reminders=[])
        lines = [_reminder_line(item, now) for item in reminders[:20]]
        lines += [f"- every: {job.schedule.describe()} — {job.spec[len('reminder add now '):]}" for job in repeating]
        count = len(reminders) + len(repeating)
        return ToolResult.success(
            f"You have {count} reminder{'s' if count != 1 else ''}:\n" + "\n".join(lines),
            reminders=reminders,
            repeating=[job.to_dict() for job in repeating],
        )

    def _reminders_due(self) -> ToolResult:
        now = datetime.now().astimezone()
        reminders = self.context.reminders.due()
        if not reminders:
            return ToolResult.success("Nothing is due right now.", reminders=[])
        return ToolResult.success(
            f"{len(reminders)} reminder{'s are' if len(reminders) != 1 else ' is'} due:\n"
            + "\n".join(_reminder_line(item, now) for item in reminders[:20]),
            reminders=reminders,
        )

    def _find_reminder(self, raw: str) -> tuple[dict | None, str]:
        """A reminder named by id, "last", or its words. (reminder, why-not)."""
        wanted = raw.strip().lstrip("#").lower()
        active = self.context.reminders.list()
        if wanted.isdigit():
            found = next((item for item in active if int(item.get("id", 0)) == int(wanted)), None)
            return found, "" if found else f"There is no active reminder #{wanted}."
        if not active:
            return None, "You have no active reminders."
        if wanted in {"", "last", "latest", "the last", "the latest", "most recent"}:
            if wanted == "" and len(active) > 1:
                return None, "Which one? " + " ".join(f"#{item['id']} {item['message']};" for item in active[:8]).rstrip(";")
            return max(active, key=lambda item: int(item.get("id", 0))), ""
        words = [word for word in re.findall(r"[a-z0-9']+", wanted) if word not in {"the", "my", "a", "to", "about", "for"}]
        matches = [item for item in active if all(word in str(item.get("message", "")).lower() for word in words)]
        if len(matches) > 1 and words and words[0] in {"timer", "alarm"}:
            # "stop the alarm" means the one ringing, not tomorrow's.
            now = datetime.now().astimezone()
            ringing = [item for due, item in self._dated() if due <= now and item in matches]
            if ringing:
                return ringing[0], ""
        if len(matches) == 1 or (matches and words and words[0] in {"timer", "alarm"}):
            return max(matches, key=lambda item: int(item.get("id", 0))), ""
        if not matches:
            return None, f"I have no reminder about '{raw.strip()}'."
        return None, "Which one? " + " ".join(f"#{item['id']} {item['message']};" for item in matches[:8]).rstrip(";")

    def _reminder_done(self, raw: str) -> ToolResult:
        reminder, why = self._find_reminder(raw)
        if reminder is None:
            return ToolResult.failure(why)
        self.context.reminders.complete(int(reminder["id"]))
        return ToolResult.success(f"Done: {reminder['message']}.", id=reminder["id"], completed=True)

    def _reminder_remove(self, raw: str) -> ToolResult:
        """Cancel a reminder by id, "last", or its words - one-off or repeating. The one
        going off now comes first: cancelling a ringing alarm deleted its weekday schedule
        and left it ringing."""
        words = raw.strip().lower()
        bulk = re.fullmatch(r"all(?:\s+(?P<kind>reminders|timers|alarms))?", words)
        if bulk:
            return self._remove_all(bulk.group("kind") or "reminders")
        ringing = None if words.lstrip("#").isdigit() else self._ringing(words)
        if ringing is not None:
            self.context.reminders.complete(int(ringing["id"]))
            return ToolResult.success(f"Stopped: {ringing['message']}.", id=ringing["id"], completed=True)
        for job in self._repeating_reminders():
            spoken = job.spec[len("reminder add now "):].lower()
            if words and not words.isdigit() and all(w in spoken for w in re.findall(r"[a-z0-9']+", words)
                                                   if w not in {"the", "my", "a", "to", "about", "for", "reminder"}):
                self.context.scheduler.remove(job.id)
                return ToolResult.success(f"Stopped the repeating reminder: {job.spec[len('reminder add now '):]}.")
        reminder, why = self._find_reminder(raw)
        if reminder is None:
            return ToolResult.failure(why)
        self.context.reminders.remove(int(reminder["id"]))
        return ToolResult.success(f"Cancelled: {reminder['message']}.", id=reminder["id"], removed=True)

    def _ringing(self, words: str) -> dict | None:
        """The reminder going off now (or overdue) that these words name, if any."""
        now = datetime.now().astimezone()
        wanted = [word for word in re.findall(r"[a-z0-9']+", words.lower())
                  if word not in {"the", "my", "a", "to", "about", "for", "reminder", "this", "that", "it", "last"}]
        for due, item in self._dated():
            if due > now:
                break
            if all(word in str(item.get("message", "")).lower() for word in wanted):
                return item
        return None

    def _reminder_stop(self, raw: str) -> ToolResult:
        """"stop the alarm": the one ringing, or a running timer. Anything else is only
        described - an ambiguous word must not delete an alarm set for the morning."""
        ringing = self._ringing(raw)
        if ringing is not None:
            self.context.reminders.complete(int(ringing["id"]))
            return ToolResult.success(f"Stopped: {ringing['message']}.", id=ringing["id"], completed=True)
        reminder, why = self._find_reminder(raw) if raw.strip() else (None, "")
        if reminder is not None and re.search(r"\btimer\b", str(reminder["message"]), re.IGNORECASE):
            self.context.reminders.remove(int(reminder["id"]))
            return ToolResult.success(f"Stopped: {reminder['message']}.", id=reminder["id"], removed=True)
        now = datetime.now().astimezone()
        wanted = [word for word in re.findall(r"[a-z0-9']+", raw.lower())
                  if word not in {"the", "my", "a", "to", "about", "for", "reminder", "this", "that", "it"}]
        jobs = [job for job in self._repeating_reminders()
                if wanted and all(word in job.spec.lower() for word in wanted)]
        set_up = []
        if reminder is not None:
            due = datetime.fromisoformat(str(reminder["due_at"]))
            set_up.append(f"{reminder['message']} is set for {describe(due, now)}")
        set_up += [f"{job.spec[len('reminder add now '):]} repeats {job.schedule.describe()}" for job in jobs]
        if not set_up:
            return ToolResult.failure(why or "Nothing is going off right now.")
        name = raw.strip() or "reminder"
        return ToolResult.success(f"Nothing is going off right now. {'; '.join(set_up)}. "
                                  f"Say \"cancel the {name}\" if you want it removed.")

    def _remove_all(self, kind: str) -> ToolResult:
        """"cancel all my reminders" - asks first when it would remove more than one, since
        nothing brings them back."""
        now = datetime.now().astimezone()
        active = self.context.reminders.list()
        if kind == "reminders":
            chosen, repeating = active, self._repeating_reminders()
        else:
            word = kind[:-1]
            chosen = [item for item in active if re.search(rf"\b{word}\b", str(item.get("message", "")), re.IGNORECASE)]
            repeating = []
        count = len(chosen) + len(repeating)
        if not count:
            return ToolResult.success(f"You have no {kind} to cancel.")
        if count > 1:
            preview = [_reminder_line(item, now) for item in chosen[:20]]
            preview += [f"- every: {job.schedule.describe()} — {job.spec[len('reminder add now '):]}" for job in repeating]
            self.context.web.approval_gate.require(ApprovalRequest(
                action=f"Cancel all {count} {kind}", risk=RiskLevel.HIGH,
                reason="Removes every one of them at once; they cannot be brought back.", preview="\n".join(preview)))
        for item in chosen:
            self.context.reminders.remove(int(item["id"]))
        for job in repeating:
            self.context.scheduler.remove(job.id)
        if count == 1:
            only = chosen[0]["message"] if chosen else repeating[0].spec[len("reminder add now "):]
            return ToolResult.success(f"Cancelled: {only}.", removed=1)
        return ToolResult.success(f"Cancelled all {count} {kind}.", removed=count)

    def _reminder_snooze(self, raw: str) -> ToolResult:
        """`reminder snooze [id|last] [<n>m]` - ten minutes unless told otherwise. A bare
        number is an id; minutes carry their unit."""
        target = raw.strip()
        minutes = 10
        minutes_match = re.search(r"(?:^|\s)(?:for\s+)?(\d+)\s*(?:m|min|mins|minutes?)\s*$", target)
        if minutes_match:
            minutes = int(minutes_match.group(1))
            target = target[: minutes_match.start()].strip()
        if not target:
            # "snooze" means the one ringing now - never the newest reminder, which may be
            # hours away.
            due = self.context.reminders.due()
            if not due:
                return ToolResult.failure("Nothing is going off right now. Say which one, e.g. \"snooze reminder 3\".")
            reminder: dict | None = max(due, key=lambda item: str(item.get("due_at", "")))
            why = ""
        else:
            reminder, why = self._find_reminder(target)
        if reminder is None:
            return ToolResult.failure(why)
        minutes = max(1, min(minutes, 24 * 60))
        now = datetime.now().astimezone()
        until = now + timedelta(minutes=minutes)
        self.context.reminders.snooze(int(reminder["id"]), until)
        return ToolResult.success(f"Snoozed until {describe(until, now)}: {reminder['message']}.",
                                  id=reminder["id"], due_local=until.isoformat())

    def _jobs_list(self) -> ToolResult:
        jobs = self.context.jobs.list()
        stats = self.context.jobs.stats()
        if not jobs:
            return ToolResult.success(
                "No tracked applications yet. Add one with: job add <company> [stage]", jobs=[], stats=stats
            )
        lines = [
            f"**Job pipeline — {stats['total']} application(s), "
            f"{stats['interviews']} interview(s), {stats['offers']} offer(s), "
            f"{round(stats['response_rate'] * 100)}% response rate**"
        ]
        for job in jobs[:20]:
            role = f" — {job['role']}" if job["role"] else ""
            when = f" · {job['next_date']}" if job["next_date"] else ""
            lines.append(f"- #{job['id']} **{job['company']}**{role} · _{job['stage']}_{when}")
        return ToolResult.success("\n".join(lines), jobs=jobs, stats=stats)

    def _job_add(self, text: str) -> ToolResult:
        text = text.strip()
        if not text:
            return ToolResult.failure("Use: job add <company> [stage]")
        # A trailing recognized stage word becomes the stage; the rest is the company.
        parts = text.rsplit(None, 1)
        company, stage = text, "applied"
        if len(parts) == 2 and normalize_stage(parts[1]) != "applied":
            company, stage = parts[0], parts[1]
        elif len(parts) == 2 and parts[1].lower() in {"applied", "apply"}:
            company, stage = parts[0], "applied"
        try:
            job = self.context.jobs.add(company.strip(), stage=stage)
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        return ToolResult.success(f"Tracking #{job['id']} {job['company']} at stage '{job['stage']}'.", job=job)

    def _job_stage(self, text: str) -> ToolResult:
        parts = text.split(None, 1)
        if len(parts) < 2 or not parts[0].lstrip("#").isdigit():
            return ToolResult.failure("Use: job stage <id> <stage>")
        job = self.context.jobs.update(int(parts[0].lstrip("#")), stage=parts[1].strip())
        if job is None:
            return ToolResult.failure(f"No tracked job #{parts[0].lstrip('#')}.")
        return ToolResult.success(f"#{job['id']} {job['company']} → {job['stage']}.", job=job)

    async def _jobright_pull(self) -> ToolResult:
        resume_text = self.context.jobs.get_resume().get("text", "")
        result = await self.context.jobright.pull(resume_text=resume_text)
        if not result.ok:
            return result
        leads = result.data.get("leads", [])
        summary = self.context.jobs.import_leads(leads)
        return ToolResult.success(
            f"Pulled {len(leads)} Jobright lead(s); added {summary['added']} new, "
            f"skipped {summary['skipped']} already tracked.",
            scraped=result.data.get("scraped", len(leads)),
            relevant=len(leads),
            added=summary["added"],
            skipped=summary["skipped"],
            added_jobs=summary["added_jobs"],
        )

    def _job_remove(self, raw: str) -> ToolResult:
        if not raw.lstrip("#").isdigit():
            return ToolResult.failure("Use: job remove <id>")
        removed = self.context.jobs.remove(int(raw.lstrip("#")))
        return ToolResult.success("Removed." if removed else f"No tracked job #{raw.lstrip('#')}.", removed=removed)

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
        "ask vault <question>",
        "notes audit",
        "read note <name>",
        "save note <title> : <body>",
        "remember <key> = <value>",
        "forget <key>",
        "knowledge prune",
        "window <name> <left|right|top|bottom|full|corner>",
        "windows",
        # web & research
        "web search <query>",
        "news [topic]",
        "image <description>",
        "document <request> as pdf|word|markdown",
        "weather <location>",
        "distance <origin> to <destination>",
        "trip <stop1> to <stop2> to <stop3>",
        "around <category>",
        "map <place|origin to destination>",
        "hotels near <place>",
        "summarize youtube <url>",
        "research <topic>",
        "research report <topic>",
        "solve <problem or decision>",
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
        "jobs",
        "job add <company> [stage]",
        "tasks",
        "reminders due",
        "reminder add <YYYY-MM-DD> <text>",
        "timer <duration>",
        "alarm <time>",
        "timers",
        "lists",
        "list <name> show",
        "list <name> add <items>",
        "calendar",
        "convert <amount> <unit> to <unit>",
        "run command <command>",
        "memory",
        "audit",
    )

    def _agent_reference(self) -> str:
        return "\n".join(f"- {command}" for command in self._AGENT_COMMANDS)

    def _build_agent_brain(self, planners=None, answer_max_tokens: int = 900):
        """Return a sync ``decide(prompt) -> str`` backed by the strongest available model.

        By default tries the smart tier, then the fast planner, then the cross-provider
        fallback (OpenRouter), using the first that returns text — so reasoning (the
        autonomous agent and the advisor) keeps working when a tier is congested. Pass an
        explicit ``planners`` tuple to change the order, and ``answer_max_tokens`` to allow
        long outputs (e.g. a full resume) instead of a concise chat reply. Yields '' when no
        LLM is configured, so callers end with a clear message.
        """
        if planners is None:
            planners = (self.smart_planner, self.planner, self.fallback_planner)
        answerers = []
        for planner in planners:
            answer = getattr(planner.provider, "answer", None) if planner else None
            if answer is not None:
                answerers.append(answer)
        if not answerers:
            return lambda _prompt: ""
        profile = self.context.memory.get_profile()

        def decide(prompt: str) -> str:
            check_cancelled()
            for answer in answerers:
                try:
                    reply = answer(prompt, profile, None, None, max_tokens=answer_max_tokens)
                except TypeError:
                    reply = answer(prompt, profile, None, None)  # older provider without max_tokens
                except Exception:
                    reply = None
                check_cancelled()
                if reply:
                    return reply
            return ""

        return decide

    def _resume_copilot(self) -> JobCopilot:
        """CoPilot for full-resume generation. Uses a large output budget so the whole resume
        (every experience + projects + education) is never truncated. The smart tier leads
        because it reliably emits the complete document; the ultra reasoning tier (which tends
        to stop a long structured output early) and OpenRouter are fallbacks."""
        if self._resume_copilot_cache is None:
            brain = self._build_agent_brain(
                (self.smart_planner, self.ultra_planner, self.planner, self.fallback_planner),
                answer_max_tokens=8000,
            )
            self._resume_copilot_cache = JobCopilot(decide=brain)
        return self._resume_copilot_cache

    async def run_agent(self, goal: str, on_step=None, history: list[dict[str, str]] | None = None) -> ToolResult:
        """Public entry for autonomous runs with an optional per-step callback (used by the
        web UI to stream the live trace). Mirrors the 'agent run <goal>' command path.
        ``history`` is the session transcript so the goal can refer to earlier turns."""
        return await self._run_agent(goal, on_step=on_step, history=history)

    async def _run_agent(self, goal: str, on_step=None, history: list[dict[str, str]] | None = None) -> ToolResult:
        goal = goal.strip()
        if not goal:
            return ToolResult.failure("Use: agent run <goal>  (e.g. 'agent run summarize the README and index it')")

        agent_id = self.control_room.start(f"agent: {goal}")
        agent = AutonomousAgent(
            decide=self._build_agent_brain(),
            execute=lambda command: self.handle(command, _allow_planner=False, history=history),
            command_reference=self._agent_reference(),
            max_steps=6,
        )
        context = context_block(history or [], goal, budget=AGENT_BUDGET)
        try:
            result = await agent.run(goal, on_step=on_step, context=context)
        except OperationCancelled:
            self.control_room.finish(agent_id, "Stopped by user", ok=False)
            raise
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
        due = self.context.scheduler.claim_due_jobs(moment)
        ran = []
        for index, job in enumerate(due):
            try:
                if job.kind == "agent":
                    result = await self._run_agent(job.spec)
                else:
                    result = await self.handle(job.spec, _allow_planner=False)
                status = "ok" if result.ok else "failed"
                message = result.message
            except OperationCancelled:
                for pending in due[index:]:
                    self.context.scheduler.mark_ran(pending.id, moment, "stopped")
                raise
            except Exception as exc:
                status, message = "failed", str(exc)
            self.context.scheduler.mark_ran(job.id, datetime.now().astimezone(), status)
            ran.append({"id": job.id, "kind": job.kind, "spec": job.spec, "status": status, "message": message})
        if not ran:
            return ToolResult.success("No scheduled jobs are due.", ran=[])
        ok = sum(1 for item in ran if item["status"] == "ok")
        return ToolResult(ok=ok == len(ran), message=f"Ran {len(ran)} due job(s): {ok} ok.", data={"ran": ran})

    def _file_processor(self) -> FileProcessor:
        # Built lazily from existing tools — no AgentContext field needed.
        if self._file_processor_cache is None:
            self._file_processor_cache = FileProcessor(self.context.files, self.context.transcribe)
        return self._file_processor_cache

    def _generate_image(self, request: str) -> ToolResult:
        """`image <description>` — the trailing shape word picks the aspect ratio."""
        described, shape = request.strip(), "square"
        match = re.search(r"\s+(square|landscape|portrait|wide|tall)$", described, re.IGNORECASE)
        if match:
            shape = match.group(1).lower()
            described = described[: match.start()].strip()
        return self._image_tool().generate(described, shape=shape)

    def _latency_report(self) -> ToolResult:
        """`latency` — where recent turns actually spent their time."""
        stats = self.traces.summary()
        if not stats.get("turns"):
            return ToolResult.success("No turns recorded yet. Ask me a few things and try again.", **stats)

        def ms(value) -> str:
            return "—" if value is None else f"{value} ms"

        lines = [
            f"Over the last {stats['turns']} turn(s) — {stats['chat_turns']} chat, {stats['command_turns']} command:",
            "",
            "| measure | median |",
            "|---|---|",
            f"| total | {ms(stats['median_total_ms'])} |",
            f"| routing | {ms(stats['median_route_ms'])} |",
            f"| time to first token (chat) | {ms(stats['median_ttft_ms'])} |",
            f"| chat total | {ms(stats['median_chat_total_ms'])} |",
            f"| tool run (command) | {ms(stats['median_tool_ms'])} |",
            "",
            f"{stats['llm_routed']} turn(s) ({stats['llm_routed_pct']}%) paid an extra model call just to "
            "classify the request before answering it.",
        ]
        if stats["degraded"]:
            lines.append(f"{stats['degraded']} turn(s) fell back to a different model tier.")
        if stats["failed"]:
            lines.append(f"{stats['failed']} turn(s) failed.")
        return ToolResult.success(chr(10).join(lines), latency=stats, recent=self.traces.recent(10))

    def _document_tool(self) -> DocumentTool:
        if self._document_tool_cache is None:
            self._document_tool_cache = DocumentTool(
                data_dir=self.data_dir,
                writer=self._build_document_writer(),
                approval_gate=self.context.web.approval_gate,
            )
        return self._document_tool_cache

    def _build_document_writer(self):
        """A document is long-form prose, so it goes to the best available tier with a
        generous token budget — the chat default would truncate it mid-section."""
        planner = self.smart_planner or self.planner
        if planner is None:
            return None
        provider = getattr(planner, "provider", None)
        if provider is None or not hasattr(provider, "answer"):
            return None

        def write(prompt: str) -> str:
            return provider.answer(prompt, {}, max_tokens=6000) or ""

        return write

    def _knowledge_reindex(self) -> ToolResult:
        """`knowledge reindex` — give older documents the vector newer ones get on save."""
        outcome = self.context.knowledge.backfill_vectors()
        if not outcome.get("ok"):
            return ToolResult.failure(
                f"Cannot reindex: {outcome.get('reason')}. Set OPENAI_API_KEY in .env.", **outcome
            )
        embedded, pending = outcome.get("embedded", 0), outcome.get("pending", 0)
        if not embedded and not pending:
            return ToolResult.success(
                f"All {outcome.get('total', 0)} document(s) already have vectors.", **outcome
            )
        message = f"Embedded {embedded} of {outcome.get('total', 0)} document(s)."
        if pending:
            message += f" {pending} could not be reached; run it again to finish them."
        return ToolResult.success(message, **outcome)

    @property
    def _clock(self) -> ClockTool:
        if self._clock_cache is None:
            self._clock_cache = ClockTool()
        return self._clock_cache

    @property
    def _calculator(self) -> CalculatorTool:
        if self._calculator_cache is None:
            self._calculator_cache = CalculatorTool()
        return self._calculator_cache

    def _news_tool(self) -> NewsTool:
        if self._news_tool_cache is None:
            from laptop_agent.tools.research import fetch_page_text

            self._news_tool_cache = NewsTool(
                page_reader=lambda url: fetch_page_text(url, max_chars=1500),
                approval_gate=self.context.web.approval_gate,
            )
        return self._news_tool_cache

    def _image_tool(self) -> ImageTool:
        if self._image_tool_cache is None:
            config = load_config()
            self._image_tool_cache = ImageTool(
                api_key=config.llm_image_api_key,
                data_dir=self.data_dir,
                model=config.llm_image_model,
                base_url=config.llm_image_base_url,
                fallback_model=config.llm_image_fallback_model,
                fallback_api_key=config.llm_image_fallback_api_key,
                approval_gate=self.context.web.approval_gate,
            )
        return self._image_tool_cache

    def _weather_tool(self) -> WeatherTool:
        if self._weather_tool_cache is None:
            # Reuse the shared approval gate so weather is MEDIUM-gated like other network reads.
            self._weather_tool_cache = WeatherTool(approval_gate=self.context.web.approval_gate)
        return self._weather_tool_cache

    def _youtube_tool(self) -> YouTubeTool:
        if self._youtube_tool_cache is None:
            self._youtube_tool_cache = YouTubeTool()
        return self._youtube_tool_cache

    def _travel_tool(self) -> TravelTool:
        if self._travel_tool_cache is None:
            self._travel_tool_cache = TravelTool(approval_gate=self.context.web.approval_gate)
        return self._travel_tool_cache

    def _youtube_summary(self, url: str) -> ToolResult:
        cleaned = url.strip().strip("'\"")
        if not cleaned:
            return ToolResult.failure("Use: summarize youtube <url>")
        got = self._youtube_tool().transcript(cleaned)
        if not got.ok:
            return got
        text = str(got.data["transcript"])
        video_id = str(got.data["video_id"])
        # Summarize with the smart tier (better than the fast router for long transcripts).
        tier_planner = self.smart_planner or self.planner
        provider = tier_planner.provider if tier_planner else None
        answer = getattr(provider, "answer", None)
        summary = ""
        if answer is not None:
            prompt = (
                "Summarize this YouTube video from its transcript. Give a one-line **TL;DR**, then 4-6 "
                "**key points** as bullets, then any **action items or takeaways**. Be concise and skimmable.\n\n"
                f"Transcript:\n{text[:8000]}"
            )
            summary = answer(prompt, self.context.memory.get_profile(), None, None) or ""
        if not summary:
            extractive = self.context.files.summarize_text(text, source=f"youtube:{video_id}", sentences=6)
            summary = str(extractive.data.get("summary", "")) if extractive.ok else ""
        # Index the transcript so the user can ask follow-up questions about the video.
        indexed = self.context.knowledge.add(f"youtube:{video_id}", text)
        return ToolResult.success(
            summary or f"Fetched the transcript for {got.data['url']}.",
            video_id=video_id,
            url=got.data["url"],
            summary=summary,
            transcript_chars=len(text),
            indexed=indexed,
        )

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

    def _problem_solver(self) -> ProblemSolver:
        if self._problem_solver_cache is None:
            self._problem_solver_cache = ProblemSolver(
                # A structured framing/options/plan analysis is long; the default 900-token
                # budget truncated it mid-sentence, so give it real room.
                decide=self._build_agent_brain(answer_max_tokens=4000),
                research=self._advice_research,
            )
        return self._problem_solver_cache

    def _copilot(self) -> JobCopilot:
        if self._copilot_cache is None:
            self._copilot_cache = JobCopilot(decide=self._build_agent_brain())
        return self._copilot_cache

    def tailor_application(self, resume_text: str, job_text: str, company: str = "", role: str = "") -> ToolResult:
        """Tailor a resume to a job description (ATS score + missing keywords + grounded
        bullets/cover letter/interview pack). Public entry for the web Job Tracker page."""
        result = self._copilot().tailor(resume_text, job_text, company=company, role=role)
        if not result.ok:
            return ToolResult.failure(result.package)
        return ToolResult.success(
            result.package,
            ats=result.ats, keywords=result.keywords, missing=result.missing,
            grounding=result.grounding, used_llm=result.used_llm, company=company, role=role,
        )

    def pipeline_snapshot(self) -> dict:
        """The job pipeline for the live dashboard: every tracked job, each annotated with a
        live (local, no-LLM) ATS score when a base resume and a job description are present,
        plus the funnel stats and base-resume metadata."""
        jobs = self.context.jobs
        resume = jobs.get_resume()
        resume_text = resume.get("text", "")
        enriched = []
        for job in jobs.list():
            item = dict(job)
            jd = job.get("description", "")
            if resume_text and jd:
                item["ats"] = ats_score(extract_keywords(jd), resume_text)
            enriched.append(item)
        return {
            "ok": True,
            "jobs": enriched,
            "stats": jobs.stats(),
            "resume": {
                "present": bool(resume_text),
                "profile": resume.get("profile", {}),
                "chars": len(resume_text),
                "source": resume.get("source", ""),
                "updated_at": resume.get("updated_at", ""),
            },
        }

    def set_resume_text(self, text: str, source: str = "pasted") -> ToolResult:
        text = (text or "").strip()
        if not text:
            return ToolResult.failure("Resume text is empty.")
        self.context.jobs.set_resume(text, source=source)
        return ToolResult.success(f"Saved base resume ({len(text)} chars).", chars=len(text), source=source)

    def set_resume_from_file(self, path: str) -> ToolResult:
        extracted = self.context.files.extract_document_text(path)
        if not extracted.ok:
            return extracted
        text = str(extracted.data.get("text", "")).strip()
        if not text:
            return ToolResult.failure("No text could be extracted from that file.")
        name = Path(path.strip().strip("'\"")).name
        self.context.jobs.set_resume(text, source=name)
        return ToolResult.success(f"Loaded base resume from {name} ({len(text)} chars).", chars=len(text), source=name)

    def tailor_job(self, job_id: int) -> ToolResult:
        """Tailor the stored base resume to a tracked job's description, persisting the
        result on the job. The LLM call is on-demand (one job at a time)."""
        job = self.context.jobs.get(job_id)
        if job is None:
            return ToolResult.failure(f"No tracked job #{job_id}.")
        resume_record = self.context.jobs.get_resume()
        resume = resume_record.get("text", "")
        if not resume:
            return ToolResult.failure("Set a base resume first — paste it or load it from a file.")
        jd = job.get("description", "")
        if not jd:
            return ToolResult.failure(f"#{job_id} {job['company']} has no job description to tailor against.")
        profile = resume_record.get("profile", {}) or {}
        repos = self._github_repos(profile.get("github_user", ""))
        name = next((ln.strip() for ln in resume.splitlines() if ln.strip()), "")
        # Explicit profile overrides win; otherwise retain source contact details.
        contact = profile.get("contact_links", "") or self._contact_from_resume(resume)
        result = self._resume_copilot().tailor_resume(
            resume, jd, company=job.get("company", ""), role=job.get("role", ""),
            repos=repos, contact=contact,
            certs=profile.get("cert_links", ""), name=name,
        )
        if not result.ok:
            return ToolResult.failure(result.package, job_id=job_id)
        stored = self.context.jobs.set_tailoring(
            job_id, package=result.package, used_llm=result.used_llm,
            grounding=result.grounding, ats=result.ats, expected_resume=resume_record.get("updated_at", ""),
        )
        if stored is None:
            return ToolResult.failure("The base resume changed or the job was removed during tailoring. Retry with the current resume.")
        return ToolResult.success(result.package, job_id=job_id, ats=result.ats, used_llm=result.used_llm)

    async def render_job_pdf(self, job_id: int) -> ToolResult:
        """Render a job's tailored HTML resume to a downloadable PDF under data_dir/resumes."""
        job = self.context.jobs.get(job_id)
        if job is None or not job.get("tailored_package"):
            return ToolResult.failure("Tailor the job first, then export to PDF.")
        from laptop_agent.tools.resume_pdf import render_html_to_pdf

        package = job["tailored_package"]
        digest = hashlib.sha256(package.encode("utf-8")).hexdigest()[:16]
        out = self.context.jobs.path.parent / "resumes" / f"job_{job_id}_{digest}.pdf"
        result = await render_html_to_pdf(package, out)
        if result.ok:
            if self.context.jobs.set_tailored_pdf(job_id, str(out), expected_package=package) is None:
                return ToolResult.failure("The resume changed during export. Export the current version again.")
        return result

    @staticmethod
    def _contact_from_resume(resume: str) -> str:
        """Recover a contact line (email · phone) from the top of the base resume, escaped
        as safe HTML for the fixed template. Best-effort; returns '' if nothing is found."""
        head = "\n".join(resume.splitlines()[:8])
        email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", head)
        phone = re.search(r"\+?\d[\d\s().-]{7,}\d", head)
        parts = [m.group(0).strip() for m in (email, phone) if m]
        parts.extend(re.findall(r"https?://[^\s<>]+", head))
        return " · ".join(html.escape(p) for p in parts)

    def _github_repos(self, user: str) -> list[dict]:
        """Public repo list (name + url) for grounding project links in tailored resumes.
        Cached per process; best-effort — returns [] if unset or unreachable."""
        user = (user or "").strip()
        if not user:
            return []
        if getattr(self, "_repo_cache", None) is None:
            self._repo_cache = {}
        if user in self._repo_cache:
            return self._repo_cache[user]
        repos: list[dict] = []
        try:
            request = urllib.request.Request(
                f"https://api.github.com/users/{user}/repos?per_page=100&sort=updated",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "jarvis-resume"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            if isinstance(data, list):
                repos = [{"name": r.get("name", ""), "url": r.get("html_url", "")}
                         for r in data if isinstance(r, dict) and r.get("html_url")]
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
            repos = []
        self._repo_cache[user] = repos
        return repos

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
            return ToolResult.failure("Use: solve <problem or decision>  (e.g. 'solve should I rewrite the auth layer now or later')")
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

    def _knowledge_answer(self, question: str, history: list[dict[str, str]] | None = None) -> ToolResult:
        cleaned = question.strip().strip("'\"?")
        if not cleaned:
            return ToolResult.failure("Use: ask knowledge <question>")
        answer = self.context.knowledge.answer(
            cleaned, retrieval_query=self._standalone_question(cleaned, history)
        )
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
            # A count is not an answer. "what files are here" used to reply "I found 200
            # file(s) in E:\projects\..." and name none of them.
            files = data["files"] if isinstance(data["files"], list) else []
            if not files:
                return f"There are no files in {data['root']}."
            shown = files[:12]
            total = data.get("total_files")
            headline = (
                f"**{total} file(s) in {data['root']}**"
                if isinstance(total, int) and total != len(files)
                else f"**{len(files)} file(s) in {data['root']}**"
            )
            lines = [headline, ""]
            # The breakdown is the answer to "how many python files are here", and
            # counting names out of a truncated listing is how that got answered wrong.
            breakdown = data.get("by_extension")
            if isinstance(breakdown, dict) and len(breakdown) > 1:
                top = list(breakdown.items())[:6]
                summary = " · ".join(f"`{ext}` {count}" for ext, count in top)
                if len(breakdown) > len(top):
                    summary += f" · +{len(breakdown) - len(top)} other types"
                lines.append("By type: " + summary)
                lines.append("")
            for entry in shown:
                if not isinstance(entry, dict):
                    continue
                name = Path(str(entry.get("path", ""))).name or str(entry.get("path", ""))
                size = entry.get("size_bytes")
                lines.append(f"- `{name}`" + (f" — {_readable_size(size)}" if isinstance(size, int) else ""))
            if len(files) > len(shown):
                lines.append(f"- …and {len(files) - len(shown)} more")
            return "\n".join(lines)
        if isinstance(data.get("text"), str) and data["text"].strip() and "path" in data:
            # `read file` / `extract text` put the content in data and the message named
            # only the path, so the thing the user asked to see was never shown.
            body = data["text"].strip()
            head = f"**{Path(str(data['path'])).name}**\n\n"
            return head + (body if len(body) <= 6000 else body[:6000] + "\n\n_(truncated)_")
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
            # The reminder commands write their own readable list; appending another one
            # here printed every reminder twice, the second time as a raw UTC timestamp.
            return result.message
        if "documents" in data and isinstance(data["documents"], list):
            docs = data["documents"]
            if not docs:
                return "Your knowledge base is empty. Index a file to get started."
            return f"You have {len(docs)} document(s) indexed: " + ", ".join(str(d.get("source", "")) for d in docs[:6]) + "."
        if isinstance(data.get("memory"), dict):
            return AgentOrchestrator._memory_text(data["memory"])
        return result.message

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
            parts.append("Here's what I remember: " + ", ".join(
                f"your {k.replace('_', ' ')} is {v}" for k, v in sorted(profile.items())) + ".")
        if notes:
            shown = notes[-12:]
            parts.append("Things you asked me to remember:\n" + "\n".join(f"- {note}" for note in shown)
                         + (f"\n…and {len(notes) - len(shown)} older." if len(notes) > len(shown) else ""))
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

    def _remember(self, expression: str) -> ToolResult:
        # "remember that I parked on level 3" is a note about parking, not "that I parked...".
        text = re.sub(r"^\s*that\s+", "", expression.strip(), flags=re.IGNORECASE).strip().rstrip(".!")
        if "=" not in text:
            # The key may carry an apostrophe: "my wife's birthday is june 5" missed this and
            # became an anonymous note, so "when is my wife's birthday" had no fact to find.
            natural = re.match(r"(?:my\s+|the\s+)?([\w' -]{1,40}?)\s+(?:is|are)\s+(.+)$", text, re.IGNORECASE)
            if natural and not re.match(r"(?:i|we|you|he|she|they|it|there|this|that)\b",
                                        natural.group(1), re.IGNORECASE):
                key = re.sub(r"\s+", "_", natural.group(1).strip().lower())
                value = natural.group(2).strip()
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
        """"put WhatsApp on the left and Chrome on the right" -> two placements.

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
                question=question, notes=notes, answered=False,
            )
        return ToolResult.success(answer, question=question, notes=notes,
                                  primary=bundle.data.get("primary"), answered=True)

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
        if len(commands) > 20:
            return ToolResult.failure("Run at most 20 subtasks in one batch.")
        slots = asyncio.Semaphore(4)
        async def run(command):
            async with slots:
                check_cancelled()
                return await asyncio.to_thread(lambda: asyncio.run(self._run_tracked_subtask(command)))
        results = await asyncio.gather(*(run(command) for command in commands), return_exceptions=True)
        check_cancelled()
        payload = []
        records = []
        for index, (command, result) in enumerate(zip(commands, results)):
            if isinstance(result, BaseException):
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
        succeeded = sum(1 for item in payload if item["ok"])
        lines = [f"**{verb} {len(payload)} subtask(s)** — {succeeded} succeeded", ""]
        for item in payload:
            mark = "ok" if item["ok"] else "failed"
            first = str(item.get("message", "")).strip().splitlines()
            lines.append(f"- `{item['command']}` — {mark}: {(first[0] if first else '')[:110]}")
        return ToolResult(
            ok=succeeded == len(payload),
            message=NL.join(lines),
            data={"results": payload, "dashboard": dashboard},
        )

    async def _run_tracked_subtask(self, command: str) -> ToolResult:
        agent_id = self.control_room.start(command)
        try:
            result = await self.handle(command)
        except (Exception, OperationCancelled) as exc:
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
        if agent_id == "run":   # "agent run" with its goal missing, not an agent named "run"
            return ToolResult.failure("Use: agent run <goal>  (e.g. 'agent run summarize the README and index it')")
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

    def _has_language_model(self) -> bool:
        return any(
            planner is not None and type(getattr(planner, "provider", None)).__name__ != "HeuristicPlannerProvider"
            for planner in (self.planner, self.smart_planner, self.ultra_planner, self.fallback_planner)
        )

    @staticmethod
    def _conversation_fallback(text: str) -> ToolResult:
        # Reached by a command no tool recognised, from a router or an agent step. It used
        # to claim no language model was connected, which was usually untrue.
        shown = " ".join((text or "").split())[:80]
        return ToolResult.failure(f"I don't know how to do that yet: “{shown}”. Say 'help' to see what I can do.",
                                  heard=text)
