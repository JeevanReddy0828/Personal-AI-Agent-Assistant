"""Everyday answers worked out here: dates, lists, facts, calendar and status.

One part of `AgentOrchestrator` (agents/orchestrator.py), split out by area; it reads the
rest of the orchestrator through `self`."""
from __future__ import annotations

import difflib
import re
from datetime import datetime, timedelta
from pathlib import Path
from laptop_agent.memory import list_name
from laptop_agent.metrics import battery_status, system_metrics
from laptop_agent.planner.heuristic import fact_question, nameless_list_edit
from laptop_agent.access import developer_only, everyday_form, is_personal, refused_command
from laptop_agent.timeparse import describe
from laptop_agent.tools.base import ToolResult
from laptop_agent.failures import record_failure
from laptop_agent.tools import keys as keys_tools
from laptop_agent.tools.units import UnitTool, looks_like_conversion
from laptop_agent.tools.chance import draw
from laptop_agent.tools.dates import (
    RELATIVE_DAYS,
    date_question,
    describe_day,
    resolve as resolve_date,
    says_a_year,
)
from laptop_agent.tools.dates import calendar_fact, span_text, until_moment
from laptop_agent.agents.common import (
    _BARE_LIST,
    _HOW_LONG_LEFT,
    _PHRASE_WORD,
    _REMINDER_REMOVE,
    _WHICH_LIST,
    _reminder_line,
)


