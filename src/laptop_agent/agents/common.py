"""Helpers and constants shared by the parts of `AgentOrchestrator`, moved here unchanged from
agents/orchestrator.py when it was split by area."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from laptop_agent.audit import AuditLogger
from laptop_agent.autopilot import AutopilotTracker
from laptop_agent.jobs import JobTracker
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.planner import Planner
from laptop_agent.reasoning import AgentRunTracker
from laptop_agent.reminders import ReminderStore
from laptop_agent.timeparse import describe, spoken_to_digits
from laptop_agent.scheduler import SchedulerStore
from laptop_agent import supervisor as supervision
from laptop_agent.supervisor import RunStopped, RunTree
from laptop_agent.tasks import TaskTracker
from laptop_agent.tools.windows import WindowTool
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.jobright import JobrightTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.obsidian import ObsidianVault
from laptop_agent.tools.research import ResearchTool
from laptop_agent.tools.terminal import TerminalTool
from laptop_agent.tools.transcribe import TranscribeTool
from laptop_agent.tools.web import WebTool
from laptop_agent.tools.webcam import WebcamTool
from laptop_agent.tools.websearch import WebSearchTool
from laptop_agent.workflows import WorkflowTracker
from typing import Callable

from laptop_agent.audit import AuditLogger
from laptop_agent.autopilot import AutopilotTracker
from laptop_agent.jobs import JobTracker
from laptop_agent.knowledge import KnowledgeBase
from laptop_agent.memory import MemoryStore
from laptop_agent.planner import Planner
from laptop_agent.reasoning import AgentRunTracker
from laptop_agent.reminders import ReminderStore
from laptop_agent.timeparse import describe, spoken_to_digits
from laptop_agent.scheduler import SchedulerStore
from laptop_agent import supervisor as supervision
from laptop_agent.supervisor import RunStopped, RunTree
from laptop_agent.tasks import TaskTracker
from laptop_agent.tools.windows import WindowTool
from laptop_agent.tools.browser import BrowserAutomationTool
from laptop_agent.tools.desktop import DesktopTool
from laptop_agent.tools.email import EmailTool
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.jobright import JobrightTool
from laptop_agent.tools.music import MusicTool
from laptop_agent.tools.obsidian import ObsidianVault
from laptop_agent.tools.research import ResearchTool
from laptop_agent.tools.terminal import TerminalTool
from laptop_agent.tools.transcribe import TranscribeTool
from laptop_agent.tools.web import WebTool
from laptop_agent.tools.webcam import WebcamTool
from laptop_agent.tools.websearch import WebSearchTool
from laptop_agent.workflows import WorkflowTracker

NL = chr(10)


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
# The router handed the sentence back as a command, and no tool runs it. Answered "I don't know
# how to do that yet" until it was answered as conversation instead: "convert 100 usd to eur"
# never reached the live rate the same question gets phrased as "how much is 100 dollars in
# euros", and "translate hello to french" never reached a model that knows French.
_DECLINED = "The router named the sentence itself as a command; answered as conversation."
_INVENTED = "The router named a command no tool runs; the sentence was answered as conversation."


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

# "pay the bill due friday" was filed as "pay the bill due": the day went and its "due" stayed.
# A "due" after something to do goes with the day; "the rent is due" and "homework due" are the
# reminder itself. Telling them apart by length lost "pay rent due" (Codex's review of #252), so
# the action decides: a verb missing from this list keeps the user's words.
_DUE_CLAUSE = re.compile(r"\s+(?:that|which)(?:'?s|\s+is|\s+are)\s+due$", re.IGNORECASE)
_DUE_PREDICATE = re.compile(r"(?:\b(?:is|are|was|were|be|been)|'s|'re)\s+due$", re.IGNORECASE)
# A word that is as often a noun - "book report due" is the assignment - counts as the action only
# when what follows it says so: "book the hotel", "hand in the essay" (Codex's review of #252).
_DUE_ACTION = re.compile(
    r"(?:pay|send|submit|return|renew|finish|complete|cancel|upload|sign|buy|bring|apply|register|prepare"
    r"|write|read|fix|take|get|do|make|deliver|ship|collect|grade|finalize|finalise)\s+(?!due\b)\S"
    r"|(?:book|file|hand|turn|mail|email|post|review|order|drop|pick|check|update|print|mark|call|transfer"
    r"|deposit|study|clean)\s+(?:(?:in|off|up|out)\s+|(?:the|a|an|my|our|your|his|her|their|this|that)\s+)\S",
    re.IGNORECASE,
)


def _reminder_message(text: str, start: int, end: int) -> str:
    """What is left of a reminder once the time words are cut out of it.

    "to call mom at 6pm" -> "call mom". The span comes from the parser rather than a
    guess, so a time in the middle ("call mom at 6pm about the invoice") loses only the
    time and keeps both halves of the sentence.
    """
    joined = re.sub(r"\s+", " ", text[:start] + " " + text[end:]).strip(" ,.;:-")
    # The whole chain: "set a reminder for 6pm to call mom" leaves "for to call mom".
    joined = re.sub(r"^(?:(?:to|that|about|for|me\s+to)\s+)+", "", joined, flags=re.IGNORECASE)
    joined = re.sub(r"\s+(?:at|on|by|around|about|this|next)$", "", joined, flags=re.IGNORECASE)
    # Only after an action: "the rent that is due" is the reminder (Codex's review of #252).
    if _DUE_ACTION.match(joined):
        if _DUE_CLAUSE.search(joined):
            joined = _DUE_CLAUSE.sub("", joined)
        elif not _DUE_PREDICATE.search(joined):
            joined = re.sub(r"\s+due$", "", joined, flags=re.IGNORECASE)
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

_UNUSABLE_PATH = "That isn't a file name I can use — it is too long or contains characters no file can have."
_FILE_VERBS = ("read file ", "scan files ", "summarize file ", "ask file ", "extract text ", "file info ",
               "extract tables ", "analyze spreadsheet ", "process file ", "convert file ", "organize folder ",
               "ocr image ", "transcribe ", "describe image ", "describe video ", "index file ", "open file ", "file move ",
               "file copy ", "file rename ", "file delete ")
# Phone keyboards type "what’s", and every route is written with a straight apostrophe: all
# eight contract phrases with one went to the chat model when it was curly. A path keeps its
# own characters, since a file may really be named with one.
_CURLY_APOSTROPHES = str.maketrans({"’": "'", "‘": "'"})
# What "don't forget …" may become: something that reminds. Timers and alarms are reminders here.
_REMINDER_CREATION = re.compile(r"^\s*(?:remind\s+me|reminder\s+add|alarm|timer)\b", re.IGNORECASE)
# Everything from the first verb that takes a path on is left as typed. Checking only the
# start of the sentence (Codex's review of #186) folded the path in "please read file
# Jeevan’s notes.txt", and the file was then not found.
_PATH_VERB = re.compile(
    r"\b(?:" + "|".join(re.escape(verb.strip()) for verb in _FILE_VERBS) +
    r"|knowledge export|search files|save research report|open app|forecast|what drives|anomalies in)\b",
    re.IGNORECASE,
)


def _fold_apostrophes(command: str) -> str:
    """Straighten curly apostrophes in what was said, never in a path it names."""
    verb = _PATH_VERB.search(command)
    if verb is None:
        return command.translate(_CURLY_APOSTROPHES)
    return command[: verb.start()].translate(_CURLY_APOSTROPHES) + command[verb.start():]


# A quoted path: straight or curly, double or single. Windows' "Copy as path" puts double
# quotes around every path, and `read file "C:\...\notes.txt"` looked for a file whose name
# began with a quote. Only a span that looks like a path is unquoted - a quoted search phrase
# keeps its quotes - and only after a verb that takes one. A quote must stand at a word's
# edge, or the two apostrophes in `C:\Jeevan's docs\Sam's.txt` would read as a pair.
_QUOTED = re.compile(
    r"(?<!\S)(?:\"([^\"\r\n]+)\"|\u201c([^\u201d\r\n]+)\u201d|\u2018([^\u2019\r\n]+)\u2019|'([^'\r\n]+)')"
    r"(?=[\s.,;:?!]|$)"
)
_PATH_LIKE = re.compile(r"[\\/]|^~|\.[A-Za-z0-9]{1,5}$")


def _query_and_root(rest: str) -> tuple[str, str] | None:
    """`search files <query> <root>`, where either may hold spaces: the root is the longest
    tail that exists on disk. Split at the first space, "find the readme in this folder"
    searched for "the" in a folder called "readme ." and said it did not exist. With no tail
    on disk the root starts at the first thing shaped like a path, so a missing folder is
    named as itself; failing that, at the first space as before."""
    cuts = [space.end() for space in re.finditer(r"\s+", rest)]
    if not cuts:
        return None
    for cut in cuts:
        root = rest[cut:].strip()
        if root and os.path.exists(os.path.expanduser(root)):   # False, not an error, for a name Windows refuses
            return rest[:cut].strip().strip("'\"“”"), root
    cut = next((cut for cut in cuts if re.match(r"[A-Za-z]:|[~./\\]", rest[cut:])), cuts[0])
    return rest[:cut].strip().strip("'\"“”"), rest[cut:].strip()


def _clean_paths(command: str) -> str:
    """`read file "C:\\a b.txt"?` -> `read file C:\\a b.txt`, for every tool at once."""
    verb = _PATH_VERB.search(command)
    if verb is None:
        return command

    def unquote(match: re.Match[str]) -> str:
        inner = next(group for group in match.groups() if group is not None)
        return inner if _PATH_LIKE.search(inner.strip()) else match.group(0)

    command = command[: verb.end()] + _QUOTED.sub(unquote, command[verb.end():])
    # "?" cannot be in a Windows file name, so one straight after a path ends the question:
    # "read file notes.txt?" looked for "notes.txt?", and with "please" it missed the route.
    last = command.rsplit(None, 1)[-1]
    if last.endswith("?") and _PATH_LIKE.search(last.rstrip("?")):
        command = command.rstrip("?")
    return command


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
# "and in feet?" after a conversion, "what about london" after the time, "and halloween?" after
# a count of days: the same question again with one thing changed. Each is keyed to the exact
# shape of the answer before it, and needs a connecting word, so a bare word is not taken.
_CONVERTED = re.compile(r"(?P<left>[^\n=*]+?) = \*\*(?P<right>[^*\n]+)\*\*(?: \(US measure\))?")
_AMOUNT_AGAIN = re.compile(r"(?:and|what\s+about|how\s+about)\s+(?P<n>-?\d+(?:\.\d+)?)\s*"
                           r"(?P<unit>[a-z°][\w° .'-]{0,30}?)?\s*\??", re.IGNORECASE)
# "**9:00 AM PDT** in `America/Los_Angeles` is **9:30 PM IST** in `Asia/Kolkata`." and "`Asia/Tokyo`
# is **13 hours ahead of** you right now: ...", where either side may be "here" / "you".
_ZONES_ANSWER = re.compile(r"\*\*(?P<at>\d{1,2}:\d{2} [AP]M) [^*\n]+\*\* (?:in `(?P<a>[^`\n]+)`|here) is "
                           r"\*\*\d{1,2}:\d{2} [AP]M [^*\n]+\*\*(?: the next day| the day before)? "
                           r"(?:in `(?P<b>[^`\n]+)`|here)\.")
_GAP_ANSWER = re.compile(r"`(?P<a>[^`\n]+)` is (?:the same time as|\*\*[^*\n]+\*\*) "
                         r"(?:you|`(?P<b>[^`\n]+)`) right now: ")
_TIME_AGAIN = re.compile(r"(?:and|what\s+about|how\s+about)\s+(?:at\s+)?"
                         r"(?P<at>\d{1,2}(?::\d{2})?\s*[ap]\.?\s?m\.?|noon|midday|midnight)\s*\??", re.IGNORECASE)
_CONVERT_AGAIN = re.compile(r"(?:(?:and|what\s+about|how\s+about)\s+(?:(?:in|to|into)\s+)?|(?:in|to|into)\s+)"
                            r"(?P<unit>[a-z°][\w° .'-]{0,30}?)\s*\??", re.IGNORECASE)
# The label is "JST" for a zone asked for, and "Eastern Daylight Time" for the laptop's own on Windows.
_CLOCK_ANSWER = re.compile(r"\*\*\d{1,2}:\d{2} [AP]M\*\* [^*\n—]+ — ")
_ZONE_AGAIN = re.compile(r"(?:(?:and|what\s+about|how\s+about)\s+(?:(?:in|for|at)\s+)?|(?:in|for|at)\s+)"
                         r"(?P<place>[a-z][a-z .'/_-]{1,30}?)\s*\??", re.IGNORECASE)
_UNTIL_ANSWER = re.compile(r"\*\*(?P<count>[^*\n]+)\*\* until (?P<name>[^\n]+?) \([^()\n]+\)\.")
_UNTIL_IN = re.compile(r"(?:(?:and\s+|what\s+about\s+|how\s+about\s+)?(?:in|as)\s+|how\s+many\s+)"
                       r"(?P<unit>days|weeks|hours|minutes)(?:\s+is\s+that)?\s*\??", re.IGNORECASE)
_UNTIL_AGAIN = re.compile(r"(?:and|what\s+about|how\s+about)\s+(?:(?:until|till|to)\s+)?(?P<what>[^?\n]{1,40}?)\s*\??",
                          re.IGNORECASE)
_WEATHER_ANSWER = re.compile(r"\*\*[^*\n]+\*\* — [^\n]*°F \(feels like ")
_DAY_ANSWER = re.compile(r"\S[^\n]*? (?:is|was) on (?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day, ")
_PERCENT_ANSWER = re.compile(r"\((?P<p>-?\d+(?:\.\d+)?)/100 × (?P<of>-?[\d,]+(?:\.\d+)?)\) = \*\*")
_PERCENT_AGAIN = re.compile(r"(?:and|what\s+about|how\s+about)\s+(?:(?P<p>-?\d+(?:\.\d+)?)\s*(?:%|percent)"
                            r"|(?:of\s+)?(?P<of>-?\d[\d,]*(?:\.\d+)?))\s*\??", re.IGNORECASE)
_PRIME_ANSWER = re.compile(r"(?:Yes|No) — \*\*[\d,]+ is (?:not )?prime\*\*")
_NUMBER_AGAIN = re.compile(r"(?:and|what\s+about|how\s+about)\s+(?P<n>\d[\d,]*)\s*\??", re.IGNORECASE)
_DRAWN = re.compile(r"\*\*(?:Heads|Tails)\*\*\.|You rolled (?:an? \*\*\d+\*\*|[\d, ]+ — \*\*\d+\*\* in total)\."
                    r"|\*\*-?\d+\*\* \(between -?\d+ and -?\d+\)\.")
_DRAW_AGAIN = re.compile(r"(?:(?:ok(?:ay)?|and|now)\s+)?(?:again|one\s+more(?:\s+time)?|once\s+more|another(?:\s+one)?"
                         r"|do\s+it\s+again|(?:flip|roll|pick|toss)\s+(?:it\s+|them\s+|one\s+)?again)"
                         r"(?:\s+please)?\s*[?!.]*", re.IGNORECASE)


_PHRASE_WORD = re.compile(r"\d|\b(?:a|an|the|some|of|for|with|to)\b", re.IGNORECASE)
# The answers to "Add eggs, milk and bread to your shopping list as 3 items? Say yes, or no ...".
_UNCLEAR_ITEMS = re.compile(r'Add (?P<split>.+) to your (?P<name>.+?) list as \d+ items\? Say yes, no to add '
                            r'"(?P<said>.+)" as you said it, or say them again with "and" between\.')
# Read with the punctuation dropped: "yes, please" was taken as the items "yes" and "please" (Codex).
# An answer may be several of its own phrases: "yes, three items", "no, as I said it" (Codex).
_POLITE = r"please|thanks|thank\s+you"
_YES = (r"yes|yeah|yep|yup|sure|ok(?:ay)?|correct|right|separately|split\s+(?:them|it)"
        r"|(?:as\s+)?(?:\d+|two|three|four|five|six|separate)\s+(?:items|things)")
_NO = r"no(?:pe)?|nah|as\s+i\s+said(?:\s+it)?|(?:as\s+)?one\s+item|keep\s+it(?:\s+as\s+it\s+was)?"
# Joined by a pause (the comma, dropped), "and" or "or": "yes and no" was taken as two items (Codex).
_JOIN = r"\s+(?:(?:and|or)\s+)?"
_SPLIT_THEM = re.compile(rf"(?:{_YES})(?:{_JOIN}(?:{_YES}|{_POLITE}))*", re.IGNORECASE)
_KEEP_THEM = re.compile(rf"(?:{_NO})(?:{_JOIN}(?:{_NO}|{_POLITE}))*", re.IGNORECASE)
_YES_AND_NO = re.compile(rf"(?:{_YES}|{_NO})(?:{_JOIN}(?:{_YES}|{_NO}|{_POLITE}))*", re.IGNORECASE)


def _meaningful(value: str) -> bool:
    """Whether a reply carries an answer: "it's" and "to" alone do not - they were filed as
    a name and as a reminder."""
    words = re.findall(r"[\w'-]+", value.lower())
    return bool(words) and any(word not in _FILLER_REPLY for word in words)


# Direct commands whose argument is free text: an "and" inside it belongs to it.
_WHOLE_ARGUMENT = frozenset({
    "email", "send", "remember", "note", "document", "image", "research", "solve", "ask", "agent", "autopilot",
    "workflow", "multi", "schedule", "run", "terminal", "shell", "write", "draft", "summarize", "translate",
    "workspace",
})
_REMINDER_REMOVE = re.compile(r"reminder (?:delete|cancel|remove)(?: |$)")
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


def _job_line(job: dict) -> str:
    """What a scheduled job does, as said: a repeating reminder is its words, not its command."""
    spec = str(job.get("spec", ""))
    if spec.lower().startswith("reminder add now "):
        text = f"reminder: {spec[len('reminder add now '):]}"
    elif job.get("kind") == "agent":
        text = f"agent: {spec}"
    else:
        text = spec
    return text if job.get("enabled", True) else f"{text} (paused)"


# Words that end a phrasal verb: the last word of "stand up" names nothing on its own, and
# matched as a substring it would also cancel "eat supper".
_PARTICLES = frozenset({"up", "down", "in", "out", "on", "off", "over", "away", "back", "around", "through"})


def _reminder_name(message: str) -> str:
    words = message.split()
    if len(words) > 1 and words[-1].lower().strip(".,!?") in _PARTICLES:
        return " ".join(words[-2:])
    return words[-1] if words else message


def _reminder_line(item: dict, now: datetime) -> str:
    """"- #3 today at 6:22 AM — check the oven", instead of a raw UTC timestamp."""
    try:
        due = datetime.fromisoformat(str(item.get("due_at", "")))
        when = describe(due, now, local=True) + (" (overdue)" if due <= now else "")
    except ValueError:
        when = str(item.get("due_at", ""))
    return f"- #{item.get('id')} {when} — {item.get('message')}"


def _answers(planner: Planner | None) -> bool:
    """Whether a tier's planner has a model that can answer an agent's prompt."""
    return planner is not None and getattr(planner.provider, "answer", None) is not None


def _too_often(schedule) -> bool:
    return schedule.kind == "interval" and schedule.seconds < supervision.MIN_AGENT_INTERVAL


def _agent_schedule_refused(schedule, jobs) -> str:
    """Why a new scheduled agent may not be added: more often than hourly, or one too many."""
    if _too_often(schedule):
        return "A scheduled agent may run at most once an hour: try 'every 1 hour' or 'daily at 08:00'."
    if sum(job.kind == "agent" and job.enabled for job in jobs) >= supervision.MAX_AGENT_SCHEDULES:
        return (f"There are already {supervision.MAX_AGENT_SCHEDULES} scheduled agents; remove one first "
                "(schedule remove <id>).")
    return ""


def _counted(brain: Callable[[str], str], tree: RunTree) -> Callable[[str], str]:
    """An agent's `decide`, counted against its run's model calls (`MAX_MODEL_CALLS`)."""

    def decide(prompt: str) -> str:
        stopped = tree.count_call()
        if stopped is not None:
            raise RunStopped(f"Stopped by the supervisor: {stopped.reason}.")
        return brain(prompt)

    return decide


def _readable_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"
