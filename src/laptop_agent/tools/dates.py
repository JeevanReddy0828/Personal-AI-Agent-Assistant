"""Dates, counted rather than guessed: "how many days until christmas", "when is
thanksgiving", "what day is my wife's birthday", "how many days between march 1 and
april 15".

A language model asked this answers from a clock it cannot see, and gets day counts wrong
by one or two. The same principle as the calculator and the reminder parser: a value with
one right answer is computed.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timedelta, timezone

from laptop_agent.timeparse import MONTHS, TimeParseError, _find_day, _find_time, on_laptop_clock, parse_when


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
    # Federal holidays "the next holiday" skipped without them.
    "juneteenth": lambda y: date(y, 6, 19), "columbus day": lambda y: _nth_weekday(y, 10, 0, 2),
    "indigenous peoples' day": lambda y: _nth_weekday(y, 10, 0, 2),
    "indigenous peoples day": lambda y: _nth_weekday(y, 10, 0, 2),
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


def _pinned_year(text: str, today: date) -> tuple[str, int | None]:
    """The text without a stated year, and that year. "july 4th this year" is the one that has
    passed, "christmas next year" is not this December's, and "july 4th 2030" is in 2030: all
    three used to get the next time the date came round."""
    step = {"last": -1, "this": 0, "next": 1}
    leading = re.fullmatch(r"(this|next|last)\s+year'?s\s+(.+)", text, re.IGNORECASE)
    if leading:
        return leading.group(2), today.year + step[leading.group(1).lower()]
    # Any year from 1000: "what day of the week was july 4 1776" is a fair question.
    trailing = re.fullmatch(r"(.+?),?\s+(?:(?:of\s+)?(this|next|last)\s+year|(?:in\s+)?([12]\d{3}))", text, re.IGNORECASE)
    if trailing:
        year = int(trailing.group(3)) if trailing.group(3) else today.year + step[trailing.group(2).lower()]
        return trailing.group(1), year
    return text, None


def says_a_year(text: str, today: date) -> bool:
    """Whether a date phrase pins its year ("this year", "next year's", "2030")."""
    cleaned = re.sub(r"^\s*(?:the\s+)?", "", (text or "").strip().rstrip("?.!")).strip()
    return _pinned_year(cleaned, today)[1] is not None


_COUNT = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "twelve": 12}
# "2 weeks ago", "in 3 months", "10 days from now": counted from today on the calendar, so a
# month back from 31 March is the end of February, not 3 March.
_SHIFTED = re.compile(
    r"(?:in\s+(?P<n1>\d+|[a-z]+)\s+(?P<u1>day|week|month|year)s?(?:\s+(?:from\s+)?(?:now|today|time))?"
    r"|(?P<n2>\d+|[a-z]+)\s+(?P<u2>day|week|month|year)s?\s+(?P<dir>ago|from\s+(?:now|today)))",
    re.IGNORECASE,
)


def _shifted(text: str, today: date) -> date | None:
    asked = _SHIFTED.fullmatch(text)
    if not asked:
        return None
    said, unit = (asked.group("n1") or asked.group("n2")).lower(), (asked.group("u1") or asked.group("u2")).lower()
    count = int(said) if said.isdigit() else _COUNT.get(said)
    if count is None:
        return None
    if (asked.group("dir") or "").lower() == "ago":
        count = -count
    if unit in {"day", "week"}:
        return today + timedelta(days=count * (7 if unit == "week" else 1))
    index = today.month - 1 + count * (12 if unit == "year" else 1)
    year, month = today.year + index // 12, index % 12 + 1
    from calendar import monthrange

    try:
        return date(year, month, min(today.day, monthrange(year, month)[1]))
    except ValueError:      # out of range for a calendar
        return None


def _month_day(text: str, today: date, year: int | None = None) -> date | None:
    """"june 5", "5th of june", "12/25": the next time that date comes round, or in `year`."""
    months = "|".join(sorted(MONTHS, key=len, reverse=True))
    written = re.search(rf"\b(?:(?P<d1>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m1>{months})"
                        rf"|(?P<m2>{months})\s+(?P<d2>\d{{1,2}})(?:st|nd|rd|th)?)\b", text, re.IGNORECASE)
    if not written:
        return None
    groups = written.groupdict()
    month = MONTHS[(groups["m1"] or groups["m2"]).lower()]
    day = int(groups["d1"] or groups["d2"])
    try:
        if year is not None:
            return date(year, month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError:
        return None


def resolve(text: str, now: datetime, profile: dict[str, object] | None = None) -> tuple[date, str] | None:
    """(the date, what to call it) for a holiday, a remembered date, or any date phrase."""
    today = now.date()
    cleaned = re.sub(r"^\s*(?:the\s+)?", "", (text or "").strip().rstrip("?.!")).strip()
    # Only the holiday and calendar-date readings take a stated year: "end of this year" has its own.
    base, year = _pinned_year(cleaned, today)
    holiday = re.fullmatch(rf"(?:next\s+)?({_HOLIDAY})", base, re.IGNORECASE)
    if holiday:
        name = holiday.group(1).lower()
        found = _HOLIDAYS[name](year) if year is not None else next_holiday(name, today)
        if found:
            return found, " ".join(word[:1].upper() + word[1:] for word in name.split())
    relative = _OFFSETS.get(" ".join(cleaned.lower().split()))
    if relative is not None:
        return today + timedelta(days=relative), cleaned.lower()
    shifted = _shifted(cleaned, today)
    if shifted is not None:
        return shifted, cleaned.lower()
    if re.fullmatch(r"end\s+of\s+(?:the|this)\s+year", cleaned, re.IGNORECASE):
        return date(today.year, 12, 31), "the end of the year"
    if re.fullmatch(r"end\s+of\s+(?:the|this)\s+month", cleaned, re.IGNORECASE):
        return date(today.year + (today.month == 12), today.month % 12 + 1, 1) - timedelta(days=1), "the end of the month"
    # "my birthday", "my wife's birthday", "our anniversary": a date the user told us.
    personal = re.fullmatch(r"(?:my|our)\s+(.+)", base, re.IGNORECASE)
    if personal and profile:
        wanted = re.sub(r"[^a-z0-9]+", " ", personal.group(1).lower()).strip()
        for key, value in profile.items():
            if re.sub(r"[^a-z0-9]+", " ", str(key).lower()).strip() == wanted:
                found = _month_day(str(value), today, year)
                if found:
                    return found, f"your {personal.group(1)}"
    found = _month_day(base, today, year)
    if found:
        return found, found.strftime("%B %d").replace(" 0", " ")
    try:
        when = parse_when(cleaned, now, local=True)
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


# "how many days until christmas", "how long till my birthday", "days until friday",
# "how many minutes until midnight", "how much time until 9:30"
_UNTIL = re.compile(
    r"^\s*(?:how\s+many\s+(?:more\s+)?(?P<unit>days|sleeps|weeks|hours|minutes)\s+(?:until|till|til|to|before"
    r"|left\s+(?:until|till|before))"
    r"|how\s+long\s+(?:is\s+it\s+)?(?:until|till|til|before)|how\s+much\s+time\s+(?:is\s+)?(?:left\s+)?(?:until|till|til)"
    r"|days\s+(?:until|till|til|to|left\s+until))\s+"
    r"(?P<what>.+?)\s*[?.!]*$",
    re.IGNORECASE,
)


def until_moment(text: str, unit: str, now: datetime, profile: dict[str, object] | None = None) -> datetime | None:
    """The instant an "until" question counts to, when it is counted in hours rather than days.

    "how long until midnight" counted the days to the date of the next midnight and said
    "1 day", four hours before it. A time of day is counted to that moment; a day asked for in
    hours or minutes ("how many hours until christmas") to its start. Anything else is None,
    and is counted in days.
    """
    cleaned = re.sub(r"^\s*(?:the\s+)?", "", (text or "").strip().rstrip("?.!")).strip()
    if _find_time(cleaned.lower()) is not None:
        # A clock said without am/pm is the sooner of its two readings: "until 9:30" at 7pm is tonight's.
        halves = ("am", "pm") if re.fullmatch(r"\d{1,2}:\d{2}", cleaned) else ("",)
        readings = []
        for half in halves:
            try:
                when = parse_when(cleaned, now, default_half=half, local=True)
            except TimeParseError:
                return None
            if when is None or when.start != 0 or when.end < len(cleaned) - 1:
                return None
            readings.append(when.at)
        # The hour the clocks go back happens twice: "until 1:30am" at the first 1:45 is the second
        # 1:30, 45 minutes away, not tomorrow's - and so is "1:30am today", and "sunday at 1:30am"
        # on that Sunday (Codex's reviews of #253). The parse moved on a day, or a week for a named
        # weekday, past the first reading, so today's is looked for that far back; "today" or a
        # date stays on its day, and "friday at 5pm" is never given the Thursday. A reading counts
        # only if it is that wall time, so a time the spring change skips keeps its rule.
        # "next sunday" is never today, so it does not look back (Codex's third review).
        if (re.search(r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)day\b", cleaned, re.IGNORECASE)
                and not re.search(r"\bnext\b", cleaned, re.IGNORECASE)):
            backs: tuple[int, ...] = (0, 7)
        else:
            backs = (0,) if _find_day(cleaned.lower(), now) is not None else (0, 1)
        for at in list(readings):
            wall = at.replace(tzinfo=None)
            for back in backs:
                for fold in (0, 1):
                    guess = (wall - timedelta(days=back)).replace(fold=fold)
                    moment = on_laptop_clock(guess, now.tzinfo)
                    # Read back through UTC: a zone converted to itself is returned unchanged.
                    shown = on_laptop_clock(moment.astimezone(timezone.utc), now.tzinfo)
                    if shown.replace(tzinfo=None) == guess:
                        readings.append(moment)
        # The next one to come; for a time already gone today, the latest that went.
        ahead = [at for at in readings if at > now]
        return min(ahead) if ahead else max(readings)
    if unit in ("hours", "minutes"):
        found = resolve(text, now, profile)
        if found is not None:
            return on_laptop_clock(datetime(found[0].year, found[0].month, found[0].day), now.tzinfo)
    return None


def span_text(minutes: int, unit: str = "") -> str:
    """"4 hours 25 minutes", or all in the unit asked for: "265 minutes"."""
    if minutes <= 0:
        return "less than a minute"
    if unit == "minutes":
        return f"{minutes:,} minute{'s' if minutes != 1 else ''}"
    days, rest = (0, minutes) if unit == "hours" else divmod(minutes, 1440)
    hours, spare = divmod(rest, 60)
    return " ".join(f"{count:,} {word}{'s' if count != 1 else ''}"
                    for count, word in ((days, "day"), (hours, "hour"), (spare, "minute")) if count)
# "when is thanksgiving", "what day is christmas", "what date is easter this year"
# Past and future tense too: "what day was july 4 1776", "what day will it be in 10 days",
# "what was the date 2 weeks ago" went to the chat model.
_WHEN = re.compile(
    r"^\s*(?:when(?:'s|s|\s+is|\s+was|\s+will\s+be)|what\s+(?:day|date)\s+(?:will\s+it\s+be|was\s+it|is\s+it|is|does|was)"
    r"|what\s+day\s+of\s+the\s+week\s+(?:is|was)"
    r"|what(?:'s|s|\s+is|\s+was|\s+will\s+be)\s+the\s+(?:date|day)(?:\s+(?:on|of))?)\s+"
    r"(?P<what>.+?)(?:\s+(?:fall|land)\s+on)?\s*[?.!]*$",
    re.IGNORECASE,
)
_BETWEEN = re.compile(
    r"^\s*(?:how\s+many\s+days\s+(?:are\s+there\s+)?|days\s+)between\s+(?P<a>.+?)\s+and\s+(?P<b>.+?)\s*[?.!]*$",
    re.IGNORECASE,
)
# "how many days since march 1": the span from then to today.
_SINCE = re.compile(
    r"^\s*(?:how\s+many\s+days\s+(?:has\s+it\s+been\s+|have\s+passed\s+|is\s+it\s+)?|how\s+long\s+(?:has\s+it\s+been\s+)?"
    r"|days\s+)since\s+(?P<a>.+?)\s*[?.!]*$",
    re.IGNORECASE,
)
# "how many days left in the year", "how many weeks are left in this month".
_LEFT_IN = re.compile(
    r"^\s*how\s+many\s+(?:more\s+)?(?P<unit>days|weeks)\s+(?:are\s+)?(?:left|remaining)\s+(?:in|of)\s+(?:the|this)\s+"
    r"(?P<span>year|month)\s*[?.!]*$",
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


# Calendar facts with one right answer, which reached the chat model or a web search for the
# sentence: "what week of the year is it", "is 2028 a leap year", "how many days in february",
# "is today a holiday", "how old am i if i was born in 1995". Each is computed, never recalled.
_MONTH_NAME = "(?:" + "|".join(sorted(MONTHS, key=len, reverse=True)) + ")"
_FACT = re.compile(
    r"^\s*(?:"
    r"(?P<week>(?:what|which)\s+week\s+(?:of\s+the\s+year\s+)?(?:is\s+it|are\s+we\s+in)(?:\s+(?:now|today))?"
    r"|what(?:'s|s|\s+is)\s+(?:the\s+)?(?:current\s+)?week\s+number(?:\s+today)?)"
    r"|(?P<yday>what\s+day\s+of\s+the\s+year\s+is\s+(?:it|today)(?:\s+today)?)"
    r"|(?P<leap>is\s+(?:(?P<leap_year>\d{4})|this\s+year|next\s+year)\s+a\s+leap\s+year|when\s+is\s+the\s+next\s+leap\s+year)"
    rf"|(?P<span>how\s+many\s+days\s+(?:are\s+)?(?:there\s+)?in\s+(?:(?P<month>{_MONTH_NAME})(?:\s+(?P<month_year>\d{{4}}))?"
    r"|(?P<rel>this|next|last)\s+month))"
    r"|(?P<quarter>(?:what|which)\s+quarter\s+(?:is\s+it|are\s+we\s+in)(?:\s+(?:now|today))?)"
    r"|(?P<ahead>what(?:'s|s|\s+is)\s+(?P<count>\d+)\s+(?P<unit>days?|weeks?)\s+(?:from|after)\s+(?:today|now))"
    r"|(?P<weekend>how\s+(?:many\s+days|long)\s+(?:is\s+it\s+)?(?:until|till|til|to)\s+the\s+weekend)"
    r"|(?P<holiday>is\s+(?P<which_day>today|tomorrow)\s+a\s+(?:public\s+|national\s+|bank\s+|federal\s+)?holiday)"
    r"|(?P<next>(?:when\s+is|what(?:'s|s|\s+is))\s+the\s+next\s+(?:public\s+|national\s+|bank\s+|federal\s+)?holiday)"
    r"|(?P<age>how\s+(?P<days_old>many\s+days\s+)?old\s+am\s+i\s+if\s+i\s+was\s+born\s+(?:in|on)\s+(?P<born>.+?))"
    r")\s*[?.!]*$",
    re.IGNORECASE,
)
# One name for each holiday, as the answers say it.
_NAMED_HOLIDAYS = ("new year's day", "martin luther king day", "presidents day", "valentine's day", "st patrick's day",
                   "easter", "mother's day", "memorial day", "father's day", "juneteenth", "independence day",
                   "labor day", "columbus day", "halloween", "veterans day", "thanksgiving", "christmas eve", "christmas",
                   "new year's eve")


def _title(name: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in name.split())


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def calendar_fact(text: str, today: date) -> str | None:
    """The answer to a calendar fact `_FACT` matches, or None when it cannot be computed."""
    match = _FACT.match(text or "")
    if match is None:
        return None
    if match.group("week"):
        year, week, _ = today.isocalendar()
        return f"It's week **{week}** of {year} (ISO weeks, which start on Monday)."
    if match.group("yday"):
        day = today.timetuple().tm_yday
        left = (date(today.year, 12, 31) - today).days
        return f"Today is day **{day}** of {today.year}, with {left} day{'s' if left != 1 else ''} left."
    if match.group("leap"):
        if match.group("leap_year") or re.search(r"\b(?:this|next)\s+year\b", match.group("leap"), re.IGNORECASE):
            year = int(match.group("leap_year")) if match.group("leap_year") else (
                today.year + (1 if "next" in match.group("leap").lower() else 0))
            return f"**{'Yes' if _is_leap(year) else 'No'}** — {year} {'is' if _is_leap(year) else 'is not'} a leap year."
        year = today.year + 1
        while not _is_leap(year):
            year += 1
        return f"The next leap year is **{year}**."
    if match.group("span"):
        if match.group("rel"):
            month_start = date(today.year, today.month, 1)
            shift = {"this": 0, "next": 1, "last": -1}[match.group("rel").lower()]
            month_index = month_start.month - 1 + shift
            year, month = month_start.year + month_index // 12, month_index % 12 + 1
        else:
            month = MONTHS[match.group("month").lower()]
            year = int(match.group("month_year")) if match.group("month_year") else today.year
        days = calendar.monthrange(year, month)[1]
        return f"{calendar.month_name[month]} {year} has **{days} days**."
    if match.group("quarter"):
        quarter = (today.month - 1) // 3 + 1
        return f"It's **Q{quarter}** of {today.year}."
    if match.group("ahead"):
        count = int(match.group("count"))
        days = count * (7 if match.group("unit").lower().startswith("week") else 1)
        day = today + timedelta(days=days)
        return f"{count} {match.group('unit').lower()} from today is **{day:%A, %d %B %Y}**.".replace(" 0", " ")
    if match.group("weekend"):
        if today.weekday() >= 5:
            return "It's the weekend now."
        days = 5 - today.weekday()
        return f"**{days} day{'s' if days != 1 else ''}** until the weekend (Saturday)."
    if match.group("holiday") or match.group("next"):
        dated = sorted((next_holiday(name, today), name) for name in _NAMED_HOLIDAYS)
        if match.group("next"):
            day, name = next((pair for pair in dated if pair[0] > today), dated[0])
            return (f"The next holiday is **{_title(name)}**, {day:%A, %d %B %Y}.".replace(" 0", " ")
                    + " (US-leaning; I don't have a regional holiday calendar.)")
        asked = today + timedelta(days=1 if match.group("which_day").lower() == "tomorrow" else 0)
        on = [name for name in _NAMED_HOLIDAYS if next_holiday(name, asked) == asked]
        if on:
            return f"**Yes** — {match.group('which_day').lower()} is {_title(on[0])}."
        return (f"**No** — {match.group('which_day').lower()} isn't one of the holidays I know "
                "(US-leaning; I don't have a regional holiday calendar).")
    if match.group("age"):
        born_text = match.group("born").strip()
        if re.fullmatch(r"\d{4}", born_text):
            year = int(born_text)
            if year > today.year:
                return None
            older = today.year - year
            return f"You're **{older - 1}** or **{older}** — {older} once your birthday has passed this year."
        born_year = re.search(r"\b(\d{4})\b", born_text)
        born = _month_day(re.sub(r"\b\d{4}\b", "", born_text).strip(" ,"), today, int(born_year.group(1))) if born_year else None
        if born is None or born > today:
            return None
        if match.group("days_old"):
            return f"You're **{(today - born).days:,} days** old."
        age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        return f"You're **{age}**."
    return None


def date_question(text: str) -> tuple[str, str, str] | None:
    """("fact"|"until"|"when"|"between", a, b) when the text asks one of these, else None. For
    "until", b is "weeks" when the question counted in weeks."""
    if _FACT.match(text or ""):
        return "fact", text, ""
    relative = _RELATIVE_DAY.match(text or "")
    if relative:
        return "when", relative.group("what").lower(), ""
    left = _LEFT_IN.match(text or "")
    if left:
        return "until", f"end of the {left.group('span').lower()}", "weeks" if left.group("unit").lower() == "weeks" else ""
    since = _SINCE.match(text or "")
    if since:
        return "between", since.group("a"), "today"
    for kind, pattern in (("between", _BETWEEN), ("until", _UNTIL), ("when", _WHEN)):
        match = pattern.match(text or "")
        if match:
            groups = match.groupdict()
            if kind == "until":
                unit = (groups.get("unit") or "").lower()
                return kind, groups["what"], unit if unit in ("weeks", "hours", "minutes") else ""
            return kind, groups.get("what") or groups.get("a") or "", groups.get("b") or ""
    return None


def answerable(text: str, now: datetime) -> bool:
    """Whether a date question is one this can answer: a holiday, a date, or something of
    the user's own ("my birthday"). "when is the next train" is not, and goes elsewhere."""
    asked = date_question(text)
    if asked is None:
        return False
    kind, first, second = asked
    if kind == "fact":
        return calendar_fact(first, now.date()) is not None
    targets = [first, second] if kind == "between" else [first]
    return all(re.match(r"\s*(?:my|our)\s+\S", target, re.IGNORECASE) or resolve(target, now) for target in targets)
