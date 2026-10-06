from __future__ import annotations

import re
from datetime import datetime

from laptop_agent.planner.core import PlanDecision
from laptop_agent.recordings import recording_seconds
from laptop_agent.timeparse import TimeParseError, parse_when, spoken_to_digits
from laptop_agent.tools.calculator import _NUMBER_WORD, _words_to_number
from laptop_agent.tools.chance import is_chance_request
from laptop_agent.tools.music import is_personal_message_target
from laptop_agent.tools.weather import clean_place
from laptop_agent.tools.windows import LAYOUTS as _LAYOUTS, _ALIASES as _LAYOUT_ALIASES

# Built from the tool's own vocabulary, never hand-written. The previous list here was a
# copy that had already drifted: it had `left` and `third` but none of `top left`,
# `bottom right`, `left third` or `right half`, so "notepad on the top left" could not
# route even though the tool parses it perfectly. A hand-maintained copy of a list fails
# by omission from the copy — the same way `_TOOL_SIGNALS` did. Longest first so
# "top left" wins over "left". `windows.py` is stdlib-only at import (its ctypes layer is
# behind an injectable backend), so this costs nothing.
_POSITION_WORD = "(?:" + "|".join(
    re.escape(phrase).replace(r"\ ", r"\s+")
    for phrase in sorted(set(_LAYOUTS) | set(_LAYOUT_ALIASES), key=len, reverse=True)
) + ")"
# Arranging windows, as it is actually said out loud. `window`/`split`/`snap`/`arrange`
# are already direct command prefixes, so this only has to catch the natural phrasings:
# "put X on the left", "move X to the top right", "maximise X", "left side X right side Y".
# The whole sentence, with a short name: a verb with a position anywhere after it sent 22 of
# 24 ordinary sentences here - "minimize distractions while studying", "center the text in
# css", "move the decimal point two places to the left" - and each got "no window matches".
# A word no window is named after ends the name, so "minimize the number of api calls" and
# "put the most important point at the top" are not names.
_NOT_IN_A_WINDOW_NAME = (r"(?:a|an|the|my|your|our|their|his|her|its|this|that|these|those|it|me|you|us|them|"
               r"yourself|myself|while|with|for|to|of|in|on|at|by|from|about|given|into|onto|and|or|but|"
               r"so|if|when|then|than|as|is|are|was|were|be|over|under|up|down|forward|back|number|most)")
_WINDOW_NAME = (r"(?:(?:the|my)\s+)?(?!" + _NOT_IN_A_WINDOW_NAME + r"\b)[a-z0-9][\w.+#-]*"
                r"(?:\s+(?!" + _NOT_IN_A_WINDOW_NAME + r"\b)[a-z0-9][\w.+#-]*){0,2}")
_PLACE_AT = (r"(?:\s+(?:to|on|at|in|into|onto)(?:\s+the)?)?\s+" + _POSITION_WORD
             + r"(?:\s+(?:side|half|third|corner))?(?:\s+(?:of|on)\s+(?:the|my)\s+(?:screen|monitor|display))?")
_ARRANGE_VERB = r"(?:put|move|place|send|shift|drag|split|snap|arrange|resize|tile)"
_ARRANGE_ASK = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+(?:you|u)\s+|please\s+|i\s+(?:want|need)\s+)?(?:jarvis[,\s]+)?(?:"
    + _ARRANGE_VERB + r"\s+" + _WINDOW_NAME + _PLACE_AT
    + r"(?:\s*,?\s*(?:and\s+(?:then\s+)?|then\s+)?(?:" + _ARRANGE_VERB + r"\s+)?" + _WINDOW_NAME + _PLACE_AT + r")*"
    r"|(?:maximi[sz]e|minimi[sz]e|centre|center)\s+" + _WINDOW_NAME
    + r"|" + _POSITION_WORD + r"\s+side\s+" + _WINDOW_NAME
    + r"(?:\s*,?\s*(?:and\s+)?" + _POSITION_WORD + r"\s+side\s+" + _WINDOW_NAME + r")*"
    r")(?:\s+(?:please|now|for\s+me))?[\s?.!]*$",
    re.IGNORECASE,
)
# "split screen", "split windows", "side by side" anywhere in the sentence is unambiguous
# about intent even when the grammar is not — spoken and transcribed, this arrived as
# "And Chrome on right using split windows function", which starts with neither a verb nor
# a position and so matched nothing above.
_ARRANGE_PHRASE = re.compile(
    r"\bsplit\s*(?:the\s+)?(?:screen|windows?|view)\b|\bside\s+by\s+side\b|\bsnap\s+layout\b",
    re.IGNORECASE,
)
# Name-then-position with no verb at all: "whatsapp on the left and chrome on the right".
# The tool already parses this correctly — only the router never sent it.
#
# Matching a bare `<name> on the <position>` is what makes this dangerous, so three things
# hold it in: every clause needs an explicit preposition (without one, "turn left and then
# right" reads as two placements); there must be TWO or more clauses, because a lone
# placement is where ordinary prose lives ("my keys are on the right"); and the pattern
# must consume the WHOLE sentence, which is what rejects "the chrome finish on the right
# handle is worn" and "what is on the left side of the brain" — both have words left over.
# A question or a decision is refused outright below, since "should i put the legend on the
# right" is a fullmatch and belongs to the advisor.
# Measured over 1469 real sentences from this repo's own docs: zero matches.
# A name ending in a copula is a sentence about something, not the name of a window.
# "in the middle" is ordinary English — "the value is in the middle and the key is on the
# left" fullmatches everything above and is not a request to move anything. No window is
# called "the value is". This narrows the class rather than closing it ("the answer lies
# in the middle and the question is on the left" still gets through with a different
# verb); the residual cost is one harmless, self-reporting tool call.
_ARRANGE_NOT_A_NAME = r"(?<!\bis)(?<!\bare)(?<!\bwas)(?<!\bwere)(?<!\bsits)(?<!\blies)(?<!\bgoes)"
_ARRANGE_CLAUSE = (
    r"[\w'.+-]+(?:\s+[\w'.+-]+){0,2}" + _ARRANGE_NOT_A_NAME
    + r"\s+(?:on|to|in|at)\s+(?:the\s+)?"
    + _POSITION_WORD + r"(?:\s+(?:side|half|hand\s+side))?"
)
_ARRANGE_PLACEMENTS = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+(?:you|u)\s+|please\s+"
    r"|i\s+(?:want|need|would\s+like)\s+(?:to\s+)?)?"
    + _ARRANGE_CLAUSE + r"(?:\s*(?:,|and|&)\s*" + _ARRANGE_CLAUSE + r")+\s*[.!]?\s*$",
    re.IGNORECASE,
)

# "a ppt for the solar system", "slides on rust" — a deck names its format at the front,
# where a document names it at the end ("... as a pdf"). Kept in step with
# `tools.document._DECK_HEAD`, which is what actually splits the subject off.
_DECK_ASK = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+you\s+|please\s+)?"
    r"(?:make|create|build|write|prepare|generate|do|throw\s+together|whip\s+up)?\s*(?:me\s+)?(?:an?\s+)?"
    r"(?:pptx|ppt|power\s*point|slide\s*deck|slides|deck|presentation)"
    r"(?:\s+(?:file|deck|presentation|slides))?\s+(?:for|on|about|of|covering|regarding)\s+\S",
    re.IGNORECASE,
)


_DOC_HEAD = re.compile(
    r"^\s*(?:can\s+you\s+|could\s+you\s+|please\s+)?(?:make|create|write|generate|build|prepare|draft|export)\s+"
    r"(?:me\s+)?(?:an?\s+)?(?P<length>(?:(?:\d{1,2}|one|two|three|four|five|single)[\s-]+pages?|one[\s-]pager)\s+)?"
    r"(?P<kind>pdf|word\s+doc(?:ument)?|docx|doc|markdown\s+(?:file|doc)|md\s+file)"
    r"(?:\s+(?:file|document))?\s+(?:about|on|for|of|covering|regarding|summari[sz]ing)\s+(?P<topic>\S.*?)\s*[.!]*$",
    re.IGNORECASE,
)

# --- Is this a plain question, or a request to do something? -------------------------
# Asking a model to classify a question costs a round-trip and carries the whole command
# vocabulary (~1800 tokens) in the prompt. Worse, measured on real turns, the router sent
# ordinary knowledge questions to the `solve` research pipeline: "how is a hash map
# different from a b-tree index" took 82 seconds and never streamed a token. A question
# with nothing actionable in it can be answered directly.
_ASKING = re.compile(
    r"^\s*(?:what|whats|what's|why|how|when|who|whom|whose|which|is|are|was|were|do|does|did"
    r"|explain|compare|describe|define|tell me)\b",
    re.IGNORECASE,
)
# Decisions belong to the advisor, which researches and recommends — do not shortcut those.
_DECIDING = re.compile(
    r"^\s*(?:should|would|could|can)\s+(?:i|we)\b"
    r"|\bbest way to\b|\bpros and cons\b|\bworth (?:it|the)\b|\bis it better to\b",
    re.IGNORECASE,
)
# Anything naming a tool, a destination, or the user's own data goes to the real router.
# Note the trailing `s?`. Without it `reminder\b` does not match "reminders", and only
# `files`, `notes` and `jobs` were ever written in the plural — so "do i have any drafts /
# documents / tasks / downloads / screenshots / workflows / reminders" all read as plain
# knowledge questions and were answered by the chat model, which cannot see any of them.
# A false positive here is harmless by design: it costs one routing call, and the router
# is exactly where ambiguous text is supposed to go.
_TOOL_SIGNALS = re.compile(
    r"\b(?:file|files|folder|directory|path|inbox|email|mail|gmail|send|reply|draft|download"
    r"|upload|open|launch|run|shell|terminal|browser|website|url|link|schedule|remind|reminder"
    r"|calendar|task|todo|note|notes|vault|obsidian|remember|memory|knowledge|search|google"
    r"|weather|forecast|temperature|distance|route|trip|map|directions|hotel|restaurant|job"
    r"|jobs|resume|apply|application|pipeline|transcribe|ocr|screen|screenshot|webcam|camera"
    r"|youtube|video|image|picture|draw|photo|document|pdf|docx|csv|spreadsheet|music|play"
    r"|volume|agent|autopilot|workflow|research|solve|my|mine|our)s?\b",
    re.IGNORECASE,
)
# Asking to see the reminder list. The `^` is in the pattern, not left to the caller's
# `.match()`, so "remind me to tell bob to check my reminders" cannot match it under a
# later `.search()` and have the reminder silently turned into a listing — and so it
# reads the same way as `_ARRANGE_ASK` and `_DECK_ASK` above. Creations are handled
# further down. It requires the plural (or an explicit "my reminder") so "what is a
# reminder" stays a definition question.
# The polite prefix is the same one `_ARRANGE_ASK` carries: `strip_address` removes
# greetings and the wake word but not "can you", so without it the commonest spoken form
# of all — "can you show me my reminders" — missed, which is the very bug this fixes.
_REMINDER_ASK = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+(?:you|u)\s+|please\s+|i\s+(?:want|need)\s+(?:to\s+)?)?"
    r"(?:what(?:'s|s| is| are)?|which|do i have|have i got|any|show|list|check|see|view"
    r"|read(?:\s+out)?|pull\s+up|give me|tell me|got)\b[^?]{0,40}?\b(?:reminders|my reminder)\b",
    re.IGNORECASE,
)
_REMINDER_BARE = re.compile(r"(?:all\s+|my\s+|all\s+my\s+|the\s+)?reminders(?:\s+list)?",
                            re.IGNORECASE)
# How a request is softened before it starts, said every way at once: "can you please set
# a timer" missed a prefix that allowed "can you" or "please" but not both.
_POLITE = (r"^\s*(?:(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+)?|please\s+|would\s+you\s+mind\s+"
           r"|kindly\s+)?")