class Everyday:

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
        if kind == "fact":
            answer = calendar_fact(what, today)
            return ToolResult.success(answer) if answer else None
        profile = self.context.memory.get_profile()
        if kind == "between":
            first, second = resolve_date(what, now, profile), resolve_date(other, now, profile)
            if first is None or second is None:
                return None
            # "between march 1 and today" (and "since christmas") is the span up to today, so a
            # date said without a year is the last time it came round: this year's once it has
            # passed, else last year's. It counted forward instead - 145 days to next March for
            # an answer of 220, and 79 days to this December "since christmas". "between today
            # and march 1" looks ahead and keeps the next one.
            if second[0] == today and first[0] > today and not says_a_year(what, today):
                for year in ("this year", "last year"):
                    earlier = resolve_date(f"{what} {year}", now, profile)
                    if earlier is not None and earlier[0] <= today:
                        first = earlier
                        break
                # A weekday has no year to go back to: "since friday" on a Saturday is yesterday,
                # and it answered 6 days, to the coming Friday (Codex's review of #244).
                if first[0] > today and re.fullmatch(r"\s*(?:last\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day\s*",
                                                     what, re.IGNORECASE):
                    while first[0] > today:
                        first = (first[0] - timedelta(days=7), first[1])
            days = abs((second[0] - first[0]).days)
            return ToolResult.success(
                f"**{days} day{'s' if days != 1 else ''}** between {first[1]} ({first[0]:%A %d %B %Y}) and {second[1]} "
                f"({second[0]:%A %d %B %Y}).".replace(" 0", " "), days=days)
        upcoming = re.fullmatch(r"\s*(?:my\s+|the\s+)?(?:next\s+)?(reminder|alarm|timer)s?"
                                r"(?:\s+(?:goes|go)\s+off|\s+rings?|\s+(?:is|are)\s+(?:up|done)|\s+ends?)?\s*", what, re.IGNORECASE)
        if kind != "between" and upcoming:
            # "when is my next reminder" is not a date anyone told us.
            return self._next_reminder("" if upcoming.group(1).lower() == "reminder" else upcoming.group(1).lower())
        moment = until_moment(what, other, now, profile) if kind == "until" else None
        if moment is not None:
            minutes = round((moment - now).total_seconds() / 60)
            when, name = describe(moment, now, local=True), what.strip()
            if minutes < 0:
                return ToolResult.success(f"{name[:1].upper() + name[1:]} was **{span_text(-minutes, other)} ago** "
                                          f"({when}).", minutes=minutes, at=moment.isoformat())
            return ToolResult.success(f"**{span_text(minutes, other)}** until {name} ({when}).",
                                      minutes=minutes, at=moment.isoformat())
        found = resolve_date(what, now, profile)
        if found is None:
            # "when is my dentist appointment" - a reminder may say.
            words = [w for w in re.findall(r"[a-z0-9']+", what.lower()) if w not in {"my", "our", "the", "a", "an"}]
            reminder = next((item for item in self.context.reminders.list()
                             if words and all(w in str(item.get("message", "")).lower() for w in words)), None)
            if reminder is not None:
                due = datetime.fromisoformat(str(reminder["due_at"]))
                return ToolResult.success(f"You have a reminder for that: {reminder['message']} — "
                                          f"{describe(due, now, local=True)}.", reminder=reminder)
            if re.match(r"\s*(?:my|our)\s+", what, re.IGNORECASE):
                yours = re.sub(r"^(?:my|our)\b", "your", what.strip(), flags=re.IGNORECASE)
                return ToolResult.success(
                    f"I don't know when {yours} is — you haven't told me, and no reminder mentions it. "
                    f"Tell me with \"remember {what.strip()} is <date>\".")
            return None
        day, name = found
        if kind == "until":
            days = (day - today).days
            if days < 0:    # a stated year can be behind us: "until july 4th this year" said "-95 days"
                return ToolResult.success(f"{name[:1].upper() + name[1:]} was **{-days} day{'s' if days != -1 else ''} "
                                          f"ago** ({day:%A %d %B %Y}).".replace(" 0", " "), days=days,
                                          date=day.isoformat())
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
        verb = "was" if day < today else "is"
        return ToolResult.success(f"{name[:1].upper() + name[1:]} {verb} on {describe_day(day, today)}.",
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
        """`list <name> show|add <items>|remove <item>|clear|has <item>`, or `list <existing name>`.

        None when the words are not a list command: "list" is an ordinary English verb, and
        "list files in downloads" or "list the planets" must keep reaching the router.
        """
        match = re.match(r"(?P<name>.+?)\s+(?P<verb>show|add|remove|clear|has)\b\s*(?P<items>.*)$", rest, re.IGNORECASE)
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
        if verb == "has":
            # "is milk on my shopping list" reached the chat model, which cannot see the list.
            wanted = " ".join(raw_items.strip(" ?.!").lower().split())
            if name not in self.context.memory.lists():
                return ToolResult.success(f"You don't have a {list_name(name)} list.", items=[])
            # An item, not a substring: "almond milk" is not "milk" (Codex's review). A plural is.
            found = [item for item in self.context.memory.list_items(name)
                     if wanted and " ".join(item.lower().split()) in {wanted, wanted + "s", wanted.removesuffix("s")}]
            if found:
                return ToolResult.success(f"Yes — {label} has {', '.join(found)}.", items=found)
            return ToolResult.success(f"No — {label} doesn't have {raw_items.strip(' ?.!')}.", items=[])
        if not raw_items:
            return ToolResult.failure(f"What should I {verb} {'to' if verb == 'add' else 'from'} {label}?")
        if verb == "remove":
            removed_item = self.context.memory.remove_from_list(name, raw_items)
            if removed_item is None:
                return ToolResult.failure(f"{raw_items} isn't on {label}.")
            return ToolResult.success(f"Removed {removed_item} from {label}.", removed=removed_item)
        items = [item.strip(" .") for item in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", raw_items) if item.strip(" .")]
        # Said without commas, "eggs milk and bread" may be three things or "eggs milk" one, and nothing
        # tells it from "almond milk and bread": ask back (Jeevan, 2026-10-22). A quantity or a joining
        # word ("2 apples", "a loaf of bread") marks a phrase, so that is not asked about.
        if "," not in raw_items and any(len(item.split()) > 1 and not _PHRASE_WORD.search(item) for item in items[:-1]):
            split = [word for item in items[:-1] for word in item.split()] + items[-1:]
            return ToolResult.success(
                f"Add {', '.join(split[:-1])} and {split[-1]} to {label} as {len(split)} items? Say yes, no to "
                f'add "{raw_items}" as you said it, or say them again with "and" between.',
                items=self.context.memory.list_items(name))
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

    def _keys(self) -> keys_tools.KeysTool:
        if self._keys_cache is None:
            self._keys_cache = keys_tools.KeysTool(self.context.web.approval_gate, keys_tools.keys_backend())
        return self._keys_cache

    def _system_status(self) -> ToolResult:
        """Battery, CPU, memory and disk, read from this machine rather than guessed."""
        import shutil

        metrics = system_metrics(force=True)
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
            name = gpu.get("name") or "GPU"
            label = name if name.startswith("GPU") else f"GPU · {name}"
            if gpu.get("util_kind") == "3D":
                label += " (3D)"
            usage = gpu.get("util_percent")
            lines.append(f"- {label}: {str(usage) + '%' if usage is not None else 'n/a'}")
        if battery is None:
            lines.append("- Battery: none reported (a desktop, or the reading is unavailable)")
        return ToolResult.success("**This computer right now**\n" + "\n".join(lines), battery=battery, **metrics)

    def _account_limits(self, command: str, lowered: str, planner: bool) -> tuple[ToolResult | None, bool]:
        """(a refusal, whether to dispatch) for the command about to run.

        Checked there, not at the top of `_handle`: after `_follow_up` has rebuilt the command
        from history the client sent, and never on prose, which goes to the router. What the
        router or a split sentence produces comes back through here as a command of its own.
        A developer form is refused with a reason. Past that it is default-deny where a command
        is claimed: for a personal account only what is marked everyday is dispatched. Free
        text that is not goes to the router; a command the router made that is not is refused,
        including one nobody has classified yet.
        """
        refused = refused_command(command)
        if refused:
            return ToolResult.failure(f"`{refused}` {developer_only()}.", refused=refused), False
        if not is_personal() or self._everyday(command, lowered):
            return None, True
        if planner:
            return None, False
        return ToolResult.failure("That isn't available to a personal account.",
                                  refused=lowered.split(" ", 1)[0]), False

    def _everyday(self, command: str, lowered: str) -> bool:
        """Whether a personal account may have `command` dispatched: a form marked everyday,
        or one of the everyday branches chosen by a pattern, which `test_access` counts."""
        return bool(
            everyday_form(lowered) or _BARE_LIST.fullmatch(lowered) or _WHICH_LIST.fullmatch(lowered)
            or _HOW_LONG_LEFT.fullmatch(lowered) or _REMINDER_REMOVE.match(lowered)
            or fact_question(command) is not None or nameless_list_edit(command) is not None
            or date_question(command) is not None or draw(command) is not None   # a throwaway draw
        )
