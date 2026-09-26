"""Turn "6pm", "tomorrow at 9", "in 20 minutes" into an exact instant.

A language model is the wrong tool for this, for the same reason `calculator.py` exists:
asked for a date it will produce a plausible one, and a reminder that fires on the wrong
day is worse than one that refuses. Everything here is deterministic, offline and pure -
given the same text and the same `now`, the answer never varies.

Two callers share it so a fix reaches both: `reminders` (a one-off instant) and
`scheduler` (a recurring time of day). Four near-identical tokenizers is the mistake
`terms.py` was created to undo; this is the same shape, caught earlier.

The resolved instant is always echoed back to the user in local time, because the only
real defence against a misparse is that it is visible immediately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

# Shared with scheduler.parse_schedule. Weeks are here; months are not, because "in 2
# months" has no single correct answer and a reminder may not guess.
DURATION_UNITS: dict[str, int] = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60, "m": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
}

WEEKDAYS: dict[str, int] = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

MONTHS: dict[str, int] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
    "november": 11, "nov": 11, "december": 12, "dec": 12,
}

# Named times. A reminder with a day but no time lands at DEFAULT_HOUR, which matches the
# long-standing behaviour of a bare ISO date.
DEFAULT_HOUR = 9
NAMED_TIMES: dict[str, tuple[int, int]] = {
    "noon": (12, 0), "midday": (12, 0), "midnight": (0, 0),
    "morning": (9, 0), "afternoon": (14, 0), "evening": (18, 0),
    "tonight": (20, 0), "night": (20, 0),
}

_MERIDIEM = r"(?:am|pm|a\.m\.|p\.m\.)"
# A bare hour only counts as a time when "at" introduces it: "buy 2 apples" must not
# become 2 o'clock. With am/pm attached it needs no introduction.
_TIME_PATTERNS = (
    rf"\b(?P<h1>\d{{1,2}})(?::(?P<m1>\d{{2}}))?\s*(?P<mer1>{_MERIDIEM})",
    r"\b(?P<h2>[01]?\d|2[0-3]):(?P<m2>[0-5]\d)\b",
    r"\bat\s+(?P<h3>\d{1,2})\b(?!\s*[:\d])",
)
_NAMED_TIME_PATTERN = (
    r"\b(?P<named>noon|midday|midnight|morning|afternoon|evening|night)\b")
# Words that introduce a time and belong to it, not to the message: "call mom at 6pm"
# should leave "call mom", not "call mom at".
_LEAD_WORDS = r"(?:\b(?:at|on|by|around|about|the|this|next|of)\s+)+$"


@dataclass(frozen=True)
class When:
    """A resolved instant and the span of text it was read from."""

    at: datetime
    start: int
    end: int


class TimeParseError(ValueError):
    """The text names a time that cannot exist, e.g. 25:00 or 30 February."""


def parse_clock(text: str) -> tuple[int, int] | None:
    """Read a time of day: '6pm', '6:30 pm', '18:00', 'noon'. None when there is none.

    Raises TimeParseError for a time that cannot exist, rather than silently sliding to
    another hour - a reminder set for the wrong time is the failure this module exists to
    prevent.
    """
    lowered = text.strip().lower()
    if not lowered:
        return None
    named = re.fullmatch(r"(?:at\s+)?(noon|midday|midnight)", lowered)
    if named:
        return NAMED_TIMES[named.group(1)]
    match = re.fullmatch(
        rf"(?:at\s+)?(\d{{1,2}})(?::(\d{{2}}))?\s*({_MERIDIEM})?", lowered)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").replace(".", "")
    return _normalise_clock(hour, minute, meridiem, had_minutes=match.group(2) is not None)


def _normalise_clock(hour: int, minute: int, meridiem: str, had_minutes: bool) -> tuple[int, int]:
    if minute > 59:
        raise TimeParseError(f"There is no minute {minute}.")
    if meridiem:
        if not 1 <= hour <= 12:
            raise TimeParseError(f"{hour}{meridiem} is not a time.")
        return hour % 12 + (12 if meridiem.startswith("p") else 0), minute
    if hour > 23:
        raise TimeParseError(f"There is no hour {hour}.")
    # A bare hour with neither minutes nor am/pm: "friday at 5" means five in the
    # afternoon, not five in the morning. 1-6 read as PM, 7-12 as written. It is a
    # convention, not a certainty, which is why the caller always echoes the resolved
    # time back - a wrong guess has to be visible in the same breath.
    if not had_minutes and 1 <= hour <= 6:
        hour += 12
    return hour, minute


def _apply(day: date, clock: tuple[int, int], now: datetime) -> datetime:
    return datetime(day.year, day.month, day.day, clock[0], clock[1], tzinfo=now.tzinfo)


def parse_when(text: str, now: datetime | None = None, default_half: str = "") -> When | None:
    """Resolve the first date/time expression in `text`, or None if there is none.

    `now` must be timezone-aware; the result carries the same zone. The span lets the
    caller strip the time words out of a message without guessing where they were.
    `default_half` ('am'/'pm') settles a bare hour with nothing around it to say which:
    an alarm passes 'am'.
    """
    if now is None:
        now = datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    lowered = text.lower()

    resolvers = (_iso, _duration, lambda text, now: _day_and_time(text, now, default_half), _right_now)
    for resolve in resolvers:
        found = resolve(lowered, now)
        if found is not None:
            lead = re.search(_LEAD_WORDS, lowered[: found.start])
            start = lead.start() if lead else found.start
            return When(found.at, start, found.end)
    # Nothing parsed, but something was clearly meant as a clock. Refusing beats treating
    # "at 25:00" as part of the message and setting no time at all.
    broken = re.search(r"\b(\d{1,2}):(\d{2})\b", lowered)
    if broken:
        raise TimeParseError(f"There is no time {broken.group(0)}.")
    return None


def _right_now(text: str, now: datetime) -> When | None:
    """"now" - tried last, so "at 5pm, now that the store is open" still means five."""
    match = re.search(r"\b(?:right\s+)?now\b", text)
    return When(now, match.start(), match.end()) if match else None


# Numbers said out loud. Voice arrives as "at six" and "in five minutes", and a reminder
# that could not read them answered "I could not find a time in that" to a clear request.
_ONES = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_SPOKEN = (
    r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"(?:[\s-]+(?:one|two|three|four|five|six|seven|eight|nine))?"
    r"|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
    r"|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen)"
)
_HOUR = rf"(?:{_SPOKEN}|\d{{1,2}})"
_SPOKEN_UNITS = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)"


def _spoken_number(words: str) -> int:
    if words.isdigit():
        return int(words)
    return sum(_TENS.get(word, 0) or _ONES.get(word, 0) for word in re.split(r"[\s-]+", words.lower()))


def spoken_to_digits(text: str) -> str:
    """"at six thirty" -> "at 6:30", "in five minutes" -> "in 5 minutes", "quarter to
    eight" -> "7:45". Only numbers that belong to a time are touched, so "buy two
    apples at six" keeps its two."""
    flags = re.IGNORECASE
    out = re.sub(rf"\bhalf\s+(?:past|after)\s+({_HOUR})\b",
                 lambda m: f"{_spoken_number(m.group(1))}:30", text, flags=flags)
    out = re.sub(rf"\bquarter\s+(?:past|after)\s+({_HOUR})\b",
                 lambda m: f"{_spoken_number(m.group(1))}:15", out, flags=flags)
    out = re.sub(rf"\bquarter\s+(?:to|til|till|before)\s+({_HOUR})\b",
                 lambda m: f"{(_spoken_number(m.group(1)) - 2) % 12 + 1}:45", out, flags=flags)

    def clock(match: re.Match[str]) -> str:
        hour = _spoken_number(match.group(2))
        minute_words = match.group(3)
        minute = _spoken_number(re.sub(r"^oh\s+", "", minute_words, flags=flags)) if minute_words else None
        if hour > 23 or (minute is not None and minute > 59):
            return match.group(0)
        return f"{match.group(1)} {hour}" + (f":{minute:02d}" if minute is not None else "")

    out = re.sub(rf"\b(at|by|around|until|till|before|after)\s+({_SPOKEN})"
                 rf"(?:\s+((?:oh\s+)?{_SPOKEN}))?(?:\s+o'?clock)?\b", clock, out, flags=flags)
    out = re.sub(rf"\ba\s+couple\s+(?:of\s+)?(?={_SPOKEN_UNITS}\b)", "2 ", out, flags=flags)
    return re.sub(rf"\b({_SPOKEN})(?=[\s-]+{_SPOKEN_UNITS}\b)",
                  lambda m: str(_spoken_number(m.group(1))), out, flags=flags)


