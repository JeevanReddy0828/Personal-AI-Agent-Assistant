"""`remind me to call mom at 6pm` answered "Reminder date/time must look like YYYY-MM-DD".

No "6pm", no "tomorrow", no "in 20 minutes" — the only accepted form was an ISO stamp,
which is not how anyone speaks and certainly not how anyone dictates. These tests pin the
whole grammar against a fixed instant, because the failure that matters here is not a
crash: it is a reminder that quietly lands on the wrong day.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from laptop_agent.scheduler import ScheduleError, parse_schedule
from laptop_agent.timeparse import (
    DURATION_UNITS, TimeParseError, describe, parse_clock, parse_when,
)

EDT = timezone(timedelta(hours=-4), "EDT")
# Thursday 17 September 2026, 2:30 PM. Afternoon on purpose: it puts 9am in the past and
# 6pm in the future, so the rollover rules are exercised in both directions.
NOW = datetime(2026, 9, 17, 14, 30, tzinfo=EDT)


def at(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=EDT)


class GrammarTests(unittest.TestCase):
    """Every phrasing, resolved against one fixed `now`."""

    CASES = [
        # a time today, and the same time already gone -> tomorrow
        ("at 6pm", at(2026, 9, 17, 18)),
        ("at 6 pm", at(2026, 9, 17, 18)),
        ("at 6:30pm", at(2026, 9, 17, 18, 30)),
        ("at 18:00", at(2026, 9, 17, 18)),
        ("at 9am", at(2026, 9, 18, 9)),
        ("at noon", at(2026, 9, 18, 12)),
        ("at midnight", at(2026, 9, 18, 0)),
        # a bare hour: 1-6 reads as the afternoon, 7-12 as written
        ("at 5", at(2026, 9, 17, 17)),
        ("at 11", at(2026, 9, 18, 11)),
        # durations
        ("in 20 minutes", at(2026, 9, 17, 14, 50)),
        ("in 2 hours", at(2026, 9, 17, 16, 30)),
        ("in an hour", at(2026, 9, 17, 15, 30)),
        ("in half an hour", at(2026, 9, 17, 15)),
        ("in 3 days", at(2026, 9, 20, 14, 30)),
        ("in 2 weeks", at(2026, 10, 1, 14, 30)),
        # day words
        ("tomorrow", at(2026, 9, 18, 9)),
        ("tomorrow at 7am", at(2026, 9, 18, 7)),
        ("tomorrow morning", at(2026, 9, 18, 9)),
        ("tomorrow evening", at(2026, 9, 18, 18)),
        ("today at 5pm", at(2026, 9, 17, 17)),
        ("tonight", at(2026, 9, 17, 20)),
        ("the day after tomorrow at 8am", at(2026, 9, 19, 8)),
        ("next week", at(2026, 9, 24, 9)),
        # weekdays. Today is Thursday, so Thursday 6pm is still ahead but 9am is gone.
        ("monday", at(2026, 9, 21, 9)),
        ("monday at 6pm", at(2026, 9, 21, 18)),
        ("on saturday at 10am", at(2026, 9, 19, 10)),
        ("thursday at 6pm", at(2026, 9, 17, 18)),
        ("thursday at 9am", at(2026, 9, 24, 9)),
        ("next thursday at 9am", at(2026, 9, 24, 9)),
        # written dates, rolling to next year once this year's has gone
        ("on the 25th december at 7am", at(2026, 12, 25, 7)),
        ("5 march at 3pm", at(2027, 3, 5, 15)),
        ("march 5", at(2027, 3, 5, 9)),
        # the format that always worked, which must keep working
        ("2026-12-25 07:30", at(2026, 12, 25, 7, 30)),
        ("2026-12-25", at(2026, 12, 25, 9)),
    ]

    def test_every_phrasing_resolves_to_the_right_instant(self) -> None:
        for text, expected in self.CASES:
            with self.subTest(text):
                found = parse_when(text, NOW)
                self.assertIsNotNone(found, f"no time found in {text!r}")
                assert found is not None
                self.assertEqual(
                    found.at, expected,
                    f"{text!r} -> {found.at:%a %d %b %H:%M}, expected {expected:%a %d %b %H:%M}")

    def test_a_resolved_time_is_never_in_the_past(self) -> None:
        """Except where the user named a day explicitly — then it is their statement, not
        ours to move, and the caller says it has already gone."""
        for text, _ in self.CASES:
            if text.startswith(("today", "2026-")):
                continue
            with self.subTest(text):
                found = parse_when(text, NOW)
                assert found is not None
                self.assertGreater(found.at, NOW, f"{text!r} resolved into the past")

    def test_parsing_is_pure(self) -> None:
        """Same text, same now, same answer — every time. A reminder may not depend on
        when it happened to be parsed."""
        for text, _ in self.CASES:
            with self.subTest(text):
                first, second = parse_when(text, NOW), parse_when(text, NOW)
                assert first is not None and second is not None
                self.assertEqual(first.at, second.at)
                self.assertEqual((first.start, first.end), (second.start, second.end))


class RefusalTests(unittest.TestCase):
    """A time that cannot exist is refused, never slid quietly to a neighbouring hour."""

    def test_impossible_times_are_refused(self) -> None:
        for text in ("at 25:00", "at 13pm", "at 0pm", "meet at 12:75", "2026-02-30",
                     "on 30 february at 9am"):
            with self.subTest(text):
                with self.assertRaises(TimeParseError, msg=f"{text!r} was accepted"):
                    parse_when(text, NOW)

    def test_text_with_no_time_finds_none(self) -> None:
        """A number in the message is not a time: this is what stops "buy 2 apples"
        becoming two in the morning."""
        for text in ("buy 2 apples", "call the office", "email bob about the 3 servers",
                     "stretch", "read chapter 4"):
            with self.subTest(text):
                self.assertIsNone(parse_when(text, NOW), f"{text!r} invented a time")


class MessageSplitTests(unittest.TestCase):
    """The words that expressed the time come out; the rest of the sentence stays."""

    def split(self, text: str) -> str:
        """The real extraction, not a reimplementation of it — a helper that rebuilds the
        logic under test would agree with a bug as readily as with a fix."""
        from laptop_agent.agents.orchestrator import _reminder_message
        found = parse_when(text, NOW)
        assert found is not None
        return _reminder_message(text, found.start, found.end)

    def test_the_time_words_are_removed_with_their_preposition(self) -> None:
        self.assertEqual(self.split("call mom at 6pm"), "call mom")
        self.assertEqual(self.split("call mom on friday at 10am"), "call mom")
        self.assertEqual(self.split("pay rent on the 1st october at 9am"), "pay rent")

    def test_the_rightmost_time_wins(self) -> None:
        """A message may carry a number of its own; the time is what the sentence ends on."""
        self.assertEqual(self.split("call mom at the office at 6pm"), "call mom at the office")
        found = parse_when("call mom at the office at 6pm", NOW)
        assert found is not None
        self.assertEqual(found.at, at(2026, 9, 17, 18))

    def test_a_time_in_the_middle_keeps_both_halves(self) -> None:
        self.assertEqual(self.split("call mom at 6pm about the invoice"),
                         "call mom about the invoice")


class ClockTests(unittest.TestCase):
    def test_parse_clock_reads_a_time_of_day(self) -> None:
        for text, expected in (("6pm", (18, 0)), ("6 pm", (18, 0)), ("18:00", (18, 0)),
                               ("08:30", (8, 30)), ("noon", (12, 0)), ("midnight", (0, 0)),
                               ("at 9am", (9, 0))):
            with self.subTest(text):
                self.assertEqual(parse_clock(text), expected)

    def test_parse_clock_returns_none_for_a_non_time(self) -> None:
        for text in ("teatime", "", "later"):
            with self.subTest(text):
                self.assertIsNone(parse_clock(text))


class SharedWithSchedulerTests(unittest.TestCase):
    """One parser, two callers. Four near-identical tokenizers is the mistake terms.py had
    to undo; this asserts the reminder and the scheduler cannot drift apart."""

    def test_the_scheduler_reads_the_same_times_a_reminder_does(self) -> None:
        for text, expected in (("daily at 6pm", (18, 0)), ("daily at 08:30", (8, 30)),
                               ("every day at 9am", (9, 0)), ("at noon", (12, 0))):
            with self.subTest(text):
                schedule = parse_schedule(text)
                self.assertEqual((schedule.hour, schedule.minute), expected)

    def test_the_scheduler_refuses_an_impossible_time_too(self) -> None:
        with self.assertRaises(ScheduleError):
            parse_schedule("daily at 25:00")

    def test_both_share_one_duration_table(self) -> None:
        from laptop_agent import scheduler
        self.assertIs(scheduler._UNIT_SECONDS, DURATION_UNITS)
        self.assertEqual(parse_schedule("every 2 weeks").seconds, 14 * 86400)


class DescribeTests(unittest.TestCase):
    """The resolved instant is read back in local time. It is the only thing standing
    between a misparse and a reminder that silently never fires."""

    def test_it_reads_back_in_words(self) -> None:
        self.assertEqual(describe(at(2026, 9, 17, 18), NOW), "today at 6:00 PM")
        self.assertEqual(describe(at(2026, 9, 18, 9), NOW), "tomorrow at 9:00 AM")
        self.assertEqual(describe(at(2026, 9, 21, 18), NOW), "Monday at 6:00 PM")
        self.assertIn("25 December 2026", describe(at(2026, 12, 25, 7), NOW))

    def test_it_uses_no_glibc_only_directive(self) -> None:
        """`%-I` raises ValueError on Windows — see ERRORS.md. This runs on the platform
        the app is developed on, so it would catch a reintroduction directly."""
        for moment in (at(2026, 9, 17, 9, 5), at(2026, 9, 17, 12), at(2026, 9, 17, 0)):
            with self.subTest(str(moment)):
                self.assertNotIn("%", describe(moment, NOW))


class ReminderCommandTests(unittest.TestCase):
    """End to end through the orchestrator, the way it is actually typed or spoken."""

    def orchestrator(self, root: Path):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_orchestrator import OrchestratorTests
        return OrchestratorTests("test_reminder_flow").build(root)

    def test_natural_phrasings_set_a_reminder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.orchestrator(Path(raw))
            for said, expected_message in (
                ("remind me to call mom at 6pm", "call mom"),
                ("remind me to take the bins out tomorrow at 7am", "take the bins out"),
                ("remind me in 20 minutes to check the oven", "check the oven"),
                ("remind me to call the dentist on friday at 10am", "call the dentist"),
            ):
                with self.subTest(said):
                    result = asyncio.run(orchestrator.handle(said))
                    self.assertTrue(result.ok, result.message)
                    self.assertEqual(result.data["reminder"]["message"], expected_message)
                    self.assertIn(result.data["due_spoken"], result.message)

    def test_it_routes_without_asking_the_model(self) -> None:
        """The heuristic used to require an ISO date, so "can you remind me to call mom at
        6pm" fell through to the LLM router — a request with one right answer settled by a
        guess. Routing this costs no network call and cannot vary."""
        from laptop_agent.planner.heuristic import HeuristicPlannerProvider
        planner = HeuristicPlannerProvider()
        for said in ("can you remind me to call mom at 6pm",
                     "set a reminder to water the plants tomorrow at 8am",
                     "hey, remind me in 20 minutes to check the oven"):
            with self.subTest(said):
                decision = planner._reminder(said)
                self.assertIsNotNone(decision, f"{said!r} did not route instantly")
                assert decision is not None
                self.assertTrue(decision.command.startswith("reminder add "))

    def test_listing_reminders_is_not_turned_into_creating_one(self) -> None:
        from laptop_agent.planner.heuristic import HeuristicPlannerProvider
        planner = HeuristicPlannerProvider()
        for said, expected in (("reminders", "reminders"), ("show reminders", "reminders"),
                               ("reminders due", "reminders due"),
                               ("complete reminder 3", "reminder done 3")):
            with self.subTest(said):
                decision = planner._reminder(said)
                assert decision is not None
                self.assertEqual(decision.command, expected)

    def test_the_iso_form_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.orchestrator(Path(raw))
            result = asyncio.run(orchestrator.handle("reminder add 2026-12-25 07:30 open presents"))
            self.assertTrue(result.ok, result.message)
            self.assertEqual(result.data["reminder"]["message"], "open presents")

    def test_a_reminder_with_no_time_says_what_to_try(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.orchestrator(Path(raw))
            result = asyncio.run(orchestrator.handle("remind me to stretch"))
            self.assertFalse(result.ok)
            self.assertIn("6pm", result.message, "the refusal does not show a working example")

    def test_an_impossible_time_is_refused_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            orchestrator = self.orchestrator(Path(raw))
            result = asyncio.run(orchestrator.handle("remind me to meet sam at 25:00"))
            self.assertFalse(result.ok)
            self.assertIn("25:00", result.message)


if __name__ == "__main__":
    unittest.main()
