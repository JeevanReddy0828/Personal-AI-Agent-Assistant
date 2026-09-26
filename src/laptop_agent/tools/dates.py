"""Dates, counted rather than guessed: "how many days until christmas", "when is
thanksgiving", "what day is my wife's birthday", "how many days between march 1 and
april 15".

A language model asked this answers from a clock it cannot see, and gets day counts wrong
by one or two. The same principle as the calculator and the reminder parser: a value with
one right answer is computed.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from laptop_agent.timeparse import MONTHS, TimeParseError, parse_when


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th weekday (0=Monday) of a month; n=-1 is the last."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Western Easter Sunday (the anonymous Gregorian computus)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


# name -> how to find it in a given year. US-leaning where a holiday is national; the
# answer always states the date, so a wrong assumption is visible at once.
_HOLIDAYS = {
    "christmas": lambda y: date(y, 12, 25), "christmas day": lambda y: date(y, 12, 25),
    "christmas eve": lambda y: date(y, 12, 24), "new year's day": lambda y: date(y, 1, 1),
    "new year": lambda y: date(y, 1, 1), "new years": lambda y: date(y, 1, 1),
    "new year's eve": lambda y: date(y, 12, 31), "new years eve": lambda y: date(y, 12, 31),
    "valentine's day": lambda y: date(y, 2, 14), "valentines day": lambda y: date(y, 2, 14),
    "valentines": lambda y: date(y, 2, 14), "st patrick's day": lambda y: date(y, 3, 17),
    "halloween": lambda y: date(y, 10, 31), "independence day": lambda y: date(y, 7, 4),
    "fourth of july": lambda y: date(y, 7, 4), "4th of july": lambda y: date(y, 7, 4),
    "july 4th": lambda y: date(y, 7, 4), "thanksgiving": lambda y: _nth_weekday(y, 11, 3, 4),
    "mother's day": lambda y: _nth_weekday(y, 5, 6, 2), "mothers day": lambda y: _nth_weekday(y, 5, 6, 2),
    "father's day": lambda y: _nth_weekday(y, 6, 6, 3), "fathers day": lambda y: _nth_weekday(y, 6, 6, 3),
    "memorial day": lambda y: _nth_weekday(y, 5, 0, -1), "labor day": lambda y: _nth_weekday(y, 9, 0, 1),
    "labour day": lambda y: _nth_weekday(y, 9, 0, 1), "easter": _easter, "easter sunday": _easter,
    "martin luther king day": lambda y: _nth_weekday(y, 1, 0, 3), "mlk day": lambda y: _nth_weekday(y, 1, 0, 3),
    "presidents day": lambda y: _nth_weekday(y, 2, 0, 3), "presidents' day": lambda y: _nth_weekday(y, 2, 0, 3),
    "veterans day": lambda y: date(y, 11, 11), "boxing day": lambda y: date(y, 12, 26),
}
_HOLIDAY = "(?:" + "|".join(re.escape(name) for name in sorted(_HOLIDAYS, key=len, reverse=True)) + ")"
_OFFSETS = {"today": 0, "tomorrow": 1, "yesterday": -1, "day after tomorrow": 2, "day before yesterday": -2}
RELATIVE_DAYS = frozenset(_OFFSETS)


def next_holiday(name: str, today: date) -> date | None:
    """The next time a holiday falls on or after today."""
    finder = _HOLIDAYS.get(name.lower().strip().rstrip("?.!"))
    if finder is None:
        return None
    this_year = finder(today.year)
    return this_year if this_year >= today else finder(today.year + 1)


def _month_day(text: str, today: date) -> date | None:
    """"june 5", "5th of june", "12/25": the next time that date comes round."""
    months = "|".join(sorted(MONTHS, key=len, reverse=True))
    written = re.search(rf"\b(?:(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m1>{months})"
                        rf"|(?P<m2>{months})\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?)\b", text, re.IGNORECASE)
    if not written:
        return None
    groups = written.groupdict()
    month = MONTHS[(groups["m1"] or groups["m2"]).lower()]
    day = int(groups["d1"] or groups["d2"])
    try:
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def resolve(text: str, now: datetime, profile: dict[str, object] | None = None) -> tuple[date, str] | None:
    """(the date, what to call it) for a holiday, a remembered date, or any date phrase."""
    today = now.date()
    cleaned = re.sub(r"^\s*(?:the\s+)?", "", (text or "").strip().rstrip("?.!")).strip()
    holiday = re.fullmatch(rf"(?:this\s+year'?s\s+|next\s+)?({_HOLIDAY})(?:\s+(?:this|next)\s+year)?", cleaned,
                           re.IGNORECASE)
    if holiday:
        found = next_holiday(holiday.group(1), today)
        if found:
            return found, " ".join(word[:1].upper() + word[1:] for word in holiday.group(1).lower().split())
    relative = _OFFSETS.get(" ".join(cleaned.lower().split()))
    if relative is not None:
        return today + timedelta(days=relative), cleaned.lower()
    if re.fullmatch(r"end\s+of\s+(?:the|this)\s+year", cleaned, re.IGNORECASE):
        return date(today.year, 12, 31), "the end of the year"
    if re.fullmatch(r"end\s+of\s+(?:the|this)\s+month", cleaned, re.IGNORECASE):
        return date(today.year + (today.month == 12), today.month % 12 + 1, 1) - timedelta(days=1), "the end of the month"
    # "my birthday", "my wife's birthday", "our anniversary": a date the user told us.
    personal = re.fullmatch(r"(?:my|our)\s+(.+)", cleaned, re.IGNORECASE)
    if personal and profile:
        wanted = re.sub(r"[^a-z0-9]+", " ", personal.group(1).lower()).strip()
        for key, value in profile.items():
            if re.sub(r"[^a-z0-9]+", " ", str(key).lower()).strip() == wanted:
                found = _month_day(str(value), today)
                if found:
                    return found, f"your {personal.group(1)}"
    found = _month_day(cleaned, today)
    if found:
        return found, found.strftime("%B %d").replace(" 0", " ")
    try:
        when = parse_when(cleaned, now)
    except TimeParseError:
        return None
    if when is not None and when.start == 0 and when.end >= len(cleaned) - 1:
        return when.at.date(), cleaned
    return None


def describe_day(day: date, today: date) -> str:
    days = (day - today).days
    stamp = day.strftime("%A, %d %B %Y").replace(" 0", " ")
    if days == 0:
        return f"{stamp} — that's today"
    if days == 1:
        return f"{stamp} — tomorrow"
    return f"{stamp} — {days} days from today" if days > 0 else f"{stamp} — {-days} days ago"


# "how many days until christmas", "how long till my birthday", "days until friday"
_UNTIL = re.compile(
    r"^\s*(?:how\s+many\s+(?:more\s+)?(?P<unit>days|sleeps|weeks)\s+(?:until|till|til|to|before|left\s+(?:until|till|before))"
    r"|how\s+long\s+(?:is\s+it\s+)?(?:until|till|til|before)|days\s+(?:until|till|til|to|left\s+until))\s+"
    r"(?P<what>.+?)\s*[?.!]*$",
    re.IGNORECASE,
)
# "when is thanksgiving", "what day is christmas", "what date is easter this year"
_WHEN = re.compile(
    r"^\s*(?:when(?:'s|s|\s+is)|what\s+(?:day|date)\s+(?:is|does)|what\s+day\s+of\s+the\s+week\s+is)\s+"
    r"(?P<what>.+?)(?:\s+(?:fall|land)\s+on)?\s*[?.!]*$",
    re.IGNORECASE,
)
_BETWEEN = re.compile(
    r"^\s*(?:how\s+many\s+days\s+(?:are\s+there\s+)?|days\s+)between\s+(?P<a>.+?)\s+and\s+(?P<b>.+?)\s*[?.!]*$",
    re.IGNORECASE,
)
# "what's today", "what's the date tomorrow", "what's tomorrow's date": the date of a day
# named relative to this one. "what's today" went to a web search for the sentence.
_RELATIVE_DAY = re.compile(
    r"^\s*(?:what(?:'s|s|\s+is)\s+(?:the\s+(?:date|day)\s+)?|what\s+(?:date|day)\s+(?:is\s+(?:it\s+)?|was\s+(?:it\s+)?)?)"
    r"(?P<what>today|tomorrow|yesterday|the\s+day\s+after\s+tomorrow|the\s+day\s+before\s+yesterday)"
    r"(?:'?s\s+date)?\s*[?.!]*$",
    re.IGNORECASE,
)


def date_question(text: str) -> tuple[str, str, str] | None:
    """("until"|"when"|"between", a, b) when the text asks one of these, else None. For
    "until", b is "weeks" when the question counted in weeks."""
    relative = _RELATIVE_DAY.match(text or "")
    if relative:
        return "when", relative.group("what").lower(), ""
    for kind, pattern in (("between", _BETWEEN), ("until", _UNTIL), ("when", _WHEN)):
        match = pattern.match(text or "")
        if match:
            groups = match.groupdict()
            if kind == "until":
                return kind, groups["what"], "weeks" if (groups.get("unit") or "").lower() == "weeks" else ""
            return kind, groups.get("what") or groups.get("a") or "", groups.get("b") or ""
    return None


def answerable(text: str, now: datetime) -> bool:
    """Whether a date question is one this can answer: a holiday, a date, or something of
    the user's own ("my birthday"). "when is the next train" is not, and goes elsewhere."""
    asked = date_question(text)
    if asked is None:
        return False
    kind, first, second = asked
    targets = [first, second] if kind == "between" else [first]
    return all(re.match(r"\s*(?:my|our)\s+\S", target, re.IGNORECASE) or resolve(target, now) for target in targets)