def _iso(text: str, now: datetime) -> When | None:
    match = re.search(
        r"\b(\d{4})-(\d{2})-(\d{2})(?:[ t](\d{1,2}):(\d{2}))?\b", text)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour = int(match.group(4)) if match.group(4) else DEFAULT_HOUR
    minute = int(match.group(5)) if match.group(5) else 0
    try:
        at = datetime(year, month, day, hour, minute, tzinfo=now.tzinfo)
    except ValueError as exc:                       # 2026-02-30, 25:00
        raise TimeParseError(str(exc)) from exc
    return When(at, match.start(), match.end())


def _duration(text: str, now: datetime) -> When | None:
    units = "|".join(sorted(DURATION_UNITS, key=len, reverse=True))
    match = re.search(
        rf"\bin\s+(?:(?P<n>\d+)|(?P<article>an?)|(?P<half>half\s+an?))\s*(?P<unit>{units})\b",
        text)
    if not match:
        return None
    unit_seconds = DURATION_UNITS[match.group("unit")]
    if match.group("half"):
        seconds = unit_seconds / 2
    elif match.group("article"):
        seconds = unit_seconds
    else:
        seconds = int(match.group("n")) * unit_seconds
    if seconds <= 0:
        raise TimeParseError("A reminder cannot be set for no time at all.")
    return When(now + timedelta(seconds=seconds), match.start(), match.end())


