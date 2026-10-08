"""What time it is, from the clock on this laptop.

Asked "what is the current date and time in EST", the assistant ran a web search, scraped
a stale page and answered 1:00 PM while the machine's own clock read 6:26 PM. Told it was
wrong, it invented an explanation about reading a 24-hour clock and cited a source that
does not exist. The time is not a web fact - it is sitting in the operating system.

Zone handling uses stdlib `zoneinfo`, which on Windows needs the `tzdata` package. When
that is absent only local time is available, and asking for another zone says so rather
than guessing an offset: a wrong time confidently stated is exactly the failure this
replaces.

The abbreviation people type is usually the standard-time one even in summer. Asking for
"EST" in September deserves the real answer - Eastern time, currently on EDT - not a
silent five-hour error, so a zone asked for by abbreviation reports which of the two is
actually in effect.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from laptop_agent.tools.base import ToolResult

# What people type -> an IANA zone. Abbreviations are ambiguous by nature (IST is India,
# Ireland and Israel); these are the readings that come up in practice, and the reply
# always names the resolved zone so a wrong guess is visible rather than silent.
_ZONE_WORDS: dict[str, str] = {
    "est": "America/New_York", "edt": "America/New_York", "et": "America/New_York",
    "eastern": "America/New_York", "new york": "America/New_York", "nyc": "America/New_York",
    "cst": "America/Chicago", "cdt": "America/Chicago", "ct": "America/Chicago",
    "central": "America/Chicago", "chicago": "America/Chicago",
    "mst": "America/Denver", "mdt": "America/Denver", "mountain": "America/Denver",
    "pst": "America/Los_Angeles", "pdt": "America/Los_Angeles", "pt": "America/Los_Angeles",
    "pacific": "America/Los_Angeles", "la": "America/Los_Angeles",
    "los angeles": "America/Los_Angeles", "san francisco": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "utc": "UTC", "gmt": "UTC", "zulu": "UTC",
    "bst": "Europe/London", "london": "Europe/London", "uk": "Europe/London",
    "cet": "Europe/Paris", "cest": "Europe/Paris", "paris": "Europe/Paris",
    "berlin": "Europe/Berlin", "germany": "Europe/Berlin", "amsterdam": "Europe/Amsterdam",
    "ist": "Asia/Kolkata", "india": "Asia/Kolkata", "kolkata": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata", "hyderabad": "Asia/Kolkata", "chennai": "Asia/Kolkata",
    "jst": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo",
    "sgt": "Asia/Singapore", "singapore": "Asia/Singapore",
    "hkt": "Asia/Hong_Kong", "hong kong": "Asia/Hong_Kong",
    "china": "Asia/Shanghai", "beijing": "Asia/Shanghai", "shanghai": "Asia/Shanghai",
    "dubai": "Asia/Dubai", "gst": "Asia/Dubai", "uae": "Asia/Dubai",
    "aest": "Australia/Sydney", "aedt": "Australia/Sydney", "sydney": "Australia/Sydney",
    "melbourne": "Australia/Melbourne", "australia": "Australia/Sydney",
    "nzst": "Pacific/Auckland", "auckland": "Pacific/Auckland",
    "brt": "America/Sao_Paulo", "sao paulo": "America/Sao_Paulo", "brazil": "America/Sao_Paulo",
    "toronto": "America/Toronto", "vancouver": "America/Vancouver",
    "mexico city": "America/Mexico_City", "sast": "Africa/Johannesburg",
    "johannesburg": "Africa/Johannesburg", "lagos": "Africa/Lagos", "cairo": "Africa/Cairo",
    "moscow": "Europe/Moscow", "msk": "Europe/Moscow",
    # US states and cities, by the zone most of the state keeps. "what time is it in
    # california" was refused as an unknown zone.
    "california": "America/Los_Angeles", "oregon": "America/Los_Angeles", "nevada": "America/Los_Angeles",
    "washington state": "America/Los_Angeles", "las vegas": "America/Los_Angeles",
    "san diego": "America/Los_Angeles", "silicon valley": "America/Los_Angeles",
    "san jose": "America/Los_Angeles", "arizona": "America/Phoenix", "phoenix": "America/Phoenix",
    "colorado": "America/Denver", "denver": "America/Denver", "utah": "America/Denver",
    "salt lake city": "America/Denver", "new mexico": "America/Denver", "montana": "America/Denver",
    "texas": "America/Chicago", "austin": "America/Chicago", "dallas": "America/Chicago",
    "houston": "America/Chicago", "san antonio": "America/Chicago", "illinois": "America/Chicago",
    "minnesota": "America/Chicago", "minneapolis": "America/Chicago", "wisconsin": "America/Chicago",
    "missouri": "America/Chicago", "louisiana": "America/Chicago", "new orleans": "America/Chicago",
    "oklahoma": "America/Chicago", "iowa": "America/Chicago", "arkansas": "America/Chicago",
    "alabama": "America/Chicago", "mississippi": "America/Chicago", "nashville": "America/Chicago",
    "new york state": "America/New_York", "florida": "America/New_York", "miami": "America/New_York",
    "orlando": "America/New_York", "atlanta": "America/New_York", "boston": "America/New_York",
    "massachusetts": "America/New_York", "philadelphia": "America/New_York",
    "pennsylvania": "America/New_York", "washington dc": "America/New_York", "dc": "America/New_York",
    "virginia": "America/New_York", "north carolina": "America/New_York", "charlotte": "America/New_York",
    "south carolina": "America/New_York", "new jersey": "America/New_York", "ohio": "America/New_York",
    "michigan": "America/New_York", "detroit": "America/Detroit", "maryland": "America/New_York",
    "connecticut": "America/New_York", "alaska": "America/Anchorage", "hawaii": "Pacific/Honolulu",
    "honolulu": "Pacific/Honolulu", "canada": "America/Toronto", "ottawa": "America/Toronto",
    "montreal": "America/Toronto",
    # Countries and the places people most often ask about.
    "england": "Europe/London", "britain": "Europe/London", "scotland": "Europe/London",
    "ireland": "Europe/Dublin", "france": "Europe/Paris", "spain": "Europe/Madrid", "italy": "Europe/Rome",
    "netherlands": "Europe/Amsterdam", "portugal": "Europe/Lisbon", "switzerland": "Europe/Zurich",
    "sweden": "Europe/Stockholm", "norway": "Europe/Oslo", "poland": "Europe/Warsaw",
    "greece": "Europe/Athens", "turkey": "Europe/Istanbul", "russia": "Europe/Moscow",
    "israel": "Asia/Jerusalem", "saudi arabia": "Asia/Riyadh", "egypt": "Africa/Cairo",
    "kenya": "Africa/Nairobi", "nigeria": "Africa/Lagos", "south africa": "Africa/Johannesburg",
    "pakistan": "Asia/Karachi", "bangladesh": "Asia/Dhaka", "nepal": "Asia/Kathmandu",
    "sri lanka": "Asia/Colombo", "thailand": "Asia/Bangkok", "vietnam": "Asia/Ho_Chi_Minh",
    "indonesia": "Asia/Jakarta", "philippines": "Asia/Manila", "malaysia": "Asia/Kuala_Lumpur",
    "korea": "Asia/Seoul", "south korea": "Asia/Seoul", "taiwan": "Asia/Taipei",
    "new zealand": "Pacific/Auckland", "argentina": "America/Argentina/Buenos_Aires",
    "buenos aires": "America/Argentina/Buenos_Aires", "chile": "America/Santiago",
    "colombia": "America/Bogota", "peru": "America/Lima", "mexico": "America/Mexico_City",
    "pune": "Asia/Kolkata", "noida": "Asia/Kolkata", "gurgaon": "Asia/Kolkata", "gurugram": "Asia/Kolkata",
    "ahmedabad": "Asia/Kolkata", "vizag": "Asia/Kolkata", "visakhapatnam": "Asia/Kolkata",
    "vijayawada": "Asia/Kolkata", "warangal": "Asia/Kolkata", "tirupati": "Asia/Kolkata",
}


def _iana_city(name: str) -> str | None:
    """"lisbon" -> Europe/Lisbon: any city the zone database itself is named after, so
    the table above only has to hold what that cannot find (states, countries, nicknames)."""
    wanted = name.strip().replace(" ", "_").lower()
    if not wanted or "/" in wanted:
        return None
    try:
        from zoneinfo import available_timezones

        zones = available_timezones()
    except Exception:  # no tz database on this machine: the table is all there is
        return None
    for zone in sorted(zones):
        # A city is always Area/City; bare names are legacy aliases or "Factory".
        if "/" not in zone or zone.startswith(("Etc/", "SystemV/", "posix/", "right/")):
            continue
        if zone.rsplit("/", 1)[-1].lower() == wanted:
            return zone
    return None

# Abbreviations that name *standard* time, so asking for one in summer deserves a note
# about which half of the year is actually in effect.
_STANDARD_ONLY = {"est", "cst", "mst", "pst", "jst", "ist", "gst", "sgt", "hkt", "sast", "nzst"}

# A question about the clock, not about the world. Routed here it is answered from the
# operating system; routed to a web search it produced a five-hour error.
# What may follow the time word is part of the pattern. It used to stop at the word, so
# anything that merely STARTED with it was a clock question: "time management tips" and
# "date night ideas" read the clock, and "time for a break" failed with "I do not know the
# time zone 'a break'".
_ASKS_THE_TIME = re.compile(
    r"^\s*(?:(?:can|could|would|will)\s+(?:you|u)\s+(?:please\s+)?|please\s+|do\s+you\s+know\s+)?"
    r"(?:(?:tell|show|give)\s+me\s+)?(?:what(?:'s| is)?|whats|wat|wut)?\s*"
    r"(?:the\s+)?(?:current\s+|today'?s\s+|now\s+|local\s+)?"
    r"(?:date\s*(?:and|&|/|,)?\s*time|time\s*(?:and|&|/|,)?\s*date|time|date|day)\b"
    r"(?:\s+(?:is\s+it|it\s+is|now|right\s+now|today|currently|please|here|there"
    r"|(?:in|at)\s+[a-z][a-z /_.-]{0,40}))*\s*$",
    re.IGNORECASE,
)
_TIME_WORD = re.compile(r"\b(?:time|date|day|clock|o'?clock)\b", re.IGNORECASE)


def asks_the_time(text: str) -> bool:
    """True when the message is asking what time or date it is.

    Deliberately narrow: "what time is the meeting" and "time to refactor this" are not
    clock questions, and "schedule daily at 08:00" must keep reaching the scheduler.
    """
    candidate = (text or "").strip().rstrip("?.!")
    if not candidate or len(candidate) > 90:
        return False
    lowered = candidate.lower()
    if any(word in lowered for word in (
        "schedule", "remind", "meeting", "deadline", "zone conversion", "timer",
        "timestamp", "time to ", "how long", "took", "spent", "latency",
    )):
        return False
    if _ASKS_THE_TIME.match(candidate):
        return True
    # "what time is it in tokyo", "time in IST"
    return bool(_TIME_WORD.search(lowered) and re.search(r"\bin\s+[a-z /]{2,30}$", lowered))


def _requested_zone(text: str) -> tuple[str | None, str | None]:
    """(IANA zone, the word the user used), or (None, None) for local time."""
    lowered = (text or "").strip().rstrip("?.!").lower()
    match = re.search(r"\b(?:in|for|at)\s+([a-z][a-z /_-]{1,30})$", lowered)
    candidate = match.group(1).strip() if match else ""
    if not candidate:
        # "EST time now", "UTC date"
        words = re.findall(r"[a-z_/]+", lowered)
        for word in words:
            if word in _ZONE_WORDS:
                return _ZONE_WORDS[word], word
        return None, None
    if candidate in _ZONE_WORDS:
        return _ZONE_WORDS[candidate], candidate
    # An IANA name typed directly, e.g. "Asia/Kolkata".
    if "/" in candidate:
        return candidate.replace(" ", "_").title().replace("_/_", "/"), candidate
    for word in candidate.split():
        if word in _ZONE_WORDS:
            return _ZONE_WORDS[word], word
    city = _iana_city(re.sub(r"\s+(?:right\s+now|now|today|currently)$", "", candidate))
    if city:
        return city, candidate
    return None, candidate or None


# Converting a time between zones, and the gap between two zones: "convert 9am pst to ist",
# "what's 3pm london time in new york", "what time is it in tokyo when it's 9am here", "how
# many hours ahead is tokyo". One right answer, which daylight saving makes easy to get
# wrong, so it is computed here from the zone database; these went to the chat model. The
# whole sentence must be one of these shapes, and both zones must be known, or it is left
# to the router.
_TIME_OF_DAY = (r"(?P<h>\d{1,2})(?::(?P<m>[0-5]\d))?\s*(?P<ap>a\.?\s?m\.?|p\.?\s?m\.?)?"
                r"|(?P<word>noon|midday|midnight)")
_ZONE_PHRASE = r"[a-z][a-z .'/_-]{0,30}?"
_HERE = {"here", "local", "local time", "my time", "my local time", "me", "mine", "where i am", "my end"}
_ZONE_ASKS = (
    re.compile(rf"(?:convert\s+|what(?:'s|s|\s+is)\s+|when\s+is\s+)?(?:{_TIME_OF_DAY})(?:\s+(?P<src>{_ZONE_PHRASE}))?"
               rf"\s+(?:to|in|into)\s+(?P<dst>{_ZONE_PHRASE})", re.IGNORECASE),
    re.compile(rf"what\s+time\s+(?:is\s+it|will\s+it\s+be)\s+in\s+(?P<dst>{_ZONE_PHRASE})\s+(?:when\s+it(?:'s|s|\s+is)|at)"
               rf"\s+(?:{_TIME_OF_DAY})(?:\s+(?:in\s+)?(?P<src>{_ZONE_PHRASE}))?", re.IGNORECASE),
)
_GAP_ASKS = (
    re.compile(rf"how\s+many\s+hours\s+(?:ahead|behind)\s+is\s+(?P<a>{_ZONE_PHRASE})"
               rf"(?:\s+(?:of|from|than)\s+(?P<b>{_ZONE_PHRASE}))?", re.IGNORECASE),
    re.compile(rf"how\s+far\s+(?:ahead|behind)\s+is\s+(?P<a>{_ZONE_PHRASE})"
               rf"(?:\s+(?:of|from|than)\s+(?P<b>{_ZONE_PHRASE}))?", re.IGNORECASE),
    re.compile(rf"what(?:'s|s|\s+is)\s+the\s+time\s+difference\s+(?:between\s+(?P<a>{_ZONE_PHRASE})"
               rf"\s+and\s+(?P<b>{_ZONE_PHRASE})|(?:with|to)\s+(?P<a2>{_ZONE_PHRASE}))", re.IGNORECASE),
)


def _zone_named(phrase: str | None) -> tuple[str | None, str] | None:
    """(IANA zone, as said), (None, as said) for the laptop's own zone, or None if unknown."""
    said = (phrase or "here").strip().lower()
    if said in _HERE:
        return None, said
    said = re.sub(r"\s+time$", "", said).strip()
    if said in _ZONE_WORDS:
        return _ZONE_WORDS[said], said
    if "/" in said:
        return said.replace(" ", "_").title().replace("_/_", "/"), said
    city = _iana_city(said)
    return (city, said) if city else None


