"""Reminders, timers, alarms and repeats.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from laptop_agent.timeparse import (
    TimeParseError,
    _find_day,
    _find_time,
    describe,
    on_laptop_clock,
    parse_when,
    spoken_to_digits,
)
from laptop_agent.safety import ApprovalRequest, RiskLevel
from laptop_agent.scheduler import ScheduleError, parse_days, parse_schedule
from laptop_agent.tools.base import ToolResult
from laptop_agent.failures import record_failure
from laptop_agent.tools.dates import resolve as resolve_date
from laptop_agent.agents.common import (
    _DAY_NAMES,
    _MAX_TIMER_SECONDS,
    _NOT_A_TIMER_NAME,
    _REPEAT,
    _TIMER_PART,
    _UNIT_WORDS,
    _WEEKDAY_NAMES,
    _reminder_line,
    _reminder_message,
    _reminder_name,
    _say_seconds,
    _span_seconds,
    _spoken_request,
)


class Reminders:
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
        cleaned = re.sub(
            r"\bin\s+(\d{1,3})(?=\s*[.!]*$|\s+(?:to|about|that)\b)", r"in \1 minutes", cleaned, flags=re.IGNORECASE
        )
        repeat = _REPEAT.search(cleaned)
        if repeat:
            return self._repeating_reminder(cleaned, repeat)
        return self._set_reminder(cleaned)

    def _set_reminder(
        self, cleaned: str, default_half: str = "", label: str = "", what: str = "reminder"
    ) -> ToolResult:
        now = datetime.now().astimezone()
        try:
            when = parse_when(cleaned, now, default_half=default_half, local=True)
        except TimeParseError as exc:
            return ToolResult.failure(str(exc))
        if when is None:
            if what == "alarm":
                return ToolResult.failure('When should the alarm go off? Try "wake me up at 7".')
            if re.search(r"\byesterday\b|\blast\s+(?:night|week|month)\b", cleaned, re.IGNORECASE):
                return ToolResult.failure("That's already in the past. When should I remind you?")
            if re.search(r"\bin\s+\d+\s+(?:months?|years?)\b", cleaned, re.IGNORECASE):
                return ToolResult.failure(
                    "I set reminders minutes, hours, days or weeks ahead, or on a "
                    'date — for example "on 2027-03-01 at 9".'
                )
            return ToolResult.failure(
                'I could not find a time in that. Try "remind me to call mom at 6pm", '
                '"tomorrow at 9", "in 20 minutes" or a date like 2026-12-25 07:30.'
            )
        message = label or _reminder_message(cleaned, when.start, when.end)
        if not message:
            return ToolResult.failure(f"What should I remind you about {describe(when.at, now, local=True)}?")
        try:
            outcome = self.context.reminders.add(when.at.isoformat(), message)
        except ValueError:
            return ToolResult.failure("Reminder date/time must look like YYYY-MM-DD HH:MM.")
        if not outcome.get("ok"):
            return ToolResult.failure(f"Could not add reminder: {outcome.get('reason', 'unknown error')}")
        reminder = outcome["reminder"]
        spoken = describe(when.at, now, local=True)
        # A time already gone is kept, not refused - an explicit past date is a legitimate
        # backfill - but it is never left to look like it was scheduled ahead.
        note = "" if when.at > now else " (that time has already passed, so it is due now)"
        if what == "timer":
            # "Pasta timer (10 minutes)" -> "Pasta timer set for 10 minutes."
            named = re.fullmatch(r"(?P<name>.+?) \((?P<amount>.+)\)", message)
            text = (
                f"{named.group('name')} set for {named.group('amount')}. It goes off {spoken}."
                if named
                else f"Timer set. It goes off {spoken}."
            )
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
        extend = re.match(r"\s*(?:add|extend(?:\s+(?:it|by))?)\s+(?P<rest>.+)$", cleaned, re.IGNORECASE)
        if extend:
            return self._timer_extend(extend.group("rest"))
        # "an hour and a half", "2 and a half minutes"
        cleaned = re.sub(
            rf"\b(\d+|an?)\s+(?:and\s+a\s+half\s+({_UNIT_WORDS})|({_UNIT_WORDS})\s+and\s+a\s+half)\b",
            lambda m: f"{'1' if m.group(1).lower() in {'a', 'an'} else m.group(1)}.5 {m.group(2) or m.group(3)}",
            cleaned,
            flags=re.IGNORECASE,
        )
        parts = list(_TIMER_PART.finditer(cleaned))
        if not parts:
            return ToolResult.failure('How long should the timer run? Say something like "set a timer for 10 minutes".')
        # "1 hour and 30 minutes": the spans that run on from the first one.
        seconds, end = _span_seconds(parts[0]), parts[0].end()
        for part in parts[1:]:
            if not re.fullmatch(r"\s*(?:,|and)?\s*", cleaned[end : part.start()], re.IGNORECASE):
                break
            seconds, end = seconds + _span_seconds(part), part.end()
        if seconds < 1:
            return ToolResult.failure("A timer needs at least a second.")
        if seconds > _MAX_TIMER_SECONDS:
            return ToolResult.failure(
                "That's longer than a timer should run. Set a reminder for the day instead, "
                'for example "remind me on 2027-03-01 to renew the lease".'
            )
        before, after = cleaned[: parts[0].start()], cleaned[end:]
        amount = _say_seconds(seconds)
        # "set a timer for 20 minutes called laundry" was set unnamed, so "the laundry timer" found nothing.
        named = (
            re.match(
                r"\s*(?:timer\s+)?(?:called|named|labell?ed)\s+(?:the\s+|my\s+|a\s+)?"
                r"(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,2})(?:\s+timer)?(?:\s+please)?\s*[.!]*$",
                after,
                re.I,
            )
            or re.search(r"\b(?:a|an|my|the)\s+(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*)?)\s+timer\b", before, re.I)
            or re.match(r"\s*(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*)?)\s+timer\b", after, re.I)
            or re.match(
                r"\s*(?:timer\s+)?for\s+(?:the\s+|my\s+|a\s+)?(?P<name>[a-z][\w'-]*(?:\s+[a-z][\w'-]*){0,2})"
                r"(?:\s+please)?\s*[.!]*$",
                after,
                re.I,
            )
        )
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
        """ "what's my next reminder", "when is my alarm". "when is my next reminder" was
        read as a date the user had never told us and answered "I don't know"."""
        now = datetime.now().astimezone()
        noun = kind or "reminder"
        upcoming = [(due, item) for due, item in self._dated(kind) if due > now]
        repeating = [
            job for job in self._repeating_reminders() if not kind or re.search(rf"\b{kind}\b", job.spec, re.IGNORECASE)
        ]
        parts = []
        if upcoming:
            due, item = upcoming[0]
            what = "" if str(item["message"]).lower() == kind else f": {item['message']}"
            parts.append(f"Your next {noun} is {describe(due, now, local=True)}{what}.")
        if repeating:
            parts.append(
                "Repeating: "
                + "; ".join(f"{job.schedule.describe()} — {job.spec[len('reminder add now ') :]}" for job in repeating)
                + "."
            )
        if not parts:
            return ToolResult.success(f"You have no {noun}s coming up.")
        return ToolResult.success(" ".join(parts), reminder=upcoming[0][1] if upcoming else None)

    def _running_timers(self) -> list[tuple[datetime, dict]]:
        # A timer is saved as "<Name> timer (<length>)", "Timer to … (<length>)" or "Timer (<length>)";
        # "fix the egg timer" is a reminder that names one (Codex's review of #257).
        return [
            (due, item)
            for due, item in self._dated("timer")
            if re.fullmatch(
                r"(?:[a-z][\w' -]*\s)?timer(?:\s+to\s.+)?\s\(.+\)", str(item.get("message", "")), re.IGNORECASE
            )
        ]

    def _timer_extend(self, raw: str) -> ToolResult:
        """`timer add <length> [<name>]` - more time on a running timer: "add 5 minutes to the laundry
        timer" reached the chat model."""
        cleaned = _spoken_request(raw)
        parts = list(_TIMER_PART.finditer(cleaned))
        if not parts:
            return ToolResult.failure('How much time should I add? Say something like "add 5 minutes to the timer".')
        seconds = sum(_span_seconds(part) for part in parts)
        name = re.sub(
            r"^(?:\s*(?:more|to|on|the|my|and)\b)+|\btimer\b", " ", cleaned[parts[-1].end() :], flags=re.IGNORECASE
        )
        name = " ".join(name.split()).lower()
        now = datetime.now().astimezone()
        # A timer going off counts too: "add 5 minutes" said while it rings is 5 minutes from now.
        running = self._running_timers()
        if name:
            running = [
                (due, item)
                for due, item in running
                if str(item.get("message", "")).lower().startswith(f"{name} timer ")
            ]
        if not running:
            return ToolResult.failure(f"No {name + ' ' if name else ''}timer is running.")
        if len(running) > 1:
            return ToolResult.failure(
                "Which one? " + "; ".join(re.sub(r"\s*\(.*\)$", "", str(item["message"])) for _due, item in running)
            )
        due, item = running[0]
        later = max(due, now) + timedelta(seconds=seconds)
        self.context.reminders.move(int(item["id"]), later)
        label = re.sub(r"\s*\(.*\)$", "", str(item["message"]))
        return ToolResult.success(
            f"Added {_say_seconds(seconds)}: {label} now goes off {describe(later, now, local=True)}.",
            id=item["id"],
            due_local=later.isoformat(),
        )

    def _timers(self, name: str = "") -> ToolResult:
        """Running timers and what is left on each - "how much time is left on my timer", or on the
        one named: "is my egg timer still going"."""
        now = datetime.now().astimezone()
        running = self._running_timers()
        if name:
            named = [
                (due, item)
                for due, item in running
                if str(item.get("message", "")).lower().startswith(f"{name.lower()} timer ")
            ]
            if not named:
                others = self._timers() if running else None
                return ToolResult.success(
                    f"No {name} timer is running." + (f" {others.message}" if others is not None else ""), timers=[]
                )
            running = named
        if not running:
            return ToolResult.success('No timer is running. Say "set a timer for 10 minutes" to start one.', timers=[])
        lines = []
        for due, item in sorted(running, key=lambda pair: pair[0]):
            name = re.sub(r"\s*\(.*\)$", "", str(item["message"]))
            left = (due - now).total_seconds()
            status = "going off now" if left <= 0 else f"**{_say_seconds(round(left))}** left"
            lines.append(
                f"{name}: {status} (at {on_laptop_clock(due, now.tzinfo).strftime('%I:%M:%S %p').lstrip('0')})"
            )
        return ToolResult.success(
            "\n".join(lines) if len(lines) > 1 else lines[0], timers=[item for _due, item in running]
        )

    def _alarms(self) -> ToolResult:
        """Every alarm set - "what alarms do i have". Only the next one could be asked for, so
        the question reached the chat model, which cannot see them."""
        now = datetime.now().astimezone()
        # An alarm is labelled exactly that; "fix the fire alarm" is a reminder that names one.
        upcoming = [
            (due, item)
            for due, item in self._dated("alarm")
            if due > now and str(item.get("message", "")).strip().lower() == "alarm"
        ]
        repeating = [
            job
            for job in self._repeating_reminders()
            if job.spec[len("reminder add now ") :].strip().lower() == "alarm"
        ]
        if not upcoming and not repeating:
            return ToolResult.success(
                'You have no alarms set. Say "wake me up at 7" to set one.', alarms=[], repeating=[]
            )
        lines = [f"- #{item['id']} {describe(due, now, local=True)}" for due, item in upcoming]
        lines += [f"- every: {job.schedule.describe()}" + ("" if job.enabled else " (off)") for job in repeating]
        count = len(lines)
        return ToolResult.success(
            f"You have {count} alarm{'s' if count != 1 else ''}:\n" + "\n".join(lines),
            alarms=[item for _due, item in upcoming],
            repeating=[job.to_dict() for job in repeating],
        )

    def _alarm(self, expression: str) -> ToolResult:
        """`alarm 7` / `alarm at 6:30am tomorrow`. A bare hour is in the morning."""
        cleaned = re.sub(r"^(?:for|to|at)\s+", "at ", _spoken_request(expression), flags=re.IGNORECASE)
        if re.fullmatch(r"\d{1,2}(?::\d{2})?(?:\s*(?:am|pm|a\.m\.|p\.m\.))?", cleaned, re.IGNORECASE):
            cleaned = "at " + cleaned
        # "every weekday at 7" was set once, for tomorrow, and the repeat was dropped unsaid.
        repeat = _REPEAT.search(cleaned)
        if repeat:
            return self._repeating_reminder(cleaned, repeat, label="Alarm", default_half="am")
        # "wake me up tomorrow" set an alarm for 9:00, a time nobody said: a day with no time of
        # day, and no "in 20 minutes", is asked about, and the answer comes back to that day.
        if (
            cleaned.strip()
            and _find_time(cleaned.lower()) is None
            and not re.search(r"\bin\s+(?:an?|half|\d)", cleaned, re.IGNORECASE)
        ):
            try:
                day = parse_when(cleaned, datetime.now().astimezone(), local=True)
            except TimeParseError:
                day = None
            if day is not None:
                said = re.sub(r"^(?:at|for|to)\s+", "", cleaned.strip(), flags=re.IGNORECASE)
                said = f"on {said}" if re.fullmatch(r"(?:mon|tues|wednes|thurs|fri|satur|sun)day", said, re.I) else said
                return ToolResult.failure(f'When should the alarm go off {said}? Try "at 7".')
        return self._set_reminder(cleaned, default_half="am", label="Alarm", what="alarm")

    def _repeating_reminder(
        self, cleaned: str, repeat: re.Match[str], label: str = "", default_half: str = ""
    ) -> ToolResult:
        """ "remind me every day at 8am to take my vitamins", on the scheduler.

        It used to become ONE reminder at 8am today reading "every day to take my vitamins".
        Weekdays, weekends and named days repeat too ("every monday", "on weekdays"); only
        "every week" and "every month" without a day are set once, and say so.
        """
        rule = repeat.group(0).lower()
        # "every monday" keeps its "monday" in what is parsed: dropped with the rest of the
        # rule, the one-off fallback below landed on today instead of the coming Monday.
        weekday = re.search(r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", rule)
        body = (
            cleaned[: repeat.start()] + " " + (weekday.group(1) if weekday else "") + " " + cleaned[repeat.end() :]
        ).strip()
        now = datetime.now().astimezone()
        try:
            when = parse_when(body, now, default_half=default_half, local=True)
        except TimeParseError as exc:
            return ToolResult.failure(str(exc))
        message = label or (_reminder_message(body, when.start, when.end) if when else _reminder_message(body, 0, 0))
        if not message:
            return ToolResult.failure("What should I remind you about?")
        days = (
            parse_days(re.sub(r"^(?:every|on)\s+", "", rule))
            if re.search(r"week(?:day|end)|" + _DAY_NAMES, rule)
            else ()
        )
        interval = re.match(r"every\s+(\d+)\s+(minutes?|hours?)", rule)
        if interval or rule in {"every hour", "hourly"}:
            schedule = f"every {interval.group(1)} {interval.group(2)}" if interval else "every hour"
        elif days or re.search(r"\b(?:day|daily|morning|evening|night|everyday)\b", rule):
            if when is not None:
                clock = on_laptop_clock(when.at, now.tzinfo)
            else:
                hour = 18 if "evening" in rule else 21 if "night" in rule else 9
                clock = now.replace(hour=hour, minute=0)
            on = (
                "weekdays"
                if days == (0, 1, 2, 3, 4)
                else "weekends"
                if days == (5, 6)
                else " and ".join(f"{_WEEKDAY_NAMES[day]}s" for day in days)
                if days
                else "daily"
            )
            schedule = f"{on} at {clock.hour:02d}:{clock.minute:02d}"
        else:
            if when:
                once = self._set_reminder(
                    body, default_half=default_half, label=label, what="alarm" if label == "Alarm" else "reminder"
                )
            else:
                once = ToolResult.failure("When should I remind you?")
            if once.ok:
                once.message = (
                    once.message.rstrip(".") + ". I can repeat daily, on weekdays or on given "
                    f"days, or every few hours, but not {rule} yet, so this one is set once."
                )
            return once
        spec = f"reminder add now {message}"
        try:
            wanted = parse_schedule(schedule).to_dict()
        except ScheduleError as exc:
            return ToolResult.failure(str(exc))
        # Said twice, it rang twice: the same request makes the same job once.
        for existing in self._repeating_reminders():
            if existing.spec == spec and existing.schedule.to_dict() == wanted:
                return ToolResult.success(
                    f"That's already set — {existing.schedule.describe()}: {message}.", job=existing.to_dict()
                )
        try:
            job = self.context.scheduler.add("command", spec, schedule, now)
        except ScheduleError as exc:
            return ToolResult.failure(str(exc))
        if label == "Alarm":
            return ToolResult.success(
                f'Alarm set — {job.schedule.describe()}. Say "cancel the alarm" to stop it.', job=job.to_dict()
            )
        return ToolResult.success(
            f"Repeating reminder set — {job.schedule.describe()}: {message}. "
            f'Say "cancel the {_reminder_name(message)} reminder" to stop it.',
            job=job.to_dict(),
        )

    def _repeating_reminders(self) -> list:
        return [
            job
            for job in self.context.scheduler.list_jobs()
            if job.kind == "command" and job.spec.lower().startswith("reminder add now ")
        ]

    def _reminders_list(self, day_text: str = "") -> ToolResult:
        now = datetime.now().astimezone()
        reminders = self.context.reminders.list()
        if day_text:
            resolved = resolve_date(day_text, now)
            if resolved is None:
                return ToolResult.failure(f"I could not read the day in {day_text!r}.")
            selected = []
            for item in reminders:
                try:
                    due = datetime.fromisoformat(str(item["due_at"]))
                    if on_laptop_clock(due, now.tzinfo).date() == resolved[0]:
                        selected.append(item)
                except (KeyError, ValueError) as exc:
                    record_failure("orchestrator.reminders_list", exc)
            repeating = [
                job
                for job in self._repeating_reminders()
                if job.enabled
                and (
                    job.schedule.kind != "daily" or not job.schedule.days or resolved[0].weekday() in job.schedule.days
                )
            ]
            if not selected and not repeating:
                return ToolResult.success(f"You have no reminders for {day_text}.", reminders=[], repeating=[])
            lines = [_reminder_line(item, now) for item in selected[:20]]
            if len(selected) > 20:
                lines.append(f"- … and {len(selected) - 20} more after these")
            lines += [
                f"- every: {job.schedule.describe()} — {job.spec[len('reminder add now ') :]}" for job in repeating
            ]
            count = len(selected) + len(repeating)
            return ToolResult.success(
                f"You have {count} reminder{'s' if count != 1 else ''} for {day_text}:\n" + "\n".join(lines),
                reminders=selected,
                repeating=[job.to_dict() for job in repeating],
            )
        repeating = self._repeating_reminders()
        if not reminders and not repeating:
            return ToolResult.success("You have no reminders set.", reminders=[])
        lines = [_reminder_line(item, now) for item in reminders[:20]]
        if len(reminders) > 20:
            # "You have 35 reminders" above twenty lines read as if the rest did not exist.
            lines.append(f"- … and {len(reminders) - 20} more after these")
        lines += [f"- every: {job.schedule.describe()} — {job.spec[len('reminder add now ') :]}" for job in repeating]
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
                return None, "Which one? " + " ".join(
                    f"#{item['id']} {item['message']};" for item in active[:8]
                ).rstrip(";")
            return max(active, key=lambda item: int(item.get("id", 0))), ""
        words = [
            word for word in re.findall(r"[a-z0-9']+", wanted) if word not in {"the", "my", "a", "to", "about", "for"}
        ]
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

    def _reminder_move(self, raw: str) -> ToolResult:
        """`reminder move <which> to <when>`. A time said without a day stays on the reminder's own
        day: "move my dentist reminder to 4pm" means Friday's at 4, not today's."""
        parts = re.match(r"(?P<which>.+)\s+(?:to|for|until|till)\s+(?P<when>.+)$", raw, re.IGNORECASE)
        if not parts:
            return ToolResult.failure('Say which reminder and when, e.g. "move my dentist reminder to 4pm".')
        reminder, why = self._find_reminder(parts.group("which"))
        if reminder is None:
            repeating = [
                job
                for job in self._repeating_reminders()
                if job.spec[len("reminder add now ") :].strip().lower() == "alarm"
            ]
            if re.search(r"\balarm\b", parts.group("which"), re.IGNORECASE) and repeating:
                # A repeating alarm is a scheduled job, not a reminder: said so, rather than "no reminder about 'alarm'".
                return ToolResult.failure(
                    f"Your alarm repeats ({repeating[0].schedule.describe()}), and I can't move "
                    'a repeating alarm yet. Say "cancel the alarm" and set it again for the new time.'
                )
            return ToolResult.failure(why)
        now = datetime.now().astimezone()
        said = spoken_to_digits(parts.group("when").strip())
        # "push my bank reminder to 11": a bare clock number, as the alarm's follow-up reads one.
        if re.fullmatch(r"\d{1,2}(?::\d{2})?(?:\s*[ap]\.?m\.?)?", said, re.IGNORECASE):
            said = f"at {said}"
        due = datetime.fromisoformat(str(reminder["due_at"])).astimezone()
        # A clock time with no day is read from the start of the reminder's own day; a length ("in an
        # hour") is no clock time, so it still counts from now.
        base = now
        if _find_time(said.lower()) is not None and _find_day(said.lower(), now) is None and due.date() > now.date():
            base = due.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            when = parse_when(said, base, local=True)
        except TimeParseError as exc:
            return ToolResult.failure(str(exc))
        if when is None:
            return ToolResult.failure(f'I could not find a time in "{parts.group("when").strip()}".')
        self.context.reminders.move(int(reminder["id"]), when.at)
        return ToolResult.success(
            f"Moved: {reminder['message']} — now {describe(when.at, now, local=True)}.",
            id=reminder["id"],
            due_local=when.at.isoformat(),
        )

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
            spoken = job.spec[len("reminder add now ") :].lower()
            if (
                words
                and not words.isdigit()
                and all(
                    w in spoken
                    for w in re.findall(r"[a-z0-9']+", words)
                    if w not in {"the", "my", "a", "to", "about", "for", "reminder"}
                )
            ):
                self.context.scheduler.remove(job.id)
                return ToolResult.success(f"Stopped the repeating reminder: {job.spec[len('reminder add now ') :]}.")
        reminder, why = self._find_reminder(raw)
        if reminder is None:
            return ToolResult.failure(why)
        self.context.reminders.remove(int(reminder["id"]))
        return ToolResult.success(f"Cancelled: {reminder['message']}.", id=reminder["id"], removed=True)

    def _ringing(self, words: str) -> dict | None:
        """The reminder going off now (or overdue) that these words name, if any."""
        now = datetime.now().astimezone()
        wanted = [
            word
            for word in re.findall(r"[a-z0-9']+", words.lower())
            if word not in {"the", "my", "a", "to", "about", "for", "reminder", "this", "that", "it", "last"}
        ]
        for due, item in self._dated():
            if due > now:
                break
            if all(word in str(item.get("message", "")).lower() for word in wanted):
                return item
        return None

    def _reminder_stop(self, raw: str) -> ToolResult:
        """ "stop the alarm": the one ringing, or a running timer. Anything else is only
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
        wanted = [
            word
            for word in re.findall(r"[a-z0-9']+", raw.lower())
            if word not in {"the", "my", "a", "to", "about", "for", "reminder", "this", "that", "it"}
        ]
        jobs = [
            job for job in self._repeating_reminders() if wanted and all(word in job.spec.lower() for word in wanted)
        ]
        set_up = []
        if reminder is not None:
            due = datetime.fromisoformat(str(reminder["due_at"]))
            set_up.append(f"{reminder['message']} is set for {describe(due, now, local=True)}")
        set_up += [f"{job.spec[len('reminder add now ') :]} repeats {job.schedule.describe()}" for job in jobs]
        if not set_up:
            return ToolResult.failure(why or "Nothing is going off right now.")
        name = raw.strip() or "reminder"
        return ToolResult.success(
            f'Nothing is going off right now. {"; ".join(set_up)}. Say "cancel the {name}" if you want it removed.'
        )

    def _remove_all(self, kind: str) -> ToolResult:
        """ "cancel all my reminders" - asks first when it would remove more than one, since
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
            preview += [
                f"- every: {job.schedule.describe()} — {job.spec[len('reminder add now ') :]}" for job in repeating
            ]
            self.context.web.approval_gate.require(
                ApprovalRequest(
                    action=f"Cancel all {count} {kind}",
                    risk=RiskLevel.HIGH,
                    everyday=True,
                    reason="Removes every one of them at once; they cannot be brought back.",
                    preview="\n".join(preview),
                )
            )
        for item in chosen:
            self.context.reminders.remove(int(item["id"]))
        for job in repeating:
            self.context.scheduler.remove(job.id)
        if count == 1:
            only = chosen[0]["message"] if chosen else repeating[0].spec[len("reminder add now ") :]
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
                return ToolResult.failure('Nothing is going off right now. Say which one, e.g. "snooze reminder 3".')
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
        return ToolResult.success(
            f"Snoozed until {describe(until, now, local=True)}: {reminder['message']}.",
            id=reminder["id"],
            due_local=until.isoformat(),
        )