# Words that say which half of the day a bare hour is in. "remind me to take out the trash
# tonight at 8" was set for 8 AM - the 7-12 rule reads a bare 8 as written - and "tomorrow
# morning at 6" for 6 PM, by the 1-6 rule. Only words next to the time count: "at 8 to
# prepare for the evening party" says nothing about which 8.
_PM_WORDS = re.compile(r"\b(?:tonight|evening|night|afternoon)\b")
_AM_WORDS = re.compile(r"\b(?:morning)\b")


def _half_of_day(text: str, start: int, end: int) -> str:
    """'pm', 'am' or '' from the words within a few words of a time."""
    window = text[max(0, start - 24): end + 24]
    if _PM_WORDS.search(window):
        return "pm"
    if _AM_WORDS.search(window):
        return "am"
    return ""


def _find_time(text: str, default_half: str = "") -> tuple[tuple[int, int], int, int] | None:
    """The rightmost time of day in `text`, with its span.

    Rightmost, because "call the 3 o'clock shift at 6pm" means six: a message often
    carries a number of its own, and the time is what the sentence ends on.

    A bare hour takes its half of the day from the words around it ("tonight at 8"), else
    from `default_half` - an alarm's "wake me up at 6" is six in the morning.
    """
    # A clock time beats a named part of the day wherever it sits: "at 6 tomorrow morning"
    # is six, not the 9:00 that "morning" means on its own.
    best: tuple[tuple[int, int], int, int] | None = None
    for patterns in (_TIME_PATTERNS, (_NAMED_TIME_PATTERN,)):
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                groups = match.groupdict()
                if groups.get("named"):
                    clock = NAMED_TIMES[groups["named"]]
                else:
                    hour = next(groups[k] for k in ("h1", "h2", "h3") if groups.get(k))
                    minute = next((groups[k] for k in ("m1", "m2") if groups.get(k)), None)
                    meridiem = (groups.get("mer1") or "").replace(".", "")
                    if not meridiem and 1 <= int(hour) <= 12:
                        meridiem = _half_of_day(text, match.start(), match.end()) or default_half
                    clock = _normalise_clock(
                        int(hour), int(minute or 0), meridiem, had_minutes=minute is not None)
                if best is None or match.end() > best[2]:
                    best = (clock, match.start(), match.end())
        if best is not None:
            return best
    return best