# Asking to see the scheduler, as a whole sentence. "what are my scheduled jobs" reached the LLM
# router, which matched "jobs" and answered from the job-application tracker.
_SCHEDULE_ASK = re.compile(
    r"(?:" + _POLITE + r"(?:(?:what(?:'s|s|\s+is|\s+are)|show(?:\s+me)?|list|see|view|check|give\s+me|tell\s+me"
    r"|do\s+i\s+have(?:\s+any)?)\s+)?(?:all\s+)?(?:(?:my|the)\s+)?"
    r"(?:scheduled\s+(?:jobs?|tasks?|commands?)|schedules|recurring\s+(?:jobs?|tasks?))"
    r"|" + _POLITE + r"what(?:'s|s|\s+is)\s+scheduled"
    r"|" + _POLITE + r"what\s+(?:jobs?|tasks?)\s+(?:are|do\s+i\s+have)\s+scheduled)\s*[?.!]*",
    re.IGNORECASE,
)
# Timers, alarms and managing reminders, read after spoken numbers become digits.
_DURATION = r"(?:\d+(?:\.\d+)?|\ban?|\bhalf\s+an?)[\s-]*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b"
_TIMER_ASK = re.compile(
    _POLITE + r"(?:(?:set|start|put\s+on|make|create|run|give\s+me)\s+)?(?:me\s+)?(?:an?\s+)?"
    r"(?:" + _DURATION + r"\s+(?:[a-z]+\s+)?timer\b|timer\b|[a-z]+\s+timer\b|count\s*down\b|countdown\b)",
    re.IGNORECASE,
)
_ALARM_ASK = re.compile(
    _POLITE + r"(?:set\s+(?:an?\s+|my\s+|the\s+)?alarm|wake\s+me(?:\s+up)?|get\s+me\s+up|alarm)\s+(?:for\s+|at\s+|to\s+)?"
    r"(?P<when>.+?)\s*[.!?]*$",
    re.IGNORECASE,
)
# "set a timer" with no length: ask for one rather than let a model claim it set one.
_TIMER_BARE = re.compile(_POLITE + r"(?:set|start)\s+(?:a|the|my)\s+timer(?:\s+please)?\s*[.!?]*$|^\s*timer\s*$",
                         re.IGNORECASE)
_TIMER_LEFT = re.compile(r"\b(?:how\s+(?:much\s+time|long)\s+(?:is\s+)?(?:left|remaining)|time\s+left)\s+on\s+"
                         r"(?:my|the)\s+(?P<which>\w+\s+)?timer\b", re.IGNORECASE)
# Letting go of one: "never mind the timer", "i don't need the alarm anymore", "stop
# reminding me about the oven".
_LET_GO = re.compile(
    r"^\s*(?:i\s+don'?t\s+need|never\s*mind|forget(?:\s+about)?|scrap|ditch|kill)\s+(?:the|my|that)\s+"
    r"(?:(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,2})\s+)?"
    r"(?P<kind>timer|alarm|reminder)(?:\s+(?:about|to|for)\s+(?P<about>.+?))?(?:\s+any\s*more)?\s*[.!]*$"
    r"|^\s*stop\s+reminding\s+me\s+(?:about|to)\s+(?P<topic>.+?)\s*[.!]*$",
    re.IGNORECASE,
)
_TIME_TOKEN = re.compile(r"\d|\b(?:noon|midnight|morning|tomorrow|tonight)\b", re.IGNORECASE)
# Asking to look at the screen, as a whole sentence. "my screen" anywhere took a screenshot
# and sent it to the vision model for "my screen is cracked, what should i do", "dim my
# screen brightness" and "i can't read the screen, it's too bright".
# Asking to be looked at through the camera, as a whole sentence, or a request that opens by
# naming it. The capture needs no approval, and "what do you see" anywhere turned the camera on
# for "what do you see in this code" and sent the frame to the vision model.
_WEBCAM_ASK = re.compile(
    "|".join(_POLITE + branch for branch in (
        r"(?:(?:take\s+a\s+)?look\s+at\s+me|what\s+(?:do|can)\s+you\s+see|(?:can|do)\s+you\s+see\s+me"
        r"|look\s+(?:through|at)\s+the\s+camera)(?:\s+(?:now|right\s+now))?"
        r"(?:\s+and\s+(?:tell|describe|say|explain)\b[^?!]*)?[\s?.!]*$",
        r"use\s+(?:the|my)\s+(?:webcam|camera)\b",
    )),
    re.IGNORECASE,
)
_SCREEN_ASK = re.compile(
    "|".join(_POLITE + branch + r"(?:\s+and\s+(?:tell|describe|read|explain|summari[sz]e)\b[^?!]*)?[\s?.!]*$" for branch in (
        r"(?:(?:take\s+a\s+)?look\s+at|read|check|view|describe|scan|see)\s+(?:what(?:'s|s|\s+is)\s+on\s+)?"
        r"(?:my|the|this)?\s*screen(?:\s+(?:for\s+me|now|right\s+now))?",
        r"what(?:'s|s|\s+is)\s+on\s+(?:my|the)\s+screen(?:\s+(?:right\s+)?now)?",
        r"what\s+(?:do|can)\s+you\s+see\s+on\s+(?:my|the)\s+screen",
        r"screen\s+text",
    )),
    re.IGNORECASE,
)
# Asking for your own mail, as a whole sentence. Matched as words anywhere, 14 of 18
# sentences that only mentioned email read the inbox: "give me a template for a follow up
# email", "summarize the history of email", "write a new email to my landlord".
_MAIL = r"(?:e-?mails?|mail|inbox)"
_MAIL_KIND = r"(?:(?:new|unread|latest|recent|important|priority)\s+)*"
_MAIL_TAIL = (
    r"(?:\s+(?:from\s+|for\s+)?(?:today|yesterday|this\s+(?:morning|afternoon|week))"
    r"|\s+(?:from|in|over|for)\s+the\s+(?:last|past)\s+(?:\d+\s+|few\s+|couple\s+(?:of\s+)?)?(?:days?|hours?|weeks?))?"
    r"(?:\s+(?:in|on)\s+(?:gmail|google|outlook|microsoft(?:\s+mail)?))?[\s?.!]*$"
)
_MAIL_LEAD = _POLITE + r"(?:for\s+(?:the\s+)?(?:last|past)\s+\d+\s+days?\s*[-,:]\s*)?"
_MAIL_ASK = re.compile(
    "|".join(_MAIL_LEAD + branch + _MAIL_TAIL for branch in (
        r"(?:do\s+i\s+have|have\s+i\s+got|did\s+i\s+get|are\s+there|is\s+there|got)\s+(?:any\s+|an?\s+)?"
        + _MAIL_KIND + _MAIL + r"(?:\s+(?:for\s+me|in\s+my\s+inbox))?",
        r"any\s+" + _MAIL_KIND + _MAIL,
        r"(?:new|unread)\s+" + _MAIL_KIND + _MAIL,
        r"(?:check|read|show|get|give|fetch|find|pull\s+up|bring\s+up)\s+(?:me\s+)?(?:for\s+)?"
        r"(?:my\s+|the\s+|all\s+(?:my\s+|the\s+)?)?(?:any\s+)?" + _MAIL_KIND + _MAIL,
        r"what(?:'s|s|\s+is)\s+(?:new\s+)?in\s+my\s+" + _MAIL,
    )),
    re.IGNORECASE,
)
_MAIL_DIGEST_ASK = re.compile(
    _MAIL_LEAD + r"(?:summari[sz]e|recap|digest|tl;?dr(?:\s+of)?"
    r"|give\s+me\s+(?:a|an)\s+(?:quick\s+)?(?:summary|digest|overview|recap)\s+of)\s+"
    r"(?:my\s+|the\s+|all\s+(?:my\s+)?)?" + _MAIL_KIND + _MAIL + _MAIL_TAIL,
    re.IGNORECASE,
)
# A time said BEFORE "remind me": "every monday at 9 remind me to file my timesheet". Only
# the words after "remind me" were kept, so the reminder lost its time and was refused.
# Nothing but time words may stand there; anything else keeps the old reading.
_DAY_WORD = r"(?:mon|tues|wednes|thurs|fri|satur|sun)days?"
_TIME_FIRST = re.compile(
    rf"(?:(?:(?<!\d)\d{{1,2}}(?::\d{{2}})?(?!\d)|[ap]\.m\.|{_DURATION}"
    r"|(?:am|pm|at|on|in|by|every|each|the|next|this|and|a|an|tomorrow|tonight|today|morning|afternoon"
    rf"|evening|night|noon|midnight|day|week|daily|hourly|weekdays?|weekends?|{_DAY_WORD})\b)[\s,]*)+",
    re.IGNORECASE,
)
_PREFIX_POLITE = re.compile(r"[\s,]*(?:(?:can|could|would|will)\s+(?:you|u)|please|kindly)(?:\s+please)?\s*$",
                            re.IGNORECASE)
# "tell me at 3pm to join the call" asks for a reminder, but starts like a question and was
# answered as one. Only when a time comes first: "tell me in one sentence how to cook rice"
# and "tell me at least three reasons to learn rust" are questions.
_TELL_ME_AT = re.compile(
    _POLITE + r"tell\s+me\s+(?P<when>(?:at\s+(?:\d|noon\b|midnight\b)|in\s+" + _DURATION
    + rf"|tomorrow\b|tonight\b|this\s+(?:morning|afternoon|evening)\b|on\s+{_DAY_WORD}\b|every\b)[^,]*?)"
    r",?\s+(?P<rest>(?:to|about|that)\b.+)$",
    re.IGNORECASE,
)
_SNOOZE = re.compile(
    r"^\s*(?:please\s+)?snooze(?:\s+(?:it|that|this|the\s+(?:reminder|alarm|timer)|(?:reminder|alarm)"
    r"\s+#?(?P<id>\d+)))?(?:\s+for)?(?:\s+(?:another\s+)?(?P<minutes>\d+)\s*(?:more\s+)?(?:minutes?|mins?|m))?"
    r"(?:\s+more)?\s*[.!]*$",
    re.IGNORECASE,
)
# "how long does it take to drive to chicago (from boston)", "how long is the drive from a to b".
_DRIVE = re.compile(
    _POLITE + r"(?:tell\s+me\s+)?(?:how\s+long\s+(?:(?:does|would|will)\s+it\s+take|it\s+(?:takes|would\s+take))"
    r"(?:\s+me)?\s+to\s+drive|how\s+long\s+is\s+the\s+drive|how\s+long\s+of\s+a\s+drive\s+is\s+it"
    r"|(?:what(?:'s|s|\s+is)\s+the\s+)?driv(?:e|ing)\s+time|how\s+far\s+is\s+it)"
    r"(?:\s+from\s+(?P<start>.+?))?\s+to\s+(?P<end>.+?)(?:\s+from\s+(?P<start2>.+?))?[\s?.!]*$",
    re.IGNORECASE,
)
# "stop" and "cancel" are different requests: "stop the alarm" means the one ringing, and
# with nothing ringing it must not delete a weekday alarm - which it did.
_CANCEL = re.compile(
    _POLITE + r"(?P<verb>delete|cancel|remove|drop|stop|turn\s+off|shut\s+off|dismiss|silence|clear|wipe|erase"
    r"|get\s+rid\s+of)\s+(?:the\s+|my\s+|this\s+)?(?P<rest>.+?)\s*[.!]*$",
    re.IGNORECASE,
)
_STOP_VERBS = {"stop", "turn off", "shut off", "dismiss", "silence"}
# "what's my next reminder", "when is my alarm", "what time is my alarm set for"
_NEXT_ASK = re.compile(
    r"^\s*(?:(?:what(?:'s|s|\s+is)|when(?:'s|s|\s+is)|show\s+(?:me\s+)?)\s+(?:my\s+|the\s+)?next\s+"
    r"(?P<kind>reminder|alarm|timer)"
    r"|what\s+time\s+is\s+my\s+(?P<alarm>alarm)(?:\s+set\s+for)?"
    r"|when\s+(?:does|will)\s+my\s+(?P<goes>alarm|timer)\s+go\s+off"
    r"|is\s+my\s+(?P<set>alarm)\s+set)\s*[?.!]*$",
    re.IGNORECASE,
)
# A list edit that names no list: "delete milk from my list". Which list it means depends
# on what lists exist, which only the assistant knows, so the sentence is passed on whole.
_NAMELESS_REMOVE = re.compile(_POLITE + r"(?:remove|delete|take|cross|scratch|strike)\s+(?P<items>.+?)\s+"
                              r"(?:off(?:\s+of)?|from)\s+(?:my|the|our)\s+list\s*[.!]*$", re.IGNORECASE)
_NAMELESS_ADD = re.compile(_POLITE + r"(?:add(?:ing)?|put(?:ting)?)\s+(?P<items>.+?)\s+(?:to|on|onto)\s+"
                           r"(?:my|the|our)\s+list\s*[.!]*$", re.IGNORECASE)
# "set the volume to 50", "volume 30%", "turn the volume to 20 percent"
_VOLUME_LEVEL = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+you\s+|please\s+)?(?:(?:set|change|put|turn|make)\s+(?:the\s+)?volume"
    rf"\s+(?:to|at)|volume(?:\s+(?:to|at))?)\s+(?P<level>\d{{1,3}}|{_NUMBER_WORD.pattern})\s*(?:%|percent)?(?:\s+please)?\s*[.!]*$",
    re.IGNORECASE,
)


