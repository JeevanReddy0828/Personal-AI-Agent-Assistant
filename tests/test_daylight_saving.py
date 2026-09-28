"""A reminder set before a daylight-saving change, for a time after it, rang an hour early.

`timeparse` stamped today's UTC offset onto every day it placed a time. So "remind me
Monday at 7am", said on Friday 30 October 2026 in New York, was stored as 07:00-04:00:
6:00 AM once the clocks went back on 1 November. The confirmation read it back in the same
offset, so it said 7:00 and hid the mistake until the alarm went off.

Most of these put a named zone in `timeparse.LOCAL_ZONE`, because a process cannot change
its zone on Windows and CI runs in UTC. One POSIX test runs the operating system's own
rules instead, which is what the app uses.
"""

from __future__ import annotations

import ast
import os
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import laptop_agent.timeparse as timeparse
from laptop_agent.reminders import ReminderStore
from laptop_agent.timeparse import describe, parse_when
from test_everyday_requests import Everyday

try:
    NEW_YORK: ZoneInfo | None = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:   # Windows without the tzdata package; CI installs it
    NEW_YORK = None

EDT = timezone(timedelta(hours=-4))
EST = timezone(timedelta(hours=-5))
# How the app reads its clock: `datetime.now().astimezone()` carries only today's offset.
FRIDAY = datetime(2026, 10, 30, 12, 0, tzinfo=EDT)           # two days before the clocks go back
SATURDAY = datetime(2026, 10, 31, 12, 0, tzinfo=EDT)
SPRING_FRIDAY = datetime(2027, 3, 12, 12, 0, tzinfo=EST)      # two days before they go forward


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def stopped(at: datetime) -> type[datetime]:
    """A `datetime` whose `now()` is `at`, read the way `datetime.now()` reads this machine."""

    class Stopped(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at.astimezone().replace(tzinfo=None)

    return Stopped


@unittest.skipIf(NEW_YORK is None, "no zone data here; CI installs tzdata")
class OnTheLaptopsClockTests(unittest.TestCase):
    def setUp(self) -> None:
        zone = patch.object(timeparse, "LOCAL_ZONE", NEW_YORK)
        zone.start()
        self.addCleanup(zone.stop)

    def test_a_time_past_the_autumn_change_keeps_its_wall_clock_time(self) -> None:
        when = parse_when("monday at 7am", FRIDAY, local=True)
        self.assertEqual(when.at, utc(2026, 11, 2, 12, 0))                 # 7:00 EST, not 6:00
        self.assertEqual(when.at.utcoffset(), timedelta(hours=-5))
        self.assertEqual(describe(when.at, FRIDAY, local=True), "Monday at 7:00 AM")

    def test_and_past_the_spring_change(self) -> None:
        when = parse_when("monday at 7am", SPRING_FRIDAY, local=True)
        self.assertEqual(when.at, utc(2027, 3, 15, 11, 0))                 # 7:00 EDT, not 8:00
        self.assertEqual(describe(when.at, SPRING_FRIDAY, local=True), "Monday at 7:00 AM")

    def test_an_iso_date_past_the_change(self) -> None:
        self.assertEqual(parse_when("2026-11-02 07:00", FRIDAY, local=True).at, utc(2026, 11, 2, 12, 0))

    def test_a_passed_time_rolls_to_the_next_calendar_day(self) -> None:
        # Said at 7pm the evening before the change, 6pm tomorrow is 23 hours away, not 24.
        evening = datetime(2026, 10, 31, 19, 0, tzinfo=EDT)
        when = parse_when("6pm", evening, local=True)
        self.assertEqual(when.at, utc(2026, 11, 1, 23, 0))
        self.assertEqual(describe(when.at, evening, local=True), "tomorrow at 6:00 PM")

    def test_a_passed_weekday_rolls_a_week_by_the_calendar(self) -> None:
        sunday_evening = datetime(2026, 10, 25, 19, 0, tzinfo=EDT)
        when = parse_when("sunday at 6pm", sunday_evening, local=True)
        self.assertEqual(when.at, utc(2026, 11, 1, 23, 0))

    def test_the_repeated_hour_is_its_first_occurrence(self) -> None:
        when = parse_when("tomorrow at 1:30am", SATURDAY, local=True)
        self.assertEqual(when.at, utc(2026, 11, 1, 5, 30))                 # 1:30 EDT, the first
        self.assertEqual(describe(when.at, SATURDAY, local=True), "tomorrow at 1:30 AM")
        # The second 1:30 reads as 1:30 too, in its own offset.
        self.assertEqual(describe(utc(2026, 11, 1, 6, 30), SATURDAY, local=True), "tomorrow at 1:30 AM")

    def test_a_skipped_time_moves_forward_by_the_gap(self) -> None:
        when = parse_when("sunday at 2:30am", SPRING_FRIDAY, local=True)
        self.assertEqual(when.at, utc(2027, 3, 14, 7, 30))                 # 3:30 EDT
        self.assertEqual(describe(when.at, SPRING_FRIDAY, local=True), "Sunday at 3:30 AM")

    def test_a_fixed_offset_now_parses_exactly_as_before(self) -> None:
        # Without `local`, `now`'s offset is the zone, so the parse stays pure.
        when = parse_when("monday at 7am", FRIDAY)
        self.assertEqual(when.at, datetime(2026, 11, 2, 7, 0, tzinfo=EDT))
        self.assertEqual(describe(when.at, FRIDAY), "Monday at 7:00 AM")


class BeyondTheOperatingSystemsRulesTests(unittest.TestCase):
    def test_a_year_its_rules_cannot_be_read_for_keeps_todays_offset(self) -> None:
        """Windows' C runtime raises OSError outside 1970-3000, so a typed date there would
        have crashed the reminder instead of setting it."""
        today = FRIDAY.astimezone().utcoffset()
        for text, year, month, day in (("1969-12-31 09:00", 1969, 12, 31), ("3001-01-01 09:00", 3001, 1, 1)):
            with self.subTest(text):
                when = parse_when(text, FRIDAY, local=True)
                self.assertEqual(when.at.replace(tzinfo=None), datetime(year, month, day, 9, 0))
                self.assertEqual(when.at.utcoffset(), today)
                self.assertTrue(describe(when.at, FRIDAY, local=True).endswith("at 9:00 AM"))


@unittest.skipUnless(hasattr(time, "tzset"), "a process can change its zone only on POSIX")
class TheOperatingSystemsOwnRulesTests(unittest.TestCase):
    """What the app itself runs: nothing in LOCAL_ZONE, the machine's own rules."""

    def setUp(self) -> None:
        saved = os.environ.get("TZ")
        os.environ["TZ"] = "America/New_York"
        time.tzset()

        def restore() -> None:
            if saved is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = saved
            time.tzset()

        self.addCleanup(restore)

    def test_monday_at_seven_is_seven_after_the_clocks_go_back(self) -> None:
        self.assertIsNone(timeparse.LOCAL_ZONE)
        now = datetime(2026, 10, 30, 12, 0).astimezone()           # as the app reads its clock
        when = parse_when("monday at 7am", now, local=True)
        self.assertEqual(when.at, utc(2026, 11, 2, 12, 0))
        self.assertEqual(describe(when.at, now, local=True), "Monday at 7:00 AM")


@unittest.skipIf(NEW_YORK is None, "no zone data here; CI installs tzdata")
class ThroughTheAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name), llm="none")
        zone = patch.object(timeparse, "LOCAL_ZONE", NEW_YORK)
        zone.start()
        self.addCleanup(zone.stop)

    def say_at(self, moment: datetime, text: str) -> str:
        with patch("laptop_agent.agents.orchestrator.datetime", stopped(moment)):
            result, _ran = self.everyday.say(text)
        return result.message

    def due(self) -> list[datetime]:
        return [datetime.fromisoformat(item["due_at"])
                for item in self.everyday.orchestrator.context.reminders.list()]

    def test_a_reminder_set_on_friday_for_monday_rings_at_seven(self) -> None:
        self.assertIn("Monday at 7:00 AM", self.say_at(FRIDAY, "remind me monday at 7am to stretch"))
        self.assertEqual(self.due(), [utc(2026, 11, 2, 12, 0)])
        self.assertIn("Monday at 7:00 AM", self.say_at(FRIDAY, "show my reminders"))

    def test_a_week_that_is_set_once_goes_through_the_same_clock(self) -> None:
        # "every week" with no day is set once, by the repeating path's own parse - the call
        # site the first version of this fix missed.
        self.say_at(SATURDAY, "remind me every week at 7am to water the plants")
        self.assertEqual(self.due(), [utc(2026, 11, 1, 12, 0)])

    def test_the_card_says_monday_at_seven(self) -> None:
        import laptop_agent.webui as webui

        store = ReminderStore(Path(self.tmp.name) / "card.json")
        store.add("2026-11-02T07:00:00-05:00", "stretch")
        original = webui._orchestrator.context.reminders
        object.__setattr__(webui._orchestrator.context, "reminders", store)
        self.addCleanup(object.__setattr__, webui._orchestrator.context, "reminders", original)
        with patch.object(webui, "datetime", stopped(FRIDAY)):
            snapshot = webui._reminders_snapshot()
        self.assertEqual(snapshot["upcoming"][0]["due_spoken"], "Monday at 7:00 AM")