def _find_day(text: str, now: datetime) -> tuple[date, int, int, bool] | None:
    """A day anchor and its span. The flag says whether a time was implied by the words."""
    today = now.date()

    named = re.search(r"\b(day after tomorrow|tomorrow|today|tonight)\b", text)
    if named:
        word = named.group(1)
        offset = {"day after tomorrow": 2, "tomorrow": 1, "today": 0, "tonight": 0}[word]
        return today + timedelta(days=offset), named.start(), named.end(), word == "tonight"

    weekdays = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
    weekday = re.search(rf"\b(?:(?P<next>next|this|on)\s+)?(?P<name>{weekdays})\b", text)
    if weekday:
        target = WEEKDAYS[weekday.group("name")]
        ahead = (target - today.weekday()) % 7
        # A weekday that is today means today only when "this" or nothing qualifies it and
        # the clock has not passed; "next monday" on a Monday is always the one coming.
        if ahead == 0 and weekday.group("next") == "next":
            ahead = 7
        return today + timedelta(days=ahead), weekday.start(), weekday.end(), False

    months = "|".join(sorted(MONTHS, key=len, reverse=True))
    written = re.search(
        rf"\b(?:(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?P<mon1>{months})"
        rf"|(?P<mon2>{months})\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?)\b", text)
    if written:
        groups = written.groupdict()
        day = int(groups["d1"] or groups["d2"])
        month = MONTHS[groups["mon1"] or groups["mon2"]]
        year = now.year
        try:
            candidate = date(year, month, day)
        except ValueError as exc:
            raise TimeParseError(str(exc)) from exc
        if candidate < today:                      # a date already gone means next year
            try:
                candidate = date(year + 1, month, day)
            except ValueError as exc:              # 29 February
                raise TimeParseError(str(exc)) from exc
        return candidate, written.start(), written.end(), False

    next_week = re.search(r"\bnext week\b", text)
    if next_week:
        return today + timedelta(days=7), next_week.start(), next_week.end(), False
    return None


def _with_period_words(text: str, start: int, end: int) -> tuple[int, int]:
    """Widen a span over the part-of-day words beside it, so "at 6 tomorrow morning to run"
    leaves "run" and not "morning to run"."""
    after = re.match(r"\s*(?:in\s+the\s+|this\s+)?(?:morning|afternoon|evening|night)\b", text[end:])
    if after:
        end += after.end()
    before = re.search(r"(?:\b(?:in\s+the|this)\s+)?\b(?:morning|afternoon|evening|night)\s*$", text[:start])
    if before:
        start = before.start()
    return start, end


def _day_and_time(text: str, now: datetime, default_half: str = "") -> When | None:
    day = _find_day(text, now)
    time_found = _find_time(text, default_half)
    if day is None and time_found is None:
        return None

    if day is None:
        clock, start, end = time_found            # type: ignore[misc]
        start, end = _with_period_words(text, start, end)
        at = _apply(now.date(), clock, now)
        if at <= now:                             # "6pm" said at 7pm means tomorrow
            at += timedelta(days=1)
        return When(at, start, end)

    target, day_start, day_end, implied = day
    if time_found is not None:
        clock, time_start, time_end = time_found
        start, end = min(day_start, time_start), max(day_end, time_end)
    else:
        clock = NAMED_TIMES["tonight"] if implied else (DEFAULT_HOUR, 0)
        start, end = day_start, day_end
    start, end = _with_period_words(text, start, end)
    at = _apply(target, clock, now)
    # A weekday that resolves to today with a time already gone means the one coming.
    # "today"/"tonight" are exempt: the user named today, so a time that has passed is a
    # mistake worth reporting, not one to silently move a week.
    said_today = re.search(r"\b(today|tonight)\b", text) is not None
    if at <= now and target == now.date() and not said_today:
        at += timedelta(days=7)
    return When(at, start, end)


def describe(moment: datetime, now: datetime | None = None) -> str:
    """A human rendering of a resolved instant, for confirming it back to the user."""
    if now is None:
        now = datetime.now().astimezone()
    local = moment.astimezone(now.tzinfo)
    clock = local.strftime("%I:%M %p").lstrip("0")   # %-I is glibc only; see ERRORS.md
    days = (local.date() - now.astimezone(now.tzinfo).date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if 2 <= days < 7:
        return f"{local.strftime('%A')} at {clock}"
    return f"{local.strftime('%A, %d %B %Y')} at {clock}".replace(" 0", " ")