def zone_question(text: str) -> dict[str, object] | None:
    """A conversion or a gap between zones, read from the whole sentence, else None."""
    candidate = re.sub(r"^\s*(?:(?:can|could|would)\s+you\s+(?:please\s+)?|please\s+)|,?\s+please$", "",
                       (text or "").strip().rstrip("?.!").strip(), flags=re.IGNORECASE)
    for pattern in _ZONE_ASKS:
        asked = pattern.fullmatch(candidate)
        if not asked:
            continue
        if asked.group("word"):
            hour, minute = (0 if asked.group("word").lower() == "midnight" else 12), 0
        else:
            hour, minute, half = int(asked.group("h")), int(asked.group("m") or 0), (asked.group("ap") or "").lower()
            if not half and asked.group("m") is None:
                return None                      # "9 pst" could be morning or night
            if half and not 1 <= hour <= 12 or hour > 23:
                return None
            if half:
                hour = hour % 12 + (12 if half.startswith("p") else 0)
        source, target = _zone_named(asked.group("src")), _zone_named(asked.group("dst"))
        if source is None or target is None or source[0] == target[0]:
            return None
        return {"kind": "convert", "hour": hour, "minute": minute, "source": source, "target": target}
    for pattern in _GAP_ASKS:
        asked = pattern.fullmatch(candidate)
        if not asked:
            continue
        groups = asked.groupdict()
        first, second = _zone_named(groups.get("a") or groups.get("a2")), _zone_named(groups.get("b"))
        if first is None or second is None or first[0] is None:
            return None
        return {"kind": "gap", "first": first, "second": second}
    return None