def nameless_list_edit(text: str) -> tuple[str, str] | None:
    """("add"|"remove", the items) for a list edit that names no list, else None."""
    for verb, pattern in (("remove", _NAMELESS_REMOVE), ("add", _NAMELESS_ADD)):
        match = pattern.match(text or "")
        if match:
            return verb, match.group("items").strip()
    return None
# Media control only when the whole message IS the control. "pause" and "resume" used to
# match anywhere in the text, so "update my resume", "how to write a good resume" and "what
# does pause mean" all toggled playback. Measured by driving a conversational corpus
# through the real orchestrator; those four were the only media commands it produced.
# "next"/"skip" need their noun: "next" alone is as likely to mean the next question.
_MEDIA_NOUN = r"(?:the\s+|this\s+|my\s+|that\s+)?(?:music|song|track|video|playback|audio|player|tune)"
_MEDIA_END = r"(?:\s+please)?\s*[.!]*\s*$"
_MEDIA_KEYS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(_POLITE + pattern + _MEDIA_END, re.IGNORECASE), key)
    for pattern, key in (
        (rf"(?:pause|unpause|play\s*/\s*pause)(?:\s+(?:it|{_MEDIA_NOUN}))?", "playpause"),
        (rf"(?:resume|continue)\s+(?:playing|{_MEDIA_NOUN})", "playpause"),
        (r"(?:play\s+(?:the\s+)?)?next\s+(?:song|track|video)|skip\s+(?:this\s+|the\s+)?(?:song|track|video)",
         "next"),
        (r"(?:play\s+(?:the\s+)?)?(?:previous|last|prior)\s+(?:song|track|video)|go\s+back\s+a\s+(?:song|track)",
         "previous"),
        (rf"stop\s+(?:playing|{_MEDIA_NOUN})", "stop"),
        (r"(?:volume\s+up|turn\s+(?:it|the\s+(?:volume|music|sound))\s+up|louder"
         r"|(?:increase|raise)\s+(?:the\s+)?volume)", "volumeup"),
        (r"(?:volume\s+down|turn\s+(?:it|the\s+(?:volume|music|sound))\s+down|quieter|softer"
         r"|(?:decrease|lower|reduce)\s+(?:the\s+)?volume)", "volumedown"),
        (r"(?:mute|unmute)(?:\s+(?:it|the\s+(?:sound|audio|volume|music)))?", "mute"),
    )
)
# "play X" only as a request that starts with it. The substring version sent "how do i play
# chess" and "how do i play guitar better" to YouTube.
_PLAY = re.compile(
    _POLITE + r"(?:open\s+[\w.]+\s+and\s+)?"
    r"(?:play|put\s+on|start\s+playing|i\s+(?:want|wanna|would\s+like)\s+to\s+"
    r"(?:hear|listen\s+to)|let\s+me\s+hear)\s+(?:me\s+)?(?:some\s+)?(?:music\s+(?=\S))?"
    r"(?P<target>.+?)(?:\s+please)?\s*[.!?]*\s*$",
    re.IGNORECASE,
)
# Things people "play" that are not music.
_NOT_MUSIC = re.compile(
    r"\b(?:games?|chess|trivia|quiz|questions|tic[\s-]?tac[\s-]?toe|rock[\s,]+paper|hide\s+and\s+seek"
    r"|with\s+me|along|role|devil'?s\s+advocate|pretend|dumb|fair|it\s+safe|it\s+cool)\b",
    re.IGNORECASE,
)
# Small talk, recognised as the WHOLE message. The old test was a substring search for
# "hi ", "hey " and "hello", which matched "sushi ", "they ", "whey " and "othello" - and
# without a model, or on any client that does not stream, the canned greeting was the
# answer: "i want sushi for dinner, any ideas?" got "I am here and ready. I can help with
# files...". A reply here is a fallback; with a model configured the chat tier answers.
SMALL_TALK = "small-talk"
_SMALL_TALK: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(r"^\s*" + pattern + r"(?:\s*,?\s*(?:jarvis|buddy|mate|man))?[\s!.,?]*$", re.IGNORECASE), reply)
    for pattern, reply in (
        (r"(?:hi|hello|hey|hiya|howdy|yo|greetings|hi\s+there|hello\s+there|hey\s+there|jarvis)",
         "Hello! What can I do for you?"),
        (r"good\s+(?:morning|afternoon|evening)", "Hello! What can I do for you?"),
        (r"(?:how\s+are\s+you(?:\s+doing)?(?:\s+today)?|how's\s+it\s+going|how\s+is\s+it\s+going"
         r"|what'?s\s+up|sup|how\s+do\s+you\s+do)",
         "I'm running well, thanks for asking. What can I do for you?"),
        (r"(?:thanks|thank\s+you|thank\s+you\s+(?:so|very)\s+much|thx|ty|cheers|much\s+appreciated"
         r"|thanks\s+a\s+lot|appreciate\s+it)", "You're welcome!"),
        (r"(?:bye|goodbye|good\s+bye|see\s+you|see\s+ya|later|good\s*night|night)", "Goodbye! I'm here whenever you need me."),
        (r"(?:ok|okay|cool|nice|great|awesome|got\s+it|sounds\s+good|alright|all\s+right|perfect)",
         "Great. Anything else?"),
        # Not "cancel that": after "Reminder #4 set" it means cancel the reminder, and only
        # the router, which sees the conversation, can tell.
        (r"(?:never\s*mind|forget\s+(?:it|about\s+it)|no\s+worries)", "No problem."),
    )
)
# --- The user's own life: lists, facts about them, the calendar stand-in, this machine.
# Every one of these reached a chat model that could not act on it, measured by driving a
# conversational corpus through the real orchestrator.
_LIST_NAME = r"(?:my|the|our)\s+(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*)?)\s+list"
_LIST_ADD = re.compile(_POLITE + r"(?:add(?:ing)?|put(?:ting)?|throw(?:ing)?|stick(?:ing)?|writ(?:e|ing))\s+"
                       r"(?P<items>.+?)\s+(?:to|on|onto|in|into)\s+"
                       + _LIST_NAME + r"\s*[.!]*$", re.IGNORECASE)
_LIST_SHOW = re.compile(_POLITE + r"(?:what(?:'s|s|\s+is|\s+are)?\s+(?:on|in)|show(?:\s+me)?|read(?:\s+me)?"
                        r"(?:\s+out)?|check|open|what\s+do\s+i\s+have\s+on)\s+" + _LIST_NAME + r"\s*[?.!]*$",
                        re.IGNORECASE)
# "do i need anything from the store" asks for the shopping list; it was answered from nothing.
_STORE_ASK = re.compile(_POLITE + r"(?:do\s+(?:i|we)\s+need\s+anything|what\s+do\s+(?:i|we)\s+need)\s+(?:from|at)\s+"
                        r"the\s+(?:store|shop|shops|grocery(?:\s+store)?|supermarket|market)\s*[?.!]*$",
                        re.IGNORECASE)
_LIST_REMOVE = re.compile(_POLITE + r"(?:remove|delete|take|cross|scratch|strike)\s+(?P<items>.+?)\s+"
                          r"(?:off(?:\s+of)?|from)\s+" + _LIST_NAME + r"\s*[.!]*$", re.IGNORECASE)
_LIST_CLEAR = re.compile(_POLITE + r"(?:clear|empty|wipe|reset)\s+(?:out\s+)?" + _LIST_NAME + r"\s*[.!]*$",
                         re.IGNORECASE)
_LISTS = re.compile(r"^\s*(?:what|which)\s+lists\s+do\s+i\s+have\b|^\s*show\s+(?:me\s+)?(?:all\s+)?my\s+lists\s*[?.!]*$",
                    re.IGNORECASE)
# A fact about the user, said without "remember": only keys that are plainly about them, so
# "my car is broken" and "my code is failing" are left for conversation.
_PERSONAL_KEY = (
    r"name|birthday|city|hometown|home\s+town|address|email(?:\s+address)?|phone(?:\s+number)?"
    r"|favou?rite\s+[a-z]+|anniversary|(?:wife|husband|partner|mom|mother|dad|father|son|daughter|brother"
    r"|sister|boss|girlfriend|boyfriend)'?s\s+(?:name|birthday|phone(?:\s+number)?)"
)
_MY_FACT = re.compile(
    r"^\s*(?:by\s+the\s+way[,\s]+|fyi[,\s]+|just\s+so\s+you\s+know[,\s]+)?my\s+"
    r"(?P<key>" + _PERSONAL_KEY + r")\s+is\s+(?P<value>.+?)\s*[.!]*$",
    re.IGNORECASE,
)
# A value that describes a situation is not a fact: "my name is on the list", "my phone is at
# 5 percent" and "my favourite part is when the hero wins" were stored, and the chat prompt
# then called the user "on the list". A date may start with "on" or "in" ("on march 3"), so
# it is refused only words that make it relative or not a date at all.
_NOT_A_VALUE = re.compile(
    r"(?:on|in|at|when|that|not|still|so|too|very|really|always|never|almost|nearly|probably|under"
    r"|over|out|off|up|down|about|like|because|being|getting|coming|going|changing|moving)\b",
    re.IGNORECASE,
)
_NOT_A_DATE = re.compile(r"(?:coming|almost|nearly|soon|not|over|today|tomorrow|tonight|yesterday|this|next"
                         r"|in\s+(?:\d+|a|an|two|three|few)\s)\b", re.IGNORECASE)


def _plausible_fact(key: str, value: str) -> bool:
    key, value = key.lower(), value.strip()
    if "phone" in key:
        return len(re.findall(r"\d", value)) >= 7
    if "email" in key:
        return "@" in value
    if "birthday" in key or "anniversary" in key:
        return not _NOT_A_DATE.match(value)
    return not _NOT_A_VALUE.match(value)
# Correcting one: "change my name to Jeev", "update my city to Dallas".
_FACT_CHANGE = re.compile(
    r"^\s*(?:please\s+)?(?:change|update|set|correct)\s+my\s+(?P<key>" + _PERSONAL_KEY + r")\s+to\s+"
    r"(?P<value>.+?)\s*[.!]*$",
    re.IGNORECASE,
)
# Asking for one back: "what's my name", "do you remember my wife's birthday", "where do i
# live". Each reached a chat model, which could only repeat what it happened to be shown.
_FACT_ASK = re.compile(
    r"^\s*(?:(?:what(?:'s|s|\s+is)|do\s+you\s+(?:remember|know)|tell\s+me|remind\s+me(?:\s+of)?)\s+my\s+"
    r"(?P<key>[a-z][\w']*(?:\s+[a-z][\w']*){0,3}?)(?:\s+is)?(?:\s+again)?"
    r"|(?P<live>where\s+do\s+i\s+live|what\s+city\s+do\s+i\s+live\s+in)"
    r"|(?P<who>who\s+am\s+i|what\s+do\s+you\s+call\s+me))\s*[?.!]*$",
    re.IGNORECASE,
)


def fact_question(text: str) -> tuple[str, bool] | None:
    """(the fact asked for, whether it is plainly personal) for "what's my name", else None.

    Only a personal fact is answered when nothing is stored ("you haven't told me");
    "what's my ip" is not one, and goes on to whatever can answer it.
    """
    match = _FACT_ASK.match(text or "")
    if not match:
        return None
    if match.group("live"):
        return "where i live", True
    if match.group("who"):
        return "name", True
    key = " ".join(match.group("key").lower().split())
    return key, bool(re.fullmatch(_PERSONAL_KEY, key, re.IGNORECASE))
_CALL_ME = re.compile(r"^\s*(?:please\s+|from\s+now\s+on[,\s]+)?call\s+me\s+(?P<name>[a-z][\w'-]{1,20})"
                      r"(?:\s+from\s+now\s+on)?\s*[.!]*$", re.IGNORECASE)
_NOT_A_NAME = {"back", "later", "when", "if", "at", "tomorrow", "tonight", "today", "maybe", "now", "soon",
               "crazy", "out", "anytime", "whenever", "please", "sometime"}