def _calls(tree: ast.AST, names: set[str]):
    """Calls of `names` as imported. A function that binds one of those names itself - the
    orchestrator's vision helper assigns `describe = getattr(...)` - is calling something else."""

    def visit(node: ast.AST, shadowed: frozenset[str]):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            arguments = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            shadowed = shadowed | {arg.arg for arg in arguments} | {
                name.id for name in ast.walk(node) if isinstance(name, ast.Name) and isinstance(name.ctx, ast.Store)}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names - shadowed:
            yield node
        for child in ast.iter_child_nodes(node):
            yield from visit(child, shadowed)

    yield from visit(tree, frozenset())


class EveryCallerReadsTheLaptopsClockTests(unittest.TestCase):
    def test_every_production_call_passes_local(self) -> None:
        """A time placed or read without `local` takes today's offset for every day, which is
        the bug above. The first version of this fix changed eight call sites by hand and
        missed a ninth, the repeating path's one-off fallback, so this reads them all."""
        root = Path(timeparse.__file__).parent
        calls, missing = 0, []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {alias.asname or alias.name for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module == "laptop_agent.timeparse"
                     for alias in node.names if alias.name in ("parse_when", "describe")}
            for call in _calls(tree, names):
                calls += 1
                local = next((keyword.value for keyword in call.keywords if keyword.arg == "local"), None)
                if not (isinstance(local, ast.Constant) and local.value is True):
                    missing.append(f"{path.relative_to(root)}:{call.lineno}")
        self.assertGreaterEqual(calls, 12, "the call sites moved; revisit this test")
        self.assertEqual(missing, [], "these read the laptop's clock in today's offset")


if __name__ == "__main__":
    unittest.main()