def _load_zone(name: str):
    from zoneinfo import ZoneInfo  # stdlib; the data may still be missing on Windows

    return ZoneInfo(name)


def _offset_text(moment: datetime) -> str:
    offset = moment.utcoffset() or timedelta(0)
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    hours, minutes = divmod(abs(total) // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


class ClockTool:
    """`time` / `date` / `time in <place>` — read from this machine, never the web."""

    def __init__(self, now=None) -> None:
        # Injectable so the tests are not a clock race.
        self._now = now or (lambda: datetime.now().astimezone())

    def now(self, request: str = "") -> ToolResult:
        local = self._now()
        between = zone_question(request)
        if between is not None:
            return self._between_zones(between, local)
        zone_name, asked_as = _requested_zone(request)

        if zone_name is None and asked_as:
            return ToolResult.failure(
                f"I do not know the time zone {asked_as!r}. Try a city, an offset like UTC, "
                f"or an IANA name such as Asia/Kolkata.",
                local=local.isoformat(),
            )

        if zone_name is None:
            return ToolResult.success(self._describe(local, "your computer"), **self._data(local, local))

        try:
            moment = local.astimezone(_load_zone(zone_name))
        except Exception as exc:
            return ToolResult.failure(
                f"I cannot look up {zone_name} on this machine ({type(exc).__name__}). "
                f"Time zone data needs: pip install tzdata. Locally it is "
                # %-I is glibc only and raises ValueError on Windows - which is exactly
                # where tzdata is missing, so the message explaining how to fix that
                # crashed instead of printing. lstrip('0') below already does the job,
                # the same way the comment on _describe says to.
                f"{local.strftime('%I:%M %p').lstrip('0') if hasattr(local, 'strftime') else ''}"
                f"{local.strftime(' %Z')}.".replace("  ", " "),
                local=local.isoformat(),
            )

        lines = [self._describe(moment, zone_name)]
        note = self._standard_time_note(asked_as, moment)
        if note:
            lines.append("")
            lines.append(note)
        if moment.utcoffset() != local.utcoffset():
            lines.append("")
            lines.append(f"Where you are it is **{self._clock(local)}** ({local.strftime('%Z')}).")
        return ToolResult.success(NL.join(lines), **self._data(moment, local, zone=zone_name))

    def _between_zones(self, asked: dict[str, object], local: datetime) -> ToolResult:
        """A time said in one zone, read in another; or how far apart two zones are now."""
        first, second = (asked["source"], asked["target"]) if asked["kind"] == "convert" else (asked["first"], asked["second"])
        try:
            zone_a, zone_b = ((_load_zone(name) if name else local.tzinfo) for name, _said in (first, second))
        except Exception as exc:
            return ToolResult.failure(f"I cannot look up that time zone on this machine ({type(exc).__name__}). "
                                      f"Time zone data needs: pip install tzdata.")
        label_a, label_b = (name or "your computer" for name, _said in (first, second))
        place_a, place_b = ("here" if name is None else f"in `{name}`" for name, _said in (first, second))
        if asked["kind"] == "convert":
            day = local.astimezone(zone_a).date()
            start = datetime(day.year, day.month, day.day, int(asked["hour"]), int(asked["minute"]), tzinfo=zone_a)
            end = start.astimezone(zone_b)
            shift = {1: " the next day", -1: " the day before"}.get((end.date() - start.date()).days, "")
            lines = [f"**{self._clock(start)} {start.strftime('%Z')}** {place_a} is "
                     f"**{self._clock(end)} {end.strftime('%Z')}**{shift} {place_b}."]
            notes = [self._standard_time_note(first[1], start), self._standard_time_note(second[1], end)]
            lines += ["", *(note for note in notes if note)] if any(notes) else []
            return ToolResult.success(NL.join(lines), source=start.isoformat(), target=end.isoformat(),
                                      source_zone=label_a, target_zone=label_b)
        now_a, now_b = local.astimezone(zone_a), local.astimezone(zone_b)
        minutes = int(((now_a.utcoffset() or timedelta(0)) - (now_b.utcoffset() or timedelta(0))).total_seconds() // 60)
        hours, spare = divmod(abs(minutes), 60)
        size = " ".join(part for part in (f"{hours} hour{'s' if hours != 1 else ''}" if hours else "",
                                          f"{spare} minutes" if spare else "") if part)
        relation = "the same time as" if minutes == 0 else f"**{size} {'ahead of' if minutes > 0 else 'behind'}**"
        other = "you" if second[0] is None else f"`{label_b}`"
        text = (f"`{label_a}` is {relation} {other} right now: it is {self._clock(now_a)} {now_a.strftime('%Z')} "
                f"there and {self._clock(now_b)} {now_b.strftime('%Z')} {place_b}.")
        return ToolResult.success(text, minutes=minutes, first_zone=label_a, second_zone=label_b)

    @staticmethod
    def _clock(moment: datetime) -> str:
        return moment.strftime("%I:%M %p").lstrip("0")

    def _describe(self, moment: datetime, where: str) -> str:
        # %-d is not portable to Windows, so strip a leading zero by hand.
        day = moment.strftime("%A, %d %B %Y").replace(" 0", " ")
        label = moment.strftime("%Z") or _offset_text(moment)
        return (
            # Backticks, not italics: an IANA name contains an underscore, so
            # _America/New_York_ rendered with its underscores showing.
            f"**{self._clock(moment)}** {label} — {day}"
            f"{NL}{NL}`{where}` · {_offset_text(moment)}"
        )

    @staticmethod
    def _standard_time_note(asked_as: str | None, moment: datetime) -> str:
        """Asking for EST in September should not get a silent five-hour error."""
        if not asked_as or asked_as.lower() not in _STANDARD_ONLY:
            return ""
        in_effect = moment.strftime("%Z")
        if not in_effect or in_effect.lower() == asked_as.lower():
            return ""
        return (
            f"You asked for {asked_as.upper()}, but that zone is on **{in_effect}** right now "
            f"({_offset_text(moment)}), so this is the correct local time there."
        )

    @staticmethod
    def _data(moment: datetime, local: datetime, zone: str | None = None) -> dict[str, object]:
        return {
            "iso": moment.isoformat(),
            "local_iso": local.isoformat(),
            "zone": zone or (local.strftime("%Z") or "local"),
            "offset": _offset_text(moment),
            "unix": int(moment.timestamp()),
            "day_of_week": moment.strftime("%A"),
        }


NL = chr(10)


def prompt_stamp(now=None) -> str:
    """One line naming the current moment, for every model-facing prompt.

    The model cannot read a clock, so without this it either guesses or - as observed -
    searches the web and believes a stale page over the machine it is running on.
    """
    moment = (now or (lambda: datetime.now().astimezone()))()
    return (
        f"The current date and time on this computer is "
        f"{moment.strftime('%A, %d %B %Y at %I:%M %p').replace(' 0', ' ')} "
        f"{moment.strftime('%Z') or _offset_text(moment)} ({_offset_text(moment)}). "
        f"Use this rather than guessing, and never search the web for the current time."
    )