_I_LIVE = re.compile(r"^\s*i\s+live\s+in\s+(?P<city>[a-z][\w .,'-]{1,40}?)\s*[.!]*$", re.IGNORECASE)
_NOTE = re.compile(
    _POLITE + r"(?:take\s+a\s+note|make\s+a\s+note|note\s+to\s+self|note|jot\s+(?:this\s+)?down"
    r"|write\s+this\s+down)(?:\s+(?:that|to))?\s*[:,-]?\s+(?P<text>\S.*?)\s*$",
    re.IGNORECASE,
)
# No calendar is connected. Asking about it gets the truth and the reminders; asking to add
# to it gets a reminder, said as one.
_CALENDAR_SHOW = re.compile(
    r"^\s*(?:what(?:'s|s|\s+is)|show(?:\s+me)?|check|read(?:\s+me)?|open)\s+(?:on\s+)?(?:my\s+)?"
    r"(?:calendar|agenda|schedule\s+(?:for\s+)?(?:today|tomorrow|this\s+week))\b"
    r"|^\s*what(?:'s|s|\s+is)\s+on\s+my\s+calendar\b"
    r"|^\s*what\s+do\s+i\s+have\s+(?:on\s+)?(?:today|tomorrow|this\s+week(?:end)?)\s*[?.!]*$"
    # "is there anything i need to do today" was answered from a web search for the sentence.
    r"|^\s*(?:is\s+there\s+anything|do\s+i\s+have\s+anything|what\s+do\s+i\s+(?:need|have)\s+to\s+do)\s+"
    r"(?:i\s+(?:need|have)\s+to\s+do\s+)?(?:on\s+)?(?:today|tomorrow|this\s+week(?:end)?)\s*[?.!]*$"
    r"|^\s*am\s+i\s+(?:free|busy|available)\b"
    r"|^\s*do\s+i\s+have\s+(?:any(?:thing)?\s+)?(?:meetings?|appointments?|plans|events?)\b",
    re.IGNORECASE,
)
_CALENDAR_ADD = re.compile(
    _POLITE + r"(?:(?:add|put)\s+(?P<what>.+?)\s+(?:to|on|in)\s+my\s+calendar(?P<when>.*?)"
    r"|(?:schedule|book|set\s+up|arrange)\s+(?P<event>(?:a|an|my|the)\s+(?:meeting|call|appointment|lunch"
    r"|dinner|coffee|interview|session|catch[\s-]?up|chat|visit)\b.*?))\s*[.!]*$",
    re.IGNORECASE,
)
_SYSTEM_ASK = re.compile(
    r"^\s*(?:how\s+much\s+battery|what(?:'s|s|\s+is)\s+(?:my|the)\s+battery|battery\s+(?:level|life|status"
    r"|left|percentage)|am\s+i\s+charging|(?:what(?:'s|s|\s+is)\s+(?:my|the)\s+)?(?:cpu|ram)\s+(?:usage|use|load)"
    r"|how\s+much\s+(?:free\s+)?(?:disk\s+)?(?:space|storage)\s+(?:do\s+i\s+have|is\s+left|left|is\s+free)"
    r"|(?:free\s+)?disk\s+space|system\s+status|computer\s+status"
    r"|how(?:'s|s|\s+is)\s+my\s+(?:computer|laptop|pc|machine)\s+doing)\b",
    re.IGNORECASE,
)
# Apps opened by name, and what to hand the OS for each. Only names on these lists route:
# "open the door" and "start a business" are not programs.
_DESKTOP_APPS = {
    "notepad": "notepad", "calculator": "calc", "calc": "calc", "paint": "mspaint",
    "file explorer": "explorer", "explorer": "explorer", "files": "explorer",
    "settings": "ms-settings:", "task manager": "taskmgr", "command prompt": "cmd", "cmd": "cmd",
    "terminal": "wt", "powershell": "powershell", "word": "winword", "microsoft word": "winword",
    "excel": "excel", "microsoft excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "teams": "msteams:", "microsoft teams": "msteams:", "spotify": "spotify:", "chrome": "chrome",
    "google chrome": "chrome", "edge": "microsoft-edge:", "microsoft edge": "microsoft-edge:",
    "firefox": "firefox", "vs code": "code", "vscode": "code", "visual studio code": "code",
    "slack": "slack:", "discord": "discord:", "whatsapp": "whatsapp:", "zoom": "zoommtg:",
    "obsidian": "obsidian:", "steam": "steam:", "vlc": "vlc", "snipping tool": "snippingtool",
}
_WEB_APPS = {
    "gmail": "https://mail.google.com", "google calendar": "https://calendar.google.com",
    "google drive": "https://drive.google.com", "google docs": "https://docs.google.com",
    "google maps": "https://maps.google.com", "youtube music": "https://music.youtube.com",
    "netflix": "https://www.netflix.com", "linkedin": "https://www.linkedin.com",
    "github": "https://github.com", "reddit": "https://www.reddit.com", "amazon": "https://www.amazon.com",
    "whatsapp web": "https://web.whatsapp.com", "chatgpt": "https://chatgpt.com",
}
_OPEN_APP = re.compile(_POLITE + r"(?:open|launch|start|run|bring\s+up|fire\s+up)\s+(?:up\s+)?(?:the\s+|my\s+)?"
                       r"(?P<app>[a-z][\w .+-]{0,30}?)(?:\s+app(?:lication)?)?(?:\s+for\s+me|\s+please)?\s*[.!]*$",
                       re.IGNORECASE)
_SCREENSHOT = re.compile(_POLITE + r"(?:take|grab|capture|get|snap)\s+(?:a\s+|me\s+a\s+)?screen\s*shot"
                         r"(?:\s+of\s+(?:my|the)\s+screen)?(?:\s+please)?\s*[.!]*$", re.IGNORECASE)
_JOBS_ASK = re.compile(
    r"^\s*(?:show|list|open)\s+(?:me\s+)?my\s+(?:jobs|job\s+applications|applications|job\s+tracker|pipeline)\b"
    r"|^\s*how(?:'s|s|\s+is)\s+my\s+job\s+(?:search|hunt|pipeline)\s+going\b"
    r"|^\s*what\s+jobs\s+have\s+i\s+applied\s+(?:to|for)\b",
    re.IGNORECASE,
)
_JOBRIGHT_ASK = re.compile(r"^\s*(?:pull|get|fetch|grab|check)\s+(?:new\s+|the\s+latest\s+)?(?:jobs|leads|job\s+leads)"
                           r"\s+(?:from|on)\s+jobright\b", re.IGNORECASE)
# A question about the weather, with or without a place in it.
_WEATHER_ASK = re.compile(
    r"^\s*(?:what(?:'s|s| is)|how(?:'s| is)|show\s+me|give\s+me|check|get)\s+(?:the\s+)?"
    r"(?:(?:today'?s|tomorrow'?s|current|local)\s+)?(?:weather|forecast|temperature)\b"
    r"|^\s*(?:the\s+)?(?:weather|forecast)(?:\s+(?:today|tonight|tomorrow|now|right\s+now"
    r"|this\s+week(?:end)?|please|report|update))*\s*[?.!]*\s*$"
    r"|^\s*(?:is\s+it|will\s+it|is\s+it\s+(?:going|gonna)\s+to|it'?s\s+(?:going|gonna)\s+to"
    r"|(?:going|gonna)\s+to)\s+(?:be\s+)?(?:rain|snow|hail|storm|drizzl|pour|sunny|cloudy|windy|hot"
    r"|cold|warm|chilly|freezing|humid|clear)\w*\b"
    r"|^\s*how\s+(?:hot|cold|warm|chilly|humid|windy)\s+is\s+it\b"
    r"|^\s*(?:do|should|will)\s+i\s+(?:need|bring|take|wear|pack|grab)\s+(?:an?\s+|my\s+)?"
    r"(?:umbrella|jacket|coat|raincoat|sunscreen|sweater|hoodie|layers?|shorts|boots|gloves|scarf)\b"
    r"|^\s*what\s+should\s+i\s+wear(?:\s+(?:today|tonight|tomorrow|outside|this\s+(?:morning|afternoon|evening)))?"
    r"\s*[?.!]*$"
    r"|^\s*(?:the\s+)?temperature(?:\s+(?:today|tonight|tomorrow|now|right\s+now|outside))*\s*[?.!]*$"
    r"|^\s*(?:what(?:'s|s|\s+is)\s+it\s+like|how(?:'s|s|\s+is)\s+it)\s+outside\b"
    r"|^\s*[a-z][a-z.'-]*(?:\s+[a-z][a-z.'-]*){0,2}\s+(?:weather|forecast)(?:\s+(?:today|tonight"
    r"|tomorrow|now|this\s+week(?:end)?))?\s*[?.!]*\s*$",
    re.IGNORECASE,
)
# "delhi weather tomorrow": the place comes first.
_WEATHER_NAMED = re.compile(r"^\s*([a-z][a-z.'-]*(?:\s+[a-z][a-z.'-]*){0,2})\s+(?:weather|forecast)\b",
                            re.IGNORECASE)
_WEATHER_NOT_NAMES = {
    "what's", "whats", "what", "how's", "how", "is", "will", "do", "show", "give", "check", "get",
    "the", "today's", "todays", "tomorrow's", "tomorrows", "current", "local", "weekend", "weekly",
    "daily", "hourly", "nice", "bad", "good", "great", "crazy", "this", "that", "any", "some", "my",
}
# A path, a URL or a filename is a target, not a topic.
_TARGETY = re.compile(
    r"[A-Za-z]:[\/]|(?:^|\s)[./~][\w./\-]+|https?://"
    r"|\b\w+\.(?:py|md|txt|csv|pdf|docx|json|ya?ml|png|jpe?g|mp4|wav|log|ini|toml)\b",
    re.IGNORECASE,
)
# Asking for the news is the whole sentence: a frame, describing words, an optional topic
# before the noun and an optional "about X" after it. Finding the word anywhere sent "good
# news, i got the job" to the headlines and "fake news is a problem" to a search for "is a
# problem"; and a topic counted only with nothing before it, so "latest tech news" lost
# "tech" and got the day's top stories.
_NEWS_WHEN = r"(?:\s+(?:today|tonight|now|right\s+now|please|this\s+(?:morning|afternoon|evening|week)))*"
_WORLD_NEWS = re.compile(
    _POLITE + r"(?:what(?:'s|s|\s+is)\s+(?:happening|going\s+on|new)|any\s+news)\s+in\s+the\s+world"
    r"(?:\s+of\s+(?P<topic>[a-z][\w&+.-]*(?:\s+[a-z][\w&+.-]*){0,2}))?"
    r"(?:\s+(?:today|right\s+now|this\s+morning|tonight|lately))?\s*[?.!]*",
    re.IGNORECASE,
)
_NEWS_ASK = re.compile(
    _POLITE
    + r"(?:(?:what(?:'s|s|\s+is)\s+(?:new|(?:the\s+)?latest)\s+in"
    r"|what(?:'s|s|\s+is|\s+are)?(?:\s+(?:happening|going\s+on))?(?:\s+in)?"
    r"|(?:give|show|tell|get|read|fetch|bring|find|check|pull\s+up)(?:\s+(?:me|us))?"
    r"|(?:is|are)\s+there|(?:have\s+you\s+)?got|i(?:'d|\s+would)\s+like|i\s+want)\s+)?"
    r"(?:(?:the|latest|newest|today'?s|top|breaking|recent|current|daily|morning|evening|big|biggest"
    r"|main|major|any|some|more|local|new|news|headline)\s+)*"
    r"(?:(?P<lead>[a-z][\w&+.-]*(?:\s+[a-z][\w&+.-]*){0,2})\s+)?(?:news|headlines?)" + _NEWS_WHEN
    + r"(?:\s+(?:about|on|regarding|concerning|for|from|in|re|of|around)\s+(?P<about>[^?!]+?))?"
    + _NEWS_WHEN + r"\s*[?.!]*",
    re.IGNORECASE,
)
# "news india": the topic straight after the noun, said as a command.
_NEWS_BARE = re.compile(r"\s*(?:news|headlines?)\s+(?P<bare>[a-z0-9][^?!]*?)\s*[?.!]*", re.IGNORECASE)
# What talk about the news says and a request for it does not: "good news", "i watched the
# news", "news is fake". Checked against the words around the noun, never the "about" part.
_NOT_A_TOPIC = frozenset({
    "i", "you", "we", "they", "he", "she", "it", "me", "us", "him", "them", "my", "your", "our",
    "their", "his", "her", "its", "this", "that", "these", "those", "a", "an", "is", "are", "was",
    "were", "be", "been", "am", "do", "does", "did", "has", "have", "had", "can", "could", "will",
    "would", "should", "may", "might", "must", "not", "no", "so", "such", "what", "which", "who",
    "how", "why", "when", "where", "good", "bad", "fake", "great", "sad", "terrible", "awful",
    "wonderful", "amazing", "horrible", "love", "hate", "like", "watch", "watched", "saw", "heard",
    "the", "in", "on", "of", "for", "to", "at", "from", "with", "about", "something", "anything",
})


