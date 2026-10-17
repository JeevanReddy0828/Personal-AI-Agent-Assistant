"""The daily briefing in the user's own format: which sections, in which order, and what each is
about - "weather in Austin, AI news, my reminders and the shopping list".

The format is kept as one remembered fact (`briefing`), so it shows in "what do you remember
about me" and "forget my briefing" puts the default back. It is parsed again on every read
rather than stored as structure: what the user said is the record, and a section added here
later reads an old format the same way. This module only reads formats and lays sections
out; the orchestrator gathers what goes in them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_FORMAT = "reminders, weather, news, laptop"
PROFILE_KEY = "briefing format"
MAX_SECTIONS = 8
MAIL_FETCH = 5      # each is a whole message over IMAP; three are shown and the count is "5+"
CAN_INCLUDE = ("reminders, weather [in <city>], news [about <topic>], mail, a list (e.g. shopping list) "
               "and laptop")


@dataclass(frozen=True)
class Section:
    kind: str       # reminders, weather, news, mail, list, laptop
    about: str = ""  # the weather's place, the news topic, the list's name

    def describe(self) -> str:
        if self.kind == "weather":
            return f"weather in {self.about}" if self.about else "weather"
        if self.kind == "news":
            return f"news about {self.about}" if self.about else "news"
        if self.kind == "list":
            return f"{self.about} list"
        return self.kind


_SPLIT = re.compile(r"\s*(?:,|;|&|\+|\band\b|\bthen\b|\bplus\b|\bfollowed\s+by\b|\balso\b)\s*", re.IGNORECASE)
# "to" goes only when it is not "to do": a speech engine writes the to-do list as "to do list".
_FILLER = re.compile(r"^(?:(?:with|to(?!\s+do\b)|have|has|include|includes|including|be|cover|covers|show|shows|"
                     r"just|only|me|my|the|a|an|some|today'?s|latest|current|top|any|all|of)\s+)+", re.IGNORECASE)
# Every section is the WHOLE item, never a word in it: matched anywhere, "machine learning papers"
# became the laptop line and "unread slack messages" the inbox, and both were saved as accepted.
_WEATHER = re.compile(r"(?:weather|forecast|temperature)(?:\s+(?:in|for|at|near)\s+(?P<place>[\w .'-]{2,40}))?",
                      re.IGNORECASE)
_NEWS = re.compile(r"(?:(?P<before>[\w.&'-]+(?:\s+[\w.&'-]+)?)\s+)?(?:news|headlines|stories)"
                   r"(?:\s+(?:about|on|from|in|for)\s+(?P<after>[\w .&'-]{1,40}))?", re.IGNORECASE)
_NO_TOPIC = {"", "world", "top", "general", "breaking", "the", "daily", "morning", "local", "big", "main"}
_LIST = re.compile(r"(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*)?)\s+list", re.IGNORECASE)
# A list's name is one or two words with no preposition in them ("milk to my shopping list" is no
# list); "to-do" is one word.
_NOT_A_LIST = re.compile(r"(?:reminders?|timers?|alarms?|(?:\S+\s+)?(?:to|in|for|from|on|of|my|the)(?:\s+\S+)?)",
                         re.IGNORECASE)
# No "calendar" or "schedule": no calendar is connected and "schedule" is the scheduled jobs, so
# either would be answered with the reminders as if they were the thing asked for.
_REMINDERS = re.compile(r"(?:reminders?|timers?|alarms?|agenda|to-?dos?)(?:\s+list)?", re.IGNORECASE)
_MAIL = re.compile(r"(?:unread\s+)?(?:mail|e-?mails?|inbox)", re.IGNORECASE)
_LAPTOP = re.compile(r"(?:(?:laptop|system|computer|pc|machine)(?:\s+status)?|battery|cpu)", re.IGNORECASE)


def _section(item: str) -> Section | None:
    # Speech writes the to-do list as "to do list"; memory knows it as one list under either name.
    text = re.sub(r"\bto\s+do\b", "to-do", _FILLER.sub("", item.strip(" .!?")).strip(), flags=re.IGNORECASE)
    if not text:
        return None
    if (found := _LIST.fullmatch(text)) is not None and not _NOT_A_LIST.fullmatch(found["name"]):
        return Section("list", found["name"].lower())
    if (found := _WEATHER.fullmatch(text)) is not None:
        return Section("weather", (found["place"] or "").strip())
    if (found := _NEWS.fullmatch(text)) is not None:
        topic = (found["after"] or found["before"] or "").strip()
        return Section("news", "" if topic.lower() in _NO_TOPIC else topic)
    if _MAIL.fullmatch(text):
        return Section("mail")
    if _REMINDERS.fullmatch(text):
        return Section("reminders")
    if _LAPTOP.fullmatch(text):
        return Section("laptop")
    return None


def parse_format(text: str) -> tuple[list[Section], list[str]]:
    """The sections `text` names, in its order and each once, and the parts it could not read.
    A place may carry its own comma ("weather in Paris, France"): a short capitalised part right
    after a weather place is the rest of that place. Lower case it is not ("weather in Boston,
    stocks" must still refuse "stocks")."""
    sections: list[Section] = []
    unknown: list[str] = []
    for item in _SPLIT.split(text or ""):
        part = item.strip(" .!?")
        if not part:
            continue
        section = _section(part)
        last = sections[-1] if sections else None
        if (section is None and last is not None and last.kind == "weather" and last.about
                and re.fullmatch(r"[A-Z][A-Za-z .'-]{1,30}", part) and len(part.split()) <= 3):
            sections[-1] = Section("weather", f"{last.about}, {part}")
        elif section is None:
            unknown.append(part)
        elif section not in sections:
            sections.append(section)
    return sections, unknown


def describe_format(sections: list[Section]) -> str:
    return ", ".join(section.describe() for section in sections)


def greeting(hour: int) -> str:
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def _number(value: object) -> str:
    return f"{value:.0f}" if isinstance(value, (int, float)) else "?"


def weather_text(data: dict) -> str:
    """One line: where, now, and today's range."""
    current = data.get("current") or {}
    today = (data.get("daily") or [{}])[0]
    line = (f"{data.get('location', 'Here')}: {current.get('description', 'unknown conditions')}, "
            f"{_number(current.get('temperature_2m'))}°F")
    if today.get("high") is not None and today.get("low") is not None:
        line += f". Today {_number(today['high'])}° / {_number(today['low'])}°F"
        if today.get("precip_chance") is not None:
            line += f", {_number(today['precip_chance'])}% chance of rain"
    return line + "."


def news_lines(headlines: list[dict]) -> list[str]:
    lines = []
    for index, item in enumerate(headlines, start=1):
        source = f" — {item['source']}" if item.get("source") else ""
        title = str(item.get("title", "")).strip()
        if item.get("source"):  # Google's titles end in their source: "Deal - The New York Times"
            title = re.sub(rf"\s+[-–—|]\s+{re.escape(str(item['source']))}$", "", title)
        lines.append(f"{index}. [{title}]({item['url']}){source}" if item.get("url") else f"{index}. {title}{source}")
    return lines


def mail_lines(messages: list[dict], shown: int = 3) -> list[str]:
    if not messages:
        return ["No unread mail."]
    count = len(messages)
    head = f"{count}{'+' if count >= MAIL_FETCH else ''} unread. Newest:"
    lines = [head]
    for message in messages[:shown]:
        sender = re.sub(r"\s*<[^>]+>", "", str(message.get("from", ""))).strip(' "') or "Unknown sender"
        subject = str(message.get("subject", "")).strip() or "(no subject)"
        lines.append(f"- {sender}: {subject}")
    return lines