# A diffusion model cannot draw an accurate technical diagram. Asked for one it returns
# something that looks like a diagram from across the room and is nonsense up close —
# invented boxes, unreadable labels. These belong in the reply as Mermaid or text.
_DIAGRAM_SUBJECT = re.compile(
    r"\b(?:erd|uml|entity[\s-]relationship|flow\s?chart|flow diagram"
    r"|state\s(?:machine|diagram)|sequence diagram|class diagram|architecture diagram"
    r"|network diagram|schema|wireframe|mind\s?map|org\s?chart|gantt|swimlane"
    r"|block diagram|diagram)\b",
    re.IGNORECASE,
)


def is_diagram_subject(text: str) -> bool:
    """True when the request is for a technical diagram rather than a picture."""
    return bool(_DIAGRAM_SUBJECT.search(text or ''))


def is_plain_question(text: str) -> bool:
    """True when the text asks for knowledge and names nothing to act on.

    Deliberately conservative: it must *start* like a question, must not be a decision
    (those belong to the advisor), and must mention no tool, target or personal data.
    Anything else falls through to the normal router, so tool routing cannot regress.
    """
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 400:
        return False
    if not _ASKING.match(stripped) or _DECIDING.search(stripped):
        return False
    return not _TOOL_SIGNALS.search(stripped) and not _TARGETY.search(stripped)



# People address an assistant by name, especially out loud, and every route here matches
# from the start of the message - so "Hey Jarvis, draw me a fox" matched nothing and fell
# through to the LLM, silently disabling the instant router and every guard built on it.
_ADDRESS = re.compile(
    r"^\s*(?:(?:hey|hi|hello|ok|okay|yo|so|um|uh|please)[\s,]+)*"
    r"(?:j\.?a\.?r\.?v\.?i\.?s\.?|jarvis|assistant|computer)?"
    r"\s*[,:!.\-]*\s*",
    re.IGNORECASE,
)


_TRAILING_COURTESY = re.compile(r"[\s,]+(?:please|thanks|thank\s+you)[\s?.!]*$", re.IGNORECASE)
_COURTESY_IS_CONTENT = re.compile(r"\b(?:say|said|saying|spell|word)$", re.IGNORECASE)


def strip_address(text: str) -> str:
    """Drop a leading greeting or wake name so routing sees the actual request.

    Never strips the whole message: "hey" and "jarvis" on their own are greetings that
    the chat path should answer, not requests with the subject removed.
    """
    raw = (text or "").strip()
    trimmed = _ADDRESS.sub("", raw, count=1).strip()
    # The same courtesy at the other end: routes end in `[?.!]*$`, so 24 of the 73 contract
    # phrases missed their tool when "please" followed them. Kept where it is the content:
    # "remind me to say please".
    courtesy = _TRAILING_COURTESY.search(trimmed)
    if courtesy and not _COURTESY_IS_CONTENT.search(trimmed[: courtesy.start()].strip()):
        trimmed = trimmed[: courtesy.start()].strip()
    return trimmed or raw


# Routes match anywhere in the sentence, so a leading "do not" was simply skipped: "do not open
# youtube" opened it, "do not remind me to call mom" set the reminder, and "do not run this
# command: del notes.txt" asked to run it. A negated request is the model's to read. "never
# mind the timer" is a cancellation, not a negation.
# "don't forget to call mom at 6", "don't let me miss the bus" ask for the very thing.
_NEGATED = re.compile(
    _POLITE + r"(?:do\s+not|don'?t|dont|never(?!\s*mind)|no\s+need\s+to)\b"
    r"(?!\s+(?:let\s+me\s+)?(?:forget|miss)\b)",
    re.IGNORECASE,
)


def is_negated(text: str) -> bool:
    """A request that opens by saying not to do something. The orchestrator holds every
    route to this, not only the instant one: the model can still return the positive."""
    return bool(_NEGATED.match(strip_address(text)))


_FORGET_IDIOM = re.compile(_POLITE + r"(?:do\s+not|don'?t|dont|never)\s+(?:let\s+me\s+)?(?:forget|miss)\b",
                           re.IGNORECASE)


def asks_not_to_forget(text: str) -> bool:
    """"don't forget to call mom at 5pm": a negation that asks for a reminder - and for
    nothing else (Codex's review of #194: the model routed it to `open url …mom.com`)."""
    return bool(_FORGET_IDIOM.match(strip_address(text)))


# "remind me how to center a div", "can you remind me what a closure is": asked to be told
# again, now. Read as a reminder they answered "I could not find a time in that." A time in
# the words still makes one ("remind me what to buy at 5pm"), and "when" is left out, since
# "remind me when I get home" asks for a reminder.
_TELL_AGAIN = re.compile(_POLITE + r"remind\s+me\s+(?:again\s+)?(?:of\s+)?"
                         r"(?:what|how|who|whom|whose|where|why|which|whether)\b", re.IGNORECASE)


def asks_to_be_told(text: str) -> bool:
    spoken = strip_address(text)
    if not _TELL_AGAIN.match(spoken):
        return False
    try:
        return parse_when(spoken, datetime.now().astimezone(), local=True) is None
    except TimeParseError:
        return True


def asks_to_arrange(text: str) -> bool:
    """The whole sentence asks to place or size windows. The router and the direct `split`,
    `snap` and `arrange` prefixes share it: the prefixes checked only for a position word,
    so "snap a photo of the bottom of the page" still reached the window tool."""
    probe = text or ""
    if _ARRANGE_PLACEMENTS.match(probe):
        # "should i put the legend on the right" is a clean fullmatch and is a decision for
        # the advisor; "what is on the left and what is on the right" is a question.
        return not (_ASKING.match(probe) or _DECIDING.search(probe))
    # The loose phrase fallback keeps spoken imperatives, but "what does split screen
    # mean" and "how do I use snap layout" ask for an explanation, not a window move.
    return bool(_ARRANGE_ASK.match(probe) or (
        _ARRANGE_PHRASE.search(probe) and not (_ASKING.match(probe) or _DECIDING.search(probe))
    ))


# "what are the largest files in my downloads", "show me the 5 biggest files on my desktop",
# "what's taking up space in my downloads". The LLM router turned the first into a plain
# `scan files ~/Downloads`, dropping "largest", and the reply listed the first 200 of 1214
# files in name order: a confident answer to a question nobody asked.
_LARGEST_FILES = re.compile(
    _POLITE + r"(?:(?:what|which)(?:'s|\s+is|\s+are)?\s+|(?:show|list|find|give|tell)(?:\s+me)?\s+)?"
    r"(?:the\s+|my\s+)?(?:\d+\s+|(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+)?"
    r"(?:largest|biggest|heaviest)\s+files?\s+(?:are\s+)?(?:(?:in|under|inside|on)\s+(?P<where>.+?)|(?P<here>here))"
    r"(?:,?\s+and\s+(?:tell|show|give)\s+me\s+(?:their|the)\s+sizes?)?[\s?.!]*$"
    r"|" + _POLITE + r"what(?:'s|\s+is)\s+(?:taking\s+up|using)\s+(?:the\s+most\s+|all\s+the\s+|so\s+much\s+)?"
    r"(?:disk\s+)?space\s+(?:in|on)\s+(?P<space>.+?)[\s?.!]*$",
    re.IGNORECASE,
)
_HOME_FOLDERS = {
    "downloads": "Downloads", "desktop": "Desktop", "documents": "Documents", "pictures": "Pictures",
    "photos": "Pictures", "music": "Music", "videos": "Videos",
}


def _folder_path(where: str) -> str | None:
    """'my downloads folder' -> ~/Downloads, 'here' -> '.', a path as written; else None.

    None for anything that does not name a folder on this machine, so "the largest files in
    a typical linux install" stays a question for the model rather than a scan of nothing.
    """
    place = where.strip().strip("'\"")
    if re.search(r"[\\/]|^~|^[A-Za-z]:", place):
        return place
    name = re.sub(r"^(?:my|the|this)\s+", "", place, flags=re.IGNORECASE)
    name = re.sub(r"\s+(?:folder|directory)$", "", name, flags=re.IGNORECASE).lower()
    if name in {"here", "folder", "directory", "current folder", "current directory"}:
        return "."
    known = _HOME_FOLDERS.get(name)
    return f"~/{known}" if known else None


class HeuristicPlannerProvider:
    def plan(self, text: str, available_commands: str, memory_profile: dict[str, object], history=None) -> PlanDecision:
        del available_commands, memory_profile, history
        raw = strip_address(text)
        if _NEGATED.match(raw):
            return PlanDecision(action="chat", confidence=0.25, explanation="A negated request; left to the model.")
        lowered = raw.lower()
        polite = re.match(_POLITE, raw, re.IGNORECASE).end()
        asked = raw[polite:]
        # "can you record?" asks whether it can: politeness around the bare verb is a question,
        # and starting the microphone is a bigger mistake than answering it.
        bare = polite and re.fullmatch(r"record\W*", asked.strip(), re.IGNORECASE)
        seconds = None if bare else recording_seconds(spoken_to_digits(asked))
        if seconds is not None:
            return self._command(f"record {seconds:f}", "User requested a microphone recording.", 0.99)

        if lowered in {"commands", "show commands", "command list", "syntax"}:
            return self._command("help", "User asked for the command list.", 0.95)
        # "what can you do" used to dump a hundred lines of command syntax, which is the
        # first thing anyone asks and the worst possible first impression.
        if lowered.rstrip("?!. ") in {
            "what can you do", "what do you do", "what are you capable of",
            "capabilities", "what can i ask you", "what can you help with",
            "what can you help me with", "how can you help", "what are your features",
        }:
            return self._command("capabilities", "User asked what the assistant can do.", 0.95)

        if "audit" in lowered and any(word in lowered for word in ("show", "open", "recent", "history", "log")):
            return self._command("audit", "User asked to see recent audit history.", 0.9)

        if re.search(r"\b(?:briefing|daily brief|morning brief|status brief|catch me up)\b", lowered):
            return self._command("briefing", "User wants a compact assistant status briefing.", 0.86)

        # Explicit autonomous-agent phrasing wins over keyword fallbacks below, so
        # "autonomously summarize X" runs the loop instead of a one-shot summarize.
        agent = self._agent(raw)
        if agent:
            return agent

        # Ahead of the "files here" listing and the text search, which both took these: "find
        # the three largest files in my downloads" searched file CONTENTS for "the three
        # largest files".
        largest = self._largest_files(raw)
        if largest:
            return largest

        casual = self._casual(lowered)
        if casual:
            return casual

        if _SCREEN_ASK.match(lowered):
            return self._command("read screen", "User wants the agent to look at the screen.", 0.85)

        if _WEBCAM_ASK.match(lowered):
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

        personal = self._personal(raw)
        if personal:
            return personal

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

        trip = self._trip(raw)
        if trip:
            return trip

        distance = self._distance(raw)
        if distance:
            return distance

        around = self._around(raw)
        if around:
            return around

        hotels = self._hotels(raw)
        if hotels:
            return hotels

        flights = self._flights(raw)
        if flights:
            return flights

        clock = self._clock(raw)
        if clock:
            return clock

        # "how many days until christmas", "when is thanksgiving", "when is my birthday":
        # counted, never guessed. Passed through whole; the orchestrator reads the question.
        from datetime import datetime as _datetime

        from laptop_agent.tools.dates import answerable

        if answerable(raw, _datetime.now().astimezone()):
            return self._command(raw.strip().rstrip("?.!"), "A date question, counted exactly.", 0.9)

        sum_ = self._arithmetic(raw)
        if sum_:
            return sum_

        # "convert 5 miles to km", "how many ounces in a pound": one right answer, computed.
        from laptop_agent.tools.units import looks_like_conversion

        if looks_like_conversion(raw):
            spoken = raw.strip()
            command = spoken if spoken.lower().startswith("convert ") else f"convert {spoken}"
            return self._command(command, "A unit conversion, computed exactly.", 0.95)

        headlines = self._news(raw)
        if headlines:
            return headlines

        arranged = self._arrange(raw)
        if arranged:
            return arranged

        written = self._document(raw)
        if written:
            return written

        picture = self._image(raw)
        if picture:
            return picture

        weather = self._weather(raw)
        if weather:
            return weather

        local_lookup = self._local_lookup(raw)
        if local_lookup:
            return local_lookup

        file_scan = self._file_scan(raw)
        if file_scan:
            return file_scan

        spreadsheet = self._spreadsheet(raw)
        if spreadsheet:
            return spreadsheet

        process_file = self._process_file(raw)
        if process_file:
            return process_file

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

        advise = self._advise(raw)
        if advise:
            return advise

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

        youtube_summary = self._youtube_summary(raw)
        if youtube_summary:
            return youtube_summary

        youtube = self._youtube(raw)
        if youtube:
            return youtube

        music = self._music(raw)
        if music:
            return music

        job = self._job_application(raw)
        if job:
            return job

        for pattern, reply in _SMALL_TALK:
            # The unstripped text too: "hey there" loses its "hey" to strip_address and
            # arrives as "there".
            if pattern.match(raw) or pattern.match(text):
                return PlanDecision(action="chat", confidence=0.6, explanation=SMALL_TALK, response=reply)

        # No text of its own: which reply is true - no model connected, models unreachable -
        # is for the orchestrator to say. This used to answer "I do not know the right tool
        # route yet" to "thanks" and to "what is photosynthesis", and even when a model was
        # configured and merely busy.
        return PlanDecision(action="chat", confidence=0.25, explanation="No high-confidence tool route found.")

    def _casual(self, lowered: str) -> PlanDecision | None:
        # "what … here" anywhere listed the folder for "what do you see as the main risk here".
        if ((re.search(r"\bfiles?\b", lowered)
             and re.search(r"\b(here|this folder|this directory|current (folder|directory))\b", lowered))
                or re.fullmatch(r"\s*what(?:'s|s|\s+is|\s+do\s+i\s+have)\s+(?:in\s+)?here[\s?.!]*", lowered)):
            return self._command("scan files .", "User wants the files in the current folder.", 0.84)
        if re.search(r"\b(summari[sz]e|gist|overview|tl;?dr)\b.*\breadme\b", lowered) or re.search(r"\breadme\b.*\b(summari[sz]e|gist|overview)\b", lowered):
            return self._command("summarize file README.md", "User wants the README summarized.", 0.84)
        if (re.search(r"\bwhat\b.*\b(remember|know)\b.*\b(about )?me\b", lowered) or lowered in {"my profile", "show my profile"}
                or re.fullmatch(r"what\s+(?:do\s+you|have\s+you|did\s+i\s+(?:ask|tell)\s+you\s+to)\s+(?:remember(?:ed)?|know)"
                                r"[?.!\s]*", lowered)):
            return self._command("memory", "User wants to see what is remembered about them.", 0.84)
        # The dashboard of parallel `multi` runs, asked for by name. Any sentence with "task"
        # and "how" used to open it, so "how do i prioritize tasks at work" got a dashboard.
        if re.search(r"\b(?:show|list|view)\s+(?:me\s+)?(?:my\s+|the\s+)?tasks\b|\btask\s+(?:status|dashboard)\b"
                     r"|\bhow\s+are\s+(?:my|the)\s+tasks\s+(?:going|doing)\b", lowered):
            return self._command("tasks", "User wants the task dashboard.", 0.8)
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
        # Asking to SEE the list, in any of the ways people actually ask. This was an
        # exact set of four strings, so "what are my reminders", "what reminders do i
        # have", "do i have any reminders", "any reminders", "show me my reminders",
        # "list my reminders" and "check my reminders" all missed it — and the two
        # phrased without "my" were not even sent to the LLM router, because nothing in
        # them looked like a tool, so the chat model answered from nothing.
        # It requires the plural, or "my reminder": bare singular keeps "what is a
        # reminder" a definition question rather than a listing.
        ask = _REMINDER_ASK.match(lowered)
        if ask:
            # Checked inside the listing branch, never on the bare word: "remind me to
            # pay the bill due friday" contains "due" and is an ADD.
            if re.search(r"\b(?:due|overdue|outstanding)\b", lowered):
                return self._command("reminders due", "User wants due reminders.", 0.86)
            return self._command("reminders", "User wants to list active reminders.", 0.86)
        if _REMINDER_BARE.fullmatch(lowered):
            return self._command("reminders", "User wants to list active reminders.", 0.86)
        if _SCHEDULE_ASK.fullmatch(lowered):
            return self._command("schedule list", "User wants the scheduled jobs.", 0.86)
        upcoming = _NEXT_ASK.match(lowered)
        if upcoming:
            kind = next((group for group in upcoming.groups() if group), "reminder")
            return self._command("reminders next" + ("" if kind == "reminder" else f" {kind}"),
                                 "User wants the next reminder.", 0.86)
        if lowered in {"reminders due", "due reminders", "show due reminders"}:
            return self._command("reminders due", "User wants due reminders.", 0.86)
        done = re.search(
            r"\b(?:complete|finish|mark done|mark complete)\s+reminder\s+#?(\d+)\b"
            r"|\b(?:mark\s+)?reminder\s+#?(\d+)\s+(?:as\s+)?(?:done|complete|completed|finished)\b"
            r"|\b(?:done|finished)\s+with\s+reminder\s+#?(\d+)\b",
            text, re.IGNORECASE,
        )
        if done:
            number = next(group for group in done.groups() if group)
            return self._command(f"reminder done {number}", "User wants to complete a reminder.", 0.84)
        # Timers and alarms are reminders that are only a time. Every phrasing of them used
        # to reach a chat model, which cannot set one and was free to say it had.
        spoken = spoken_to_digits(text)
        if _TIMER_LEFT.search(spoken) or re.fullmatch(
                r"\s*(?:(?:show|list|check)\s+)?(?:(?:my|the|all)\s+)?(?:running\s+)?timers\s*[?.!]*", spoken, re.I):
            return self._command("timers", "User asked about running timers.", 0.88)
        if _TIMER_BARE.match(spoken):
            return self._command("timer", "User wants a timer but gave no length.", 0.84)
        if _TIMER_ASK.match(spoken) and re.search(_DURATION, spoken, re.IGNORECASE):
            return self._command(f"timer {spoken.strip()}", "User wants a countdown timer.", 0.9)
        let_go = _LET_GO.match(spoken)
        if let_go:
            kind = (let_go.group("kind") or "").lower()
            # "the pasta timer" is labelled "Pasta timer"; "my dentist reminder" is just "dentist".
            named = (let_go.group("name") + ("" if kind == "reminder" else f" {kind}")) if let_go.group("name") else ""
            target = (let_go.group("about") or let_go.group("topic") or named
                      or ("" if kind == "reminder" else kind))
            return self._command(f"reminder delete {target}".strip(), "User no longer wants a reminder.", 0.86)
        alarm = _ALARM_ASK.match(spoken)
        if alarm and _TIME_TOKEN.search(alarm.group("when")):
            return self._command(f"alarm {alarm.group('when').strip()}", "User wants an alarm.", 0.9)
        snooze = _SNOOZE.match(spoken)
        if snooze:
            # "5m", never a bare 5: a bare number is a reminder id.
            minutes = f"{snooze.group('minutes')}m" if snooze.group("minutes") else ""
            parts = [part for part in (snooze.group("id"), minutes) if part]
            return self._command(" ".join(["reminder snooze", *parts]), "User wants a reminder later.", 0.86)
        cancel = _CANCEL.match(spoken)
        if cancel:
            target = self._reminder_target(cancel.group("rest"))
            if target is not None:
                verb = " ".join(cancel.group("verb").lower().split())
                action = "stop" if verb in _STOP_VERBS and not target.startswith("all ") else "delete"
                return self._command(f"reminder {action} {target}".strip(), "User wants a reminder cancelled.", 0.86)
        if re.fullmatch(r"\s*(?:time\s+left|how\s+much\s+time\s+(?:is\s+)?left)\s*[?.!]*", spoken, re.IGNORECASE):
            return self._command("timers", "User asked about running timers.", 0.84)
        # The whole remainder goes through, exactly as said, because `timeparse` reads the
        # time far better than a pattern here could and it is the one place that should.
        # This used to require an ISO date, so "can you remind me to call mom at 6pm" fell
        # through to the LLM router - a deterministic request answered by a guess.
        # "remind me" is required rather than a bare "reminder", so "what reminders do I
        # have" is not turned into one.
        add = re.search(
            r"\b(?:remind me|(?:set|create|add|make)\s+(?:a\s+|an\s+)?reminder)\b[,:]?\s*(.+)$",
            text, re.IGNORECASE,
        )
        # "remind me of my wife's birthday" asks, it sets nothing; so does "remind me how".
        if add and not fact_question(text) and not asks_to_be_told(text):
            rest = add.group(1).strip().strip("'\"")
            said_first = spoken_to_digits(_PREFIX_POLITE.sub("", text[: add.start()]).strip(" ,"))
            if said_first and _TIME_FIRST.fullmatch(said_first):
                rest = f"{rest} {said_first}"
            if rest:
                return self._command(
                    f"reminder add {rest}", "User wants to create a reminder.", 0.86)
        told = _TELL_ME_AT.match(spoken)
        if told:
            return self._command(f"reminder add {told.group('rest').strip()} {told.group('when').strip()}",
                                 "User wants to be told something at a time.", 0.86)
        return None

    def _personal(self, text: str) -> PlanDecision | None:
        """Lists, facts about the user, notes, the calendar stand-in, this machine, apps."""
        # Handed on as said: the assistant answers these from what it holds, and "hey
        # jarvis, what's my name" only reaches it through here.
        asked = fact_question(text)
        if asked and asked[1]:
            return self._command(text.strip().rstrip("?.! "), "The user asked for something they told me.", 0.86)
        if is_chance_request(text):
            return self._command(text.strip().rstrip("?.! "), "A random draw.", 0.9)
        if nameless_list_edit(text):
            return self._command(text.strip().rstrip(".! "), "A list edit that names no list.", 0.84)
        changed = _FACT_CHANGE.match(text)
        if changed:
            key = re.sub(r"\s+", "_", changed.group("key").strip().lower())
            return self._command(f"remember {key} = {changed.group('value').strip()}", "A fact, corrected.", 0.86)
        added = _LIST_ADD.match(text)
        if added:
            return self._command(f"list {added.group('name')} add {added.group('items')}", "Add to a list.", 0.88)
        shown = _LIST_SHOW.match(text)
        if shown:
            return self._command(f"list {shown.group('name')} show", "Read a list.", 0.88)
        if _STORE_ASK.match(text):
            return self._command("list shopping show", "What to buy is the shopping list.", 0.86)
        removed = _LIST_REMOVE.match(text)
        if removed:
            return self._command(f"list {removed.group('name')} remove {removed.group('items')}", "Remove from a list.", 0.88)
        cleared = _LIST_CLEAR.match(text)
        if cleared:
            return self._command(f"list {cleared.group('name')} clear", "Clear a list.", 0.86)
        if _LISTS.match(text):
            return self._command("lists", "Show every list.", 0.86)
        fact = _MY_FACT.match(text)
        if fact and _plausible_fact(fact.group("key"), fact.group("value")):
            key = re.sub(r"\s+", "_", fact.group("key").strip().lower())
            return self._command(f"remember {key} = {fact.group('value').strip()}", "A fact about the user.", 0.86)
        called = _CALL_ME.match(text)
        if called and called.group("name").lower() not in _NOT_A_NAME:
            return self._command(f"remember name = {called.group('name')}", "What to call the user.", 0.84)
        lives = _I_LIVE.match(text)
        if lives:
            return self._command(f"remember city = {lives.group('city')}", "Where the user lives.", 0.84)
        noted = _NOTE.match(text)
        if noted:
            return self._command(f"remember {noted.group('text')}", "Something to remember.", 0.84)
        if _CALENDAR_SHOW.match(text):
            return self._command("calendar", "The user asked about their calendar.", 0.86)
        booked = _CALENDAR_ADD.match(text)
        if booked:
            event = booked.group("event") or f"{booked.group('what')} {booked.group('when') or ''}"
            return self._command(f"calendar add {' '.join(event.split())}", "Add to the calendar.", 0.84)
        if _SYSTEM_ASK.match(text):
            return self._command("system status", "The user asked about this machine.", 0.86)
        if _SCREENSHOT.match(text):
            return self._command("screenshot", "Take a screenshot.", 0.86)
        opened = _OPEN_APP.match(text)
        if opened:
            app = re.sub(r"\s+", " ", opened.group("app").strip().lower())
            if app in _WEB_APPS:
                return self._command(f"open url {_WEB_APPS[app]}", f"Open {app}.", 0.84)
            if app in _DESKTOP_APPS:
                return self._command(f"open app {_DESKTOP_APPS[app]}", f"Open {app}.", 0.84)
        if _JOBRIGHT_ASK.match(text):
            return self._command("jobright pull", "Pull job leads from Jobright.", 0.84)
        if _JOBS_ASK.match(text):
            return self._command("jobs", "The user asked about their job search.", 0.84)
        return None

    @staticmethod
    def _reminder_target(rest: str) -> str | None:
        """What "cancel <rest>" names, as `reminder delete` takes it, or None when <rest> is
        not a reminder at all ("stop the music")."""
        # "all my reminders", or the plural alone: "clear my reminders".
        bulk = re.fullmatch(r"(?:all(?:\s+of)?(?:\s+(?:my|the))?\s+)?(?P<kind>reminders|timers|alarms)", rest,
                            re.IGNORECASE)
        if bulk:
            return f"all {bulk.group('kind').lower()}"
        named = re.fullmatch(
            r"(?:(?P<which>last|latest|most\s+recent)\s+)?(?P<kind>reminder|timer|alarm)"
            r"(?:\s+(?:#|number\s+)?(?P<id>\d+))?(?:\s+(?:to|about|for)\s+(?P<about>.+))?",
            rest, re.IGNORECASE,
        )
        if named:
            if named.group("id"):
                return named.group("id")
            if named.group("which"):
                return "last"
            if named.group("about"):
                return named.group("about")
            return "" if named.group("kind").lower() == "reminder" else named.group("kind").lower()
        described = re.fullmatch(r"(?P<about>.+?)\s+(?:reminder|timer|alarm)", rest, re.IGNORECASE)
        if described and len(described.group("about").split()) <= 4:
            return described.group("about")
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
        match = re.search(r"\b(?:search|find|look for)\s+(?:for\s+)?(.+?)\s+(?:in|under|inside)\s+(.+)$", text, re.IGNORECASE)
        if not match:
            return None
        said = match.group(1).strip()
        quoted = len(said) > 1 and said[0] in "'\"" and said[-1] == said[0]
        query = said.strip("'\"")
        root = match.group(2).strip().strip("'\"")
        # A size is never something to look for inside files: "find the largest files in the
        # linux kernel" searched a folder called "the linux kernel" for the words "the largest
        # files". `_largest_files` takes the ones that name a folder here; the rest are questions.
        # Ranking files by size only - a size word then "files", unquoted - so a word or a quoted
        # phrase to find stays one: "find largest in src/app" and 'find "largest files" in
        # src/app' are text searches (Codex's reviews).
        if not quoted and re.search(r"\b(?:largest|biggest|heaviest|smallest)\s+(?:\w+\s+)?files?\b", query, re.IGNORECASE):
            return None
        # Only treat this as a FILE search when there's an explicit file/folder cue or
        # the root is clearly a path. Otherwise a request like "find Indian restaurants
        # in Kyle, TX" must NOT become a file scan — let it fall through to web search /
        # the LLM router instead of erroring with "Path does not exist".
        has_file_cue = re.search(r"\b(files?|folders?|director(?:y|ies)|docs?|documents?)\b", text, re.IGNORECASE)
        path_like = re.search(r"[\\/]|^[A-Za-z]:|^[.~]|\.[A-Za-z0-9]{1,5}$", root)
        named_file = re.search(r"\S\.[A-Za-z0-9]{1,5}\b", query)  # query names a file, e.g. report.txt
        if not has_file_cue and not path_like and not named_file:
            return None
        query = re.sub(r"^(?:files?\s+)?(?:for\s+)?", "", query, flags=re.IGNORECASE).strip()
        # "in my documents folder" was searched for as a folder of that name under the app.
        root = _folder_path(root) or root
        return self._command(f"search files {query} {root}", "User wants to search text files.", 0.85)

    def _trip(self, text: str) -> PlanDecision | None:
        """Multi-stop trip (3+ stops): 'plan a road trip from A to B to C'. A plain
        two-point 'A to B' is left to _distance."""
        if not re.search(r"\b(?:road ?trip|trip|itinerary|multi-?stop)\b", text, re.IGNORECASE):
            return None
        m = re.search(r"\bfrom\s+(.+)$", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(?:road ?trip|trip|itinerary|multi-?stop)\b(?:\s+(?:through|via|across))?[:\s]+(.+)$",
                          text, re.IGNORECASE)
        if not m:
            return None
        segment = m.group(1)
        # Split on the verb separators only, so "City, State" stays one stop (matches
        # _distance). Fall back to commas only for a plain "A, B, C" list with no verbs.
        stops = [s.strip(" ?.!") for s in re.split(r"\s+(?:to|then|->|→)\s+", segment) if s.strip(" ?.!")]
        if len(stops) < 2 and "," in segment:
            stops = [s.strip(" ?.!") for s in segment.split(",") if s.strip(" ?.!")]
        if len(stops) < 3:
            return None
        return self._command("trip " + " | ".join(stops), "User wants a multi-stop trip plan.", 0.85)

    def _around(self, text: str) -> PlanDecision | None:
        """Places around the user's current (IP-derived) location: 'restaurants near
        me', 'gas around me'. Needs a known category, else fall through to search."""
        if not re.search(r"\b(?:near|around|by|close to)\s+me\b|\baround here\b|\bnearby\b|\bclose\s+by\b",
                         text, re.IGNORECASE):
            return None
        m = re.search(
            r"\b(hotels?|motels?|hostels?|restaurants?|food|cafes?|coffee|bars?|pubs?|gas|fuel|petrol|"
            r"pharmac(?:y|ies)|hospitals?|atms?|banks?|parking|supermarkets?|grocery|gyms?)\b",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        return self._command(f"around {m.group(1).lower()}", "User wants places around their current location.", 0.83)

    def _distance(self, text: str) -> PlanDecision | None:
        """Driving distance + time between two places (free OSRM routing)."""
        # "how long does it take to drive to chicago" matched nothing - only "how long to
        # drive" did - and named no start, so it reached the chat model. No start is here.
        drive = _DRIVE.match(text)
        if drive:
            origin = drive.group("start") or drive.group("start2") or "here"
            return self._command(f"distance {origin.strip()} to {drive.group('end').strip()}",
                                 "User wants a driving time.", 0.86)
        m = re.search(
            r"\b(?:distance|how far|driving (?:distance|time)|how long (?:to drive|is the drive))\b.*?\bfrom\s+(.+?)\s+to\s+(.+)$",
            text, re.IGNORECASE,
        )
        if m:
            return self._command(f"distance {m.group(1).strip()} to {m.group(2).strip()}", "User wants distance/route.", 0.86)
        m = re.search(r"\bhow far is\s+(.+?)\s+from\s+(.+)$", text, re.IGNORECASE)
        if m:
            return self._command(f"distance {m.group(2).strip()} to {m.group(1).strip()}", "User wants distance.", 0.86)
        m = re.search(r"\b(?:distance|how far)\b.*?\bbetween\s+(.+?)\s+and\s+(.+)$", text, re.IGNORECASE)
        if m:
            return self._command(f"distance {m.group(1).strip()} to {m.group(2).strip()}", "User wants distance.", 0.86)
        return None

    def _hotels(self, text: str) -> PlanDecision | None:
        """Real hotel listings near a place (free OpenStreetMap), not a web search."""
        m = re.search(
            r"\b(?:hotels?|motels?|hostels?|places? to stay|where (?:to|can i) stay)\b.*?\b(?:near|in|around|at)\s+(.+)$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        place = m.group(1).strip().strip("?.!,'\"")
        return self._command(f"hotels near {place}", "User wants hotels near a place.", 0.84) if place else None

    def _flights(self, text: str) -> PlanDecision | None:
        """No free no-key fare API exists, so route flights through web search."""
        m = re.search(r"\bflights?\b.*?\bfrom\s+(.+?)\s+to\s+(.+)$", text, re.IGNORECASE)
        if m:
            return self._command(
                f"web search flights from {m.group(1).strip()} to {m.group(2).strip()}", "User wants flights.", 0.8
            )
        # A bare "fly ... to" matched "how do birds fly to the south" and "i'm afraid to fly
        # to be honest"; flying is a request only when someone plans to do it.
        m = re.search(r"\b(?:flights?|airfare|plane\s+tickets?)\b.*?\bto\s+(.+)$", text, re.IGNORECASE)
        if not m:
            m = re.search(r"\b(?:want|need|planning|plan|going|have)\s+to\s+fly\s+to\s+(.+)$", text, re.IGNORECASE)
        if m:
            dest = m.group(1).strip().strip("?.!,'\"")
            return self._command(f"web search flights to {dest}", "User wants flights.", 0.78) if dest else None
        return None

    def _news(self, text: str) -> PlanDecision | None:
        """'what is the latest news' -> real headlines. A generic web search for this
        returns the homepages of CNN and Fox with their taglines, not the news."""
        # "read the news article file.txt" names a target, so it belongs to the file path.
        if _TARGETY.search(text):
            return None
        world = _WORLD_NEWS.fullmatch(text)
        if world:
            # "what's happening in the world today" names no "news", and got a web search.
            topic = re.sub(r"\s+(?:today|tonight|lately|right\s+now|this\s+morning)$", "",
                           (world.group("topic") or "").strip(), flags=re.IGNORECASE)
            if any(word in _NOT_A_TOPIC for word in topic.lower().split()):
                return None     # "the world of my dreams"
            return self._command(f"news {topic}".strip(), "User wants the latest headlines.", 0.85)
        asked = _NEWS_ASK.fullmatch(text) or _NEWS_BARE.fullmatch(text)
        if not asked:
            return None
        named = asked.groupdict()
        lead, bare = named.get("lead") or "", named.get("bare") or ""
        if any(word in _NOT_A_TOPIC for word in f"{lead} {bare}".lower().split()):
            return None
        # "in" and "from" belong to the phrasing, not the topic: "news in india" was
        # answered "Top stories about in india".
        about = re.sub(r"^(?:the|a|an)\s+", "", (named.get("about") or bare).strip(" ,'\""), flags=re.IGNORECASE)
        if about.lower() in {"today", "now", "please", "day", "the day", "week", "update", "updates"}:
            about = ""
        topic = " ".join(part for part in (lead, about) if part)
        if not topic:
            return self._command("news", "User wants the latest headlines.", 0.85)
        return self._command(f"news {topic}", "User wants headlines on a topic.", 0.85)

    def _clock(self, text: str) -> PlanDecision | None:
        """'what time is it in EST' -> the machine's clock, with no model and no search.

        Routed to the web this answered 1:00 PM against a real local time of 6:26 PM,
        then invented a citation to defend it."""
        from laptop_agent.tools.clock import asks_the_time

        if not asks_the_time(text):
            return None
        return self._command(f"time {text.strip()}", "Asking what time or date it is.", 0.95)

    def _arithmetic(self, text: str) -> PlanDecision | None:
        """'what is 67458363*37834872' -> the calculator, with no model in the loop.

        A language model is the wrong tool for 8-digit multiplication: the reported case
        produced a decision framework and never reached 2,552,278,529,434,536."""
        from laptop_agent.tools.calculator import looks_like_arithmetic

        if not looks_like_arithmetic(text):
            return None
        return self._command(f"calculate {text.strip()}", "That is a sum; compute it exactly.", 0.95)

    def _arrange(self, text: str) -> PlanDecision | None:
        """'put whatsapp on the left and chrome on the right' -> the window tool.

        The whole sentence is passed through as `window <text>`; the tool parses the
        placements, because the position can come before the name when it is spoken.
        """
        if not asks_to_arrange(text):
            return None
        return self._command(f"window {text.strip()}", "User wants windows arranged.", 0.88)

    def _document(self, text: str) -> PlanDecision | None:
        """'write a report on X as a pdf' -> the document tool. The named format is what
        separates this from an ordinary request to write something in the chat."""
        if not re.search(
            r"\b(?:as|in|to|into)\s+(?:an?\s+)?(?:pdf|word|docx|doc|markdown|md|pptx|ppt"
            r"|power\s*point|slide\s*deck|slides?|deck|presentation)"
            r"(?:\s+(?:file|doc|document|format))?\s*$",
            text, re.IGNORECASE,
        ):
            # A deck names its format at the front instead — "a ppt for the solar system".
            # Pass the whole sentence through so the tool can still read the format off it.
            if _DECK_ASK.match(text or ""):
                return self._command(f"document {text.strip()}", "User wants a slide deck file.", 0.85)
            # So does "make a pdf about healthy eating", the commonest way to ask for one.
            head = _DOC_HEAD.match(text or "")
            if head:
                kind = head.group("kind").lower()
                fmt = "word" if kind.startswith(("word", "doc")) else "markdown" if kind.startswith(("markdown", "md")) else "pdf"
                # The length rides along: "a one page pdf on X" lost it and printed on two.
                length = f"{head.group('length').strip()}: " if head.group("length") else ""
                return self._command(f"document {length}{head.group('topic').strip()} as {fmt}", "User wants a document file.", 0.85)
            return None
        match = re.match(
            r"^\s*(?:can you |could you |please )?"
            r"(?:write(?:\s+up)?|create|make|generate|draft|prepare|produce|export)\s+"
            r"(?:me\s+)?(?:an?\s+|the\s+)?(.+)$",
            text, re.IGNORECASE,
        )
        if not match:
            return None
        request = match.group(1).strip()
        if not request:
            return None
        return self._command(f"document {request}", "User wants a written document file.", 0.85)

    def _image(self, text: str) -> PlanDecision | None:
        """'draw me a picture of a fox' -> the image tool, with no LLM round-trip."""
        match = re.match(
            r"^\s*(?:can you |could you |please )?"
            r"(?:draw|paint|sketch|generate|create|make|render)\s+"
            r"(?:me\s+)?(?:an?\s+|some\s+)?"
            r"(?:image|picture|photo|illustration|drawing|painting|artwork)\s+"
            r"(?:of|showing|with)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        # "draw a cat" and "sketch a dragon" name the picture directly. "draw" is also a
        # verb of conclusions, lines and attention, which are not pictures - and "paint the
        # wall blue" is a chore, so paint only counts in the longer form above.
        if not match:
            match = re.match(
                # The stop-list is checked past any article: with the article optional, the
                # engine otherwise backtracks and tests "a" instead of "a conclusion".
                r"^\s*(?:can you |could you |please )?(?:draw|sketch)\s+(?:me\s+)?"
                r"(?!(?:(?:an?|some|the)\s+)?(?:up|conclusions?|lines?|attention|blood|near|back|closer|breath"
                r"|money|cash|parallels?|comparisons?|distinctions?|inspiration|lots|straws|curtains?|bath|water"
                r"|blank|it|this|that)\b)"
                r"(?:an?\s+|some\s+|the\s+)?([a-z].*)$",
                text,
                re.IGNORECASE,
            )
        if not match:
            return None
        subject = match.group(1).strip().strip("?.!,'\"")
        if not subject or subject.lower() in {"picture", "image", "photo", "drawing", "pic", "something", "anything"}:
            return None
        # A diagram is drawn in the reply as Mermaid; a diffusion model makes nonsense of it.
        if is_diagram_subject(subject):
            return None
        return self._command(f"image {subject}", "User wants a generated picture.", 0.85)

    def _weather(self, text: str) -> PlanDecision | None:
        """Real forecast (Open-Meteo) instead of opening a web search for weather.

        A question with no place is answered for where the user is: `weather` with no
        argument uses a remembered city, else the IP location. Those questions - "will it
        rain tomorrow", "do i need an umbrella", "what's the weather" - used to reach a
        chat model that cannot see the sky, or a web search for the sentence itself.
        """
        # A sentence naming a table is a data forecast, never the weather: one that missed the
        # forecast command's grammar was answered with the weather at a place called "sales.csv".
        if re.search(r"\.(?:csv|tsv)\b", text, re.IGNORECASE):
            return None
        asked = _WEATHER_ASK.match(text)
        if not asked and not re.search(r"\b(weather|forecast|temperature)\b", text, re.IGNORECASE):
            return None
        match = re.search(r"\b(?:in|for|at|near|around)\s+(.+)$", text, re.IGNORECASE)
        location = clean_place(match.group(1)) if match else ""
        if not location and asked:
            named = _WEATHER_NAMED.match(text)
            if named and named.group(1).split()[0].lower() not in _WEATHER_NOT_NAMES:
                location = clean_place(named.group(1))
        if location:
            return self._command(f"weather {location}", "User wants a weather forecast.", 0.85)
        if asked:
            return self._command("weather", "User wants the forecast where they are.", 0.85)
        return None

    def _local_lookup(self, text: str) -> PlanDecision | None:
        """Recommendation / local-place queries go to web search — live data beats
        stale model knowledge. e.g. 'find me Indian restaurants in Kyle, TX'."""
        place = re.search(
            r"\b(restaurants?|cafes?|coffee|bars?|pubs?|hotels?|motels?|shops?|stores?|gym|gyms|salons?|"
            r"clinics?|hospitals?|pharmac(?:y|ies)|dentists?|doctors?|gas stations?|parking|things to do|"
            r"places? to (?:eat|visit|stay|go)|near me|nearby|open now)\b",
            text,
            re.IGNORECASE,
        )
        intent = re.search(r"\b(find|show|get|recommend|suggest|where|best|good|top|cheap|nearest|list)\b", text, re.IGNORECASE)
        if not place or not intent:
            return None
        if re.search(r"\b(files?|folders?|director(?:y|ies))\b", text, re.IGNORECASE):
            return None  # a file request that happens to mention a place word
        query = re.sub(r"^\s*(?:hey\s+jarvis[,\s]*)?(?:can you|could you|please|would you)?\s*", "", text, flags=re.IGNORECASE)
        query = re.sub(r"^\s*(?:find|get|show|give)\s+me\s+", "", query, flags=re.IGNORECASE).strip().strip("?")
        if not query:
            return None
        return self._command(f"web search {query}", "User wants local recommendations — search the web.", 0.8)

    def _largest_files(self, text: str) -> PlanDecision | None:
        match = _LARGEST_FILES.match(text.strip())
        root = _folder_path(match.group("where") or match.group("space") or match.group("here")) if match else None
        if root is None:
            return None
        return self._command(f"scan files {root} by size", "User wants the largest files in a folder.", 0.86)

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

    def _process_file(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:process|handle|deal with|what(?:'?s| is) (?:in|inside))\s+"
            r"(?:the\s+)?(?:file\s+)?(.+\.[a-z0-9]{1,5})$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        path = match.group(1).strip().strip("'\"?")
        return self._command(f"process file {path}", "User wants the agent to auto-detect and process a file.", 0.8)

    def _spreadsheet(self, text: str) -> PlanDecision | None:
        match = re.search(
            r"\b(?:analy[sz]e|stats? (?:for|of|on)|column stats? (?:for|of)|summari[sz]e the data in)\s+"
            r"(?:the\s+)?(?:spreadsheet\s+|csv\s+|data\s+|file\s+)?(.+\.(?:csv|tsv))$",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        path = match.group(1).strip().strip("'\"")
        return self._command(f"analyze spreadsheet {path}", "User wants per-column statistics for a spreadsheet.", 0.82)

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

    def _advise(self, text: str) -> PlanDecision | None:
        """Decision/problem-solving requests go to the structured advisor (research +
        options + recommendation + plan), not a plain chat reply or web search."""
        match = re.search(
            r"\b(?:help me (?:decide|choose|figure out|solve)|weigh (?:my|the|up) options|"
            r"what(?:'?s| is) the best (?:way|approach|option|strategy) (?:to|for)|"
            r"how should i (?:approach|tackle|handle|solve)|figure out (?:how|whether)|"
            r"should i\b.+?\bor\b)\b",
            text,
            re.IGNORECASE,
        )
        if not match:
            return None
        problem = re.sub(r"^\s*(?:hey\s+jarvis[,\s]*)?(?:can you|could you|please|would you)?\s*", "", text, flags=re.IGNORECASE)
        problem = problem.strip().strip("?.!")
        if len(problem.split()) < 3:
            return None
        return self._command(f"solve {problem}", "User wants a reasoned recommendation, not just chat.", 0.78)

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
            r"\b(?:search the web for|search online for|look up|web search(?: for)?|search the internet for)\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        # "google" is a verb only at the front. Anywhere in the sentence it is as often the
        # company: "tailor my resume for the google job" searched the web for "job".
        if not match:
            match = re.match(_POLITE + r"google\s+(.+)$", text, re.IGNORECASE)
        # "search for best laptops", the commonest phrasing of all, unless it names the
        # user's own data, which belongs to the files, mail or notes tools.
        if not match:
            match = re.match(_POLITE + r"search\s+(?:for\s+)?(.+)$", text, re.IGNORECASE)
            if match and re.search(
                r"\b(?:my|our|files?|folders?|emails?|inbox|mail|notes?|vault|knowledge|documents?"
                r"|youtube|yt)\b",
                match.group(1), re.IGNORECASE,
            ):
                match = None
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

    def _youtube_summary(self, text: str) -> PlanDecision | None:
        """Summarize/answer about a YouTube video from its transcript."""
        url = re.search(r"(https?://\S*(?:youtube\.com|youtu\.be)\S*|youtu\.be/[A-Za-z0-9_-]{11})", text, re.IGNORECASE)
        if not url:
            return None
        link = url.group(1).strip().strip("<>'\"")
        wants_summary = re.search(
            r"summar|tl;?dr|recap|key ?points|takeaway|what(?:'s| is| does| are)|\b(?:explain|about|gist|notes)\b",
            text,
            re.IGNORECASE,
        )
        if not wants_summary:
            return None
        return self._command(f"summarize youtube {link}", "User wants a YouTube video summarized.", 0.85)

    def _youtube(self, text: str) -> PlanDecision | None:
        """Make YouTube requests actually open the browser (via the music tool's
        YouTube search) instead of letting the LLM claim it opened a tab."""
        t = text.strip()
        m = re.search(r"\bplay\s+(.+?)\s+on\s+youtube\b", t, re.IGNORECASE)
        if m:
            return self._command(f"play music {m.group(1).strip()}", "Play it on YouTube.", 0.84)
        m = re.search(
            r"\b(?:open\s+youtube\s+(?:and\s+)?(?:type|search(?:\s+for)?|play|look\s*up)\s+"
            r"|(?:search|look\s*up)\s+(?:on\s+)?youtube\s+(?:for\s+)?"
            r"|youtube\s+(?:search\s+(?:for\s+)?|for\s+))(.+)$",
            t,
            re.IGNORECASE,
        )
        if m:
            query = m.group(1).strip().strip("?'\"")
            if query:
                return self._command(f"play music {query}", "Search it on YouTube.", 0.84)
        if re.search(r"\bopen\s+youtube\s*$", t, re.IGNORECASE) or re.fullmatch(r"\s*youtube\s*", t, re.IGNORECASE):
            return self._command("open url https://www.youtube.com", "Open YouTube.", 0.8)
        return None

    def _music(self, text: str) -> PlanDecision | None:
        for pattern, key in _MEDIA_KEYS:
            if pattern.match(text):
                return self._command(f"media {key}", "User wants media playback controlled.", 0.8)
        level = _VOLUME_LEVEL.match(text)
        if level:
            said_level = level.group("level")
            digits = said_level if said_level.isdigit() else _words_to_number(said_level)
            return self._command(f"media volume {digits}", "User wants a volume level.", 0.84)
        match = _PLAY.match(text)
        if not match:
            return None
        target = match.group("target").strip().strip("'\"")
        if not target or _NOT_MUSIC.search(target) or is_personal_message_target(target):
            return None
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
            # How it is said: "email bob@x.com about lunch", "send an email to amy@x.com
            # saying I'll be late". The direct `email` prefix answered these with its syntax.
            said = re.match(
                _POLITE + r"(?:(?:send|write|draft)\s+(?:an?\s+)?e-?mail\s+to|e-?mail|send)\s+(?P<to>\S+@[\w.-]+\w)[,:]?\s+"
                r"(?:an?\s+e-?mail\s+)?(?P<how>about|regarding|re:?|saying|that|to\s+say|telling\s+(?:them|him|her))\s+"
                r"(?P<what>\S.*?)\s*[.!]*$",
                text, re.IGNORECASE,
            )
            if not said:
                return None
            what = said.group("what").strip()
            if said.group("how").lower() in {"about", "regarding", "re", "re:"}:
                subject, body = what, f"Draft email about: {what}"
            else:
                words = what.split()
                subject = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
                subject, body = subject[:1].upper() + subject[1:], what
            return self._command(f"email to {said.group('to')} subject {subject} body {body}",
                                 "User wants an email draft.", 0.65)
        to = match.group(1).strip()
        subject = match.group(2).strip()
        body = f"Draft email about: {subject}"
        return self._command(f"email to {to} subject {subject} body {body}", "User wants an email draft.", 0.65)

    def _email_search(self, text: str) -> PlanDecision | None:
        lowered = text.lower()
        api_provider = self._email_api_provider(lowered)
        if _MAIL_DIGEST_ASK.match(text):
            return self._command("email digest", "User wants a summary of their inbox.", 0.82)
        ask = _MAIL_ASK.match(text)
        if ask and re.search(r"\b(?:new|unread)\b", ask.group(0), re.IGNORECASE):
            if api_provider:
                return self._command(f"email api unread {api_provider}", "User wants unread OAuth-backed mailbox messages.", 0.78)
            return self._command("email unread", "User wants unread inbox messages.", 0.78)
        # The rest -> the IMAP digest (categorised inbox). Keep this on the local IMAP path,
        # not OAuth, unless the user explicitly names a provider — OAuth reads are gated.
        if ask:
            if api_provider:
                return self._command(f"email api unread {api_provider}", "User wants OAuth-backed mailbox messages.", 0.76)
            return self._command("email digest", "User wants a look at their important inbox mail.", 0.8)
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
