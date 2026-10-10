"""Unit conversions and date counting: values with one right answer, computed.

Both used to reach a chat model: "convert 5 miles to km", "how many ounces in a pound",
"how many days until christmas", "when is thanksgiving".
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from laptop_agent.planner import HeuristicPlannerProvider
from laptop_agent.tools.dates import (answerable, calendar_fact, date_question, next_holiday, resolve, span_text,
                                      until_moment)
from laptop_agent.tools.units import UnitTool, convert, looks_like_conversion, parse
from test_everyday_requests import Everyday

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone(timedelta(hours=-5)))

try:
    _NEW_YORK: ZoneInfo | None = ZoneInfo("America/New_York")
except ZoneInfoNotFoundError:   # Windows without the tzdata package; CI installs it
    _NEW_YORK = None


class UnitTests(unittest.TestCase):
    def test_everyday_conversions(self) -> None:
        cases = {
            "convert 5 miles to km": "8.05 kilometres",
            "how many ounces in a pound": "16 ounces",
            "100 fahrenheit to celsius": "37.78°C",
            "what is 30 degrees celsius in fahrenheit": "86°F",
            "0 kelvin to celsius": "-273.15°C",
            "6 feet in cm": "182.88 centimetres",
            "60 mph to km/h": "96.56 kilometres per hour",
            "convert five miles to km": "8.05 kilometres",
            "how many seconds in a day": "86,400 seconds",
        }
        for text, expected in cases.items():
            result = UnitTool().convert(text)
            self.assertTrue(result.ok, f"{text}: {result.message}")
            self.assertIn(f"**{expected}**", result.message, text)

    def test_an_amount_said_as_a_fraction(self) -> None:
        # "how many tablespoons in a quarter cup" went to the chat model.
        cases = {
            "how many tablespoons in a quarter cup": "4 tablespoons",
            "how many teaspoons in half a cup": "24 teaspoons",
            "how many ml in three quarters of a cup": "177.44 millilitres",
            "convert one and a half cups to ml": "354.88 millilitres",
            "how many grams in a pound and a half": "680.39 grams",
            "convert 3/4 cup to ml": "177.44 millilitres",
            "how many tablespoons in a third of a cup": "5.33 tablespoons",
            "convert half a mile to meters": "804.67 metres",
        }
        for text, expected in cases.items():
            with self.subTest(text):
                result = UnitTool().convert(text)
                self.assertTrue(result.ok, result.message)
                self.assertIn(f"**{expected}**", result.message)
        # Only where an amount stands, just before a unit: the rest of a sentence is not rewritten.
        from laptop_agent.tools.units import _fraction_amounts

        self.assertEqual(_fraction_amounts("half the cups and a quarter of them"), "half the cups and a quarter of them")
        self.assertEqual(_fraction_amounts("half a cup"), "0.5 cup")
        for text in ("half the cups are broken", "a quarter pounder with cheese",
                     "how many calories in half a cup of rice", "how many tablespoons in 1/0 cup"):
            with self.subTest(text):
                self.assertFalse(looks_like_conversion(text))

    def test_us_volumes_say_so(self) -> None:
        self.assertIn("(US measure)", UnitTool().convert("convert 1 gallon to liters").message)

    def test_a_bare_k_is_kilometres_against_a_length(self) -> None:
        # "convert 5k to miles" was answered "kelvin is a temperature and mile is a length".
        self.assertIn("**3.11 miles**", UnitTool().convert("convert 5k to miles").message)
        self.assertIn("**3.11 miles**", UnitTool().convert("how many miles is a 5k").message)
        self.assertIn("**26.85°C**", UnitTool().convert("300k to celsius").message)

    def test_ounces_against_a_volume_are_fluid_ounces(self) -> None:
        # Refused as "a volume and a mass"; nobody asking how many ounces are in a cup means weight.
        self.assertIn("**8 fluid ounces** (US measure)", UnitTool().convert("how many ounces in a cup").message)
        self.assertIn("**2 cups**", UnitTool().convert("convert 16 oz to cups").message)
        self.assertIn("**16 ounces**", UnitTool().convert("how many ounces in a pound").message)

    def test_different_kinds_do_not_convert(self) -> None:
        result = UnitTool().convert("convert 5 miles to kg")
        self.assertFalse(result.ok)
        self.assertIn("length", result.message)

    def test_only_conversions_are_conversions(self) -> None:
        for text in ("convert this pdf to word", "i am 5 feet away", "how many people in a room",
                     "run 5 miles in an hour", "convert file a.txt to b.md", "10 to 3",
                     "how many minutes in the meeting"):
            self.assertFalse(looks_like_conversion(text), text)
        self.assertEqual(parse("convert how many ounces in a pound"), (1.0, "pound", "ounce"))
        self.assertAlmostEqual(convert(212, "fahrenheit", "celsius"), 100)

    def test_an_amount_in_two_units(self) -> None:
        """A height or a weight said in two units reached a chat model."""
        tool = UnitTool()
        for text, expected in (
            ("what's 5 feet 10 inches in cm", "5 feet 10 inches = **177.8 centimetres**"),
            ("convert 5 ft 10 in to cm", "5 feet 10 inches = **177.8 centimetres**"),
            ("what is 5'10\" in cm", "5 feet 10 inches = **177.8 centimetres**"),
            ("how tall is 6 foot 2 in cm", "6 feet 2 inches = **187.96 centimetres**"),
            ("what's 2 pounds 4 ounces in grams", "2 pounds 4 ounces = **1,020.58 grams**"),
            ("how many minutes is 2 hours 30 minutes", "2 hours 30 minutes = **150 minutes**"),
            ("convert 180 cm to feet and inches", "180 centimetres = **5 feet 10.87 inches**"),
            ("convert 152.4 cm to feet and inches", "152.4 centimetres = **5 feet**"),
            ("convert 150 minutes to hours and minutes", "150 minutes = **2 hours 30 minutes**"),
            # divmod floors, so a negative split wrongly (Codex's review): -90 minutes is -1 h 30 min.
            ("convert -90 minutes to hours and minutes", "-90 minutes = **-1 hour 30 minutes**"),
            ("convert -18 ounces to pounds and ounces", "-18 ounces = **-1 pound 2 ounces**"),
            ("convert -5 ft 10 in to cm", "-5 feet 10 inches = **-177.8 centimetres**"),
            ("convert -0.001 minutes to hours and minutes", "-0.001 minutes = **0 minutes**"),
        ):
            with self.subTest(text):
                self.assertTrue(looks_like_conversion(text))
                self.assertEqual(tool.convert(text).message, expected)
        # Two units that are not one amount stay out: no unit says what 10 is here.
        for text in ("i am 5 feet 10 away", "remind me in 2 hours 30 minutes", "what is 2 hours 30 minutes from now",
                     "convert 5 feet 10 pounds to cm"):
            with self.subTest(text):
                self.assertFalse(looks_like_conversion(text))


class DateTests(unittest.TestCase):
    def test_holidays_fixed_and_floating(self) -> None:
        today = NOW.date()
        self.assertEqual(next_holiday("christmas", today), date(2026, 12, 25))
        self.assertEqual(next_holiday("thanksgiving", today), date(2026, 11, 26))    # 4th Thursday
        self.assertEqual(next_holiday("easter", today), date(2027, 3, 28))           # computus
        self.assertEqual(next_holiday("mother's day", today), date(2027, 5, 9))      # 2nd Sunday of May
        self.assertEqual(next_holiday("labor day", today), date(2027, 9, 6))         # 2026's has passed
        self.assertEqual(next_holiday("memorial day", date(2026, 1, 1)), date(2026, 5, 25))  # last Monday

    def test_a_stated_year_is_the_year(self) -> None:
        # Each of these got the next time the date came round, whatever year was said.
        for text, expected in (("july 4th this year", date(2026, 7, 4)), ("easter this year", date(2026, 4, 5)),
                               ("this year's easter", date(2026, 4, 5)), ("christmas next year", date(2027, 12, 25)),
                               ("thanksgiving next year", date(2027, 11, 25)), ("july 4th 2030", date(2030, 7, 4)),
                               ("christmas 2030", date(2030, 12, 25)), ("the 4th of july 2028", date(2028, 7, 4)),
                               ("june 5 this year", date(2026, 6, 5)), ("june 5, 2027", date(2027, 6, 5))):
            with self.subTest(text):
                self.assertEqual(resolve(text, NOW)[0], expected)
        # Unchanged: no year said is the next one, and "end of this year" keeps its own reading.
        self.assertEqual(resolve("easter", NOW)[0], date(2027, 3, 28))
        self.assertEqual(resolve("next easter", NOW)[0], date(2027, 3, 28))
        self.assertEqual(resolve("end of this year", NOW)[0], date(2026, 12, 31))
        self.assertIsNone(resolve("february 29 2027", NOW))
        profile = {"birthday": "march 3"}
        self.assertEqual(resolve("my birthday next year", NOW, profile)[0], date(2027, 3, 3))

    def test_a_count_of_days_weeks_months_or_years_from_today(self) -> None:
        # "what was the date 2 weeks ago", "what day will it be in 3 months" went to the model.
        for text, expected in (("2 weeks ago", date(2026, 9, 12)), ("a month ago", date(2026, 8, 26)),
                               ("in 3 months", date(2026, 12, 26)), ("10 days from now", date(2026, 10, 6)),
                               ("in two years", date(2028, 9, 26)), ("christmas last year", date(2025, 12, 25)),
                               ("last year's easter", date(2025, 4, 20)), ("july 4 1776", date(1776, 7, 4))):
            with self.subTest(text):
                self.assertEqual(resolve(text, NOW)[0], expected)
        # A month back from 31 March is the end of February, not 3 March.
        self.assertEqual(resolve("a month ago", datetime(2026, 3, 31, 12, 0))[0], date(2026, 2, 28))
        self.assertIsNone(resolve("a few weeks ago", NOW))

    def test_since_left_and_the_past_tense_are_date_questions(self) -> None:
        for text, expected in (("how many days since march 1", ("between", "march 1", "today")),
                               ("how long has it been since christmas", ("between", "christmas", "today")),
                               ("how many days left in the year", ("until", "end of the year", "")),
                               ("how many weeks are left in this month", ("until", "end of the month", "weeks")),
                               ("what day of the week was july 4 1776", ("when", "july 4 1776", "")),
                               ("what day will it be in 10 days", ("when", "in 10 days", "")),
                               ("what was the date 2 weeks ago", ("when", "2 weeks ago", "")),
                               ("when was christmas last year", ("when", "christmas last year", ""))):
            with self.subTest(text):
                self.assertEqual(date_question(text), expected)
        self.assertFalse(answerable("how many days since the update", NOW))

    def test_the_users_own_dates(self) -> None:
        profile = {"wife's_birthday": "june 5", "birthday": "march 3"}
        self.assertEqual(resolve("my wife's birthday", NOW, profile), (date(2027, 6, 5), "your wife's birthday"))
        self.assertEqual(resolve("my birthday", NOW, profile)[0], date(2027, 3, 3))
        self.assertIsNone(resolve("my birthday", NOW, {}))

    def test_what_is_and_is_not_a_date_question(self) -> None:
        self.assertEqual(date_question("how many days until christmas?"), ("until", "christmas", ""))
        self.assertEqual(date_question("how many days between march 1 and april 15"),
                         ("between", "march 1", "april 15"))
        self.assertTrue(answerable("when is thanksgiving", NOW))
        self.assertTrue(answerable("when is my birthday", NOW))           # the user's own, looked up later
        self.assertFalse(answerable("when is the next train", NOW))
        self.assertFalse(answerable("how long until the movie starts", NOW))


class StoppedClock(datetime):
    """The orchestrator's `datetime`, stopped at local noon, 90 days before Christmas. On the
    real clock "how many weeks until christmas" was "5 days" from 19 to 25 December, and
    midnight passing mid-test moved the answer to a day the test had not expected."""

    @classmethod
    def now(cls, tz=None):
        noon = datetime(2026, 9, 26, 12, 0)
        return noon if tz is None else noon.astimezone(tz)


class ThroughTheAssistantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.everyday = Everyday(Path(self.tmp.name))

    def say(self, text: str):
        return self.everyday.say(text, stream=False)

    def test_they_are_answered_exactly(self) -> None:
        result, ran = self.say("hey jarvis, how many days until christmas")
        self.assertIn("days** until Christmas", result.message)
        result, _ran = self.say("how many ounces in a pound")
        self.assertIn("**16 ounces**", result.message)
        result, _ran = self.say("how many days between march 1 and april 15")
        self.assertIn("**45 days**", result.message)
        # "what's the date tomorrow" was computed, "what's the date next friday" went to a model.
        # On the stopped clock: on the real one, every Thursday "on friday" is "— tomorrow", and
        # CI went red one day in seven (2026-10-08, on a docs-only PR).
        with patch("laptop_agent.agents.orchestrator.datetime", StoppedClock):
            for text in ("what's the date next friday", "what is the date on friday", "what's the day of thanksgiving"):
                result, _ran = self.say(text)
                self.assertNotIn("answered]", result.message, text)
                self.assertRegex(result.message, r"\d{4} — \d+ days? from today", text)

    def test_a_personal_date_comes_from_memory_or_a_reminder(self) -> None:
        self.say("remember my wife's birthday is june 5")
        self.assertIn("5 June", self.say("when is my wife's birthday")[0].message)
        self.say("add dentist appointment to my calendar next tuesday at 9")
        self.assertIn("You have a reminder for that: dentist appointment",
                      self.say("when is my dentist appointment")[0].message)
        self.assertIn("haven't told me", self.say("when is my anniversary")[0].message)
        self.assertIn("I don't know when your birthday is", self.say("how many days until my birthday")[0].message)

    def test_counted_in_the_unit_asked_for(self) -> None:
        # "how many weeks until christmas" was answered "90 days".
        with patch("laptop_agent.agents.orchestrator.datetime", StoppedClock):
            message = self.say("how many weeks until christmas")[0].message
            self.assertRegex(message, r"^\*\*\d+ weeks?(?: and \d days?)?\*\* until Christmas")
            self.assertNotIn("weeks", self.say("how many days until christmas")[0].message)

    def test_a_span_up_to_today_starts_from_the_last_time_the_date_came_round(self) -> None:
        # "between march 1 and today" counted to NEXT March (145 days for an answer of 220), and
        # "since christmas" to this December. Looking ahead ("between today and march 1") keeps
        # the next one. On the stopped clock, Saturday 26 September 2026.
        with patch("laptop_agent.agents.orchestrator.datetime", StoppedClock):
            for text, days in (("how many days between march 1 and today", 209), ("how many days since march 1", 209),
                               ("how long has it been since christmas", 275),
                               ("how many days between today and march 1", 156),
                               ("how many days between march 1 2027 and today", 156),   # a year said is kept
                               ("how many days left in the year", 96)):
                with self.subTest(text):
                    self.assertTrue(self.say(text)[0].message.startswith(f"**{days} days**"), self.say(text)[0].message)
            # A weekday has no year to step back to (Codex's review of #244): on this Saturday,
            # "since friday" is yesterday, not the coming Friday six days ahead.
            for text, start in (("how many days since friday", "**1 day** between friday (Friday 25 September 2026)"),
                                ("how many days between friday and today", "**1 day** between friday (Friday 25"),
                                ("how many days since monday", "**5 days** between monday (Monday 21 September 2026)")):
                with self.subTest(text):
                    self.assertTrue(self.say(text)[0].message.startswith(start), self.say(text)[0].message)
            self.assertEqual(self.say("what day of the week was july 4 1776")[0].message.split(" — ")[0],
                             "July 4 was on Thursday, 4 July 1776")
            self.assertEqual(self.say("what was the date 2 weeks ago")[0].message,
                             "2 weeks ago was on Saturday, 12 September 2026 — 14 days ago.")

    def test_a_date_already_past_is_said_in_the_past(self) -> None:
        # With a stated year a date can be behind us: "until" said "-84 days" and "is on" a past day.
        with patch("laptop_agent.agents.orchestrator.datetime", StoppedClock):
            self.assertEqual(self.say("how many days until july 4th this year")[0].message,
                             "July 4th was **84 days ago** (Saturday 4 July 2026).")
            self.assertEqual(self.say("what day is july 4th this year")[0].message,
                             "July 4th was on Saturday, 4 July 2026 — 84 days ago.")
            self.assertEqual(self.say("what day is christmas next year")[0].message,
                             "Christmas is on Saturday, 25 December 2027 — 455 days from today.")

    def test_today_and_tomorrow_by_name(self) -> None:
        # "what's today" went to a web search for the sentence; "what day is tomorrow" said
        # "Tomorrow is on Sunday ... — tomorrow".
        today = StoppedClock.now().date()
        with patch("laptop_agent.agents.orchestrator.datetime", StoppedClock):
            for text, day in (("what's today", today), ("what's the date tomorrow", today + timedelta(days=1)),
                              ("what's tomorrow's date", today + timedelta(days=1)),
                              ("what day was yesterday", today - timedelta(days=1))):
                message = self.say(text)[0].message
                self.assertIn(f"**{day:%A}, {day.day} {day:%B %Y}**", message, text)
                self.assertNotIn("— tomorrow", message)
        self.assertTrue(self.say("what day was yesterday")[0].message.startswith("Yesterday was"))

    def test_a_question_this_cannot_answer_goes_on(self) -> None:
        result, ran = self.say("when is the next train")
        self.assertIsNone(ran)
        self.assertIn("answered]", result.message)

    def test_the_router_hands_them_over(self) -> None:
        planner = HeuristicPlannerProvider()
        self.assertEqual(planner.plan("convert 5 miles to km", "", {}).command, "convert 5 miles to km")
        self.assertEqual(planner.plan("5 miles in km", "", {}).command, "convert 5 miles in km")
        # Addressed by name, so the direct command does not see it and the router must.
        result, ran = self.say("hey jarvis, convert 5 miles to km")
        self.assertEqual(ran, "convert 5 miles to km")
        self.assertIn("**8.05 kilometres**", result.message)
        # The router prefixes `convert`, which the parser read only before "how many": "what's 70
        # fahrenheit in celsius" was routed here and then answered "I don't know how to do that yet".
        result, ran = self.say("what's 70 fahrenheit in celsius")
        self.assertEqual(ran, "convert what's 70 fahrenheit in celsius")
        self.assertIn("**21.11°C**", result.message)
        self.assertIn("**8.05 kilometres**", self.say("how much is 5 miles in km")[0].message)
        self.assertEqual(planner.plan("when is thanksgiving?", "", {}).command, "when is thanksgiving")
        self.assertEqual(planner.plan("what day is it", "", {}).command, "time what day is it")


class CalendarFactTests(unittest.TestCase):
    """Calendar facts with one right answer reached the chat model, or a web search for the
    sentence: "what week of the year is it", "is 2028 a leap year", "is today a holiday"."""

    def test_each_is_computed(self) -> None:
        today = date(2026, 9, 26)    # a Saturday
        for text, expected in (
                ("what week of the year is it", "week **39** of 2026"),
                ("what day of the year is it", "day **269** of 2026, with 96 days left"),
                ("is 2028 a leap year", "**Yes** — 2028 is a leap year"),
                ("is this year a leap year", "**No** — 2026 is not a leap year"),
                ("when is the next leap year", "**2028**"),
                ("how many days in february 2028", "February 2028 has **29 days**"),
                ("how many days are in this month", "September 2026 has **30 days**"),
                ("how many days in next month", "October 2026 has **31 days**"),
                ("what quarter are we in", "**Q3** of 2026"),
                ("what's 30 days from today", "**Monday, 26 October 2026**"),
                ("how many days until the weekend", "It's the weekend now."),
                ("is today a holiday", "**No**"),
                ("when is the next holiday", "**Columbus Day**, Monday, 12 October 2026"),
                ("how old am i if i was born in 1995", "**30** or **31**"),
                ("how old am i if i was born on june 5 1995", "You're **31**."),
                ("how many days old am i if i was born on june 5 1995", "**11,436 days**")):
            with self.subTest(text):
                self.assertIn(expected, calendar_fact(text, today))
                self.assertEqual(date_question(text)[0], "fact")
        self.assertIn("**3 days** until the weekend", calendar_fact("how many days until the weekend", date(2026, 9, 23)))
        self.assertTrue(answerable("is 2028 a leap year", NOW))     # so the instant router claims it
        self.assertIn("**Yes** — today is Christmas", calendar_fact("is today a holiday", date(2026, 12, 25)))
        for text in ("what week is it in the series", "how old am i", "how old am i if i was born on december 25 2030",
                     "how old am i if i was born in 2030"):
            with self.subTest(text):
                self.assertIsNone(calendar_fact(text, today))
                self.assertFalse(answerable(text, NOW))

    def test_through_the_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("laptop_agent.agents.orchestrator.datetime", StoppedClock):
            everyday = Everyday(Path(tmp))
            for text, expected in (("is 2028 a leap year", "**Yes**"), ("what quarter are we in", "**Q3**"),
                                   ("how many days in february", "February 2026 has **28 days**")):
                with self.subTest(text):
                    self.assertIn(expected, everyday.say(text, stream=False)[0].message)


class EveningClock(datetime):
    """The orchestrator's `datetime`, stopped at 7:35pm on a Saturday with no clock change that night."""

    @classmethod
    def now(cls, tz=None):
        evening = datetime(2026, 9, 26, 19, 35)
        return evening if tz is None else evening.astimezone(tz)


class TimeUntilTests(unittest.TestCase):
    """"how long until midnight" counted the days to the date of the next midnight and said
    "1 day", four and a half hours before it; "how many minutes until midnight" reached a model."""

    def test_the_unit_asked_for_is_kept(self) -> None:
        self.assertEqual(date_question("how many minutes until midnight"), ("until", "midnight", "minutes"))
        self.assertEqual(date_question("how many hours till 5pm?"), ("until", "5pm", "hours"))
        self.assertEqual(date_question("how much time until 6:15"), ("until", "6:15", ""))
        for minutes, unit, expected in ((265, "", "4 hours 25 minutes"), (265, "minutes", "265 minutes"),
                                        (1500, "", "1 day 1 hour"), (1500, "hours", "25 hours"),
                                        (60, "", "1 hour"), (0, "", "less than a minute")):
            self.assertEqual(span_text(minutes, unit), expected)

    def test_a_time_of_day_is_counted_to_that_moment(self) -> None:
        offset = NOW.tzinfo
        with patch("laptop_agent.timeparse.LOCAL_ZONE", offset):
            self.assertEqual(until_moment("midnight", "", NOW), datetime(2026, 9, 27, 0, 0, tzinfo=offset))
            # At 10am "9:30" is tonight's, not tomorrow morning's.
            self.assertEqual(until_moment("9:30", "", NOW), datetime(2026, 9, 26, 21, 30, tzinfo=offset))
            self.assertEqual(until_moment("christmas", "hours", NOW), datetime(2026, 12, 25, 0, 0, tzinfo=offset))
            self.assertIsNone(until_moment("friday", "", NOW))              # still counted in days
            self.assertIsNone(until_moment("christmas", "weeks", NOW))
            self.assertIsNone(until_moment("the pasta is done", "minutes", NOW))

    @unittest.skipIf(_NEW_YORK is None, "no zone data here; CI installs tzdata")
    def test_hours_across_a_clock_change_are_real_hours(self) -> None:
        evening = datetime(2026, 9, 26, 19, 35, tzinfo=timezone(timedelta(hours=-4)))
        with patch("laptop_agent.timeparse.LOCAL_ZONE", _NEW_YORK):
            moment = until_moment("christmas", "hours", evening)
        # 25 December starts at 05:00 UTC in New York; the hour the clocks go back is counted.
        self.assertEqual(moment, datetime(2026, 12, 25, 5, 0, tzinfo=timezone.utc))
        self.assertEqual(span_text(round((moment - evening).total_seconds() / 60), "hours"), "2,141 hours 25 minutes")

    @unittest.skipIf(_NEW_YORK is None, "no zone data here; CI installs tzdata")
    def test_the_repeated_hour_is_counted_to_its_next_real_reading(self) -> None:
        """Codex's review: at the first 1:45 on the night the clocks go back, "until 1:30am" said
        "1 day 45 minutes"; the second 1:30 is 45 minutes away."""
        edt, est = timezone(timedelta(hours=-4)), timezone(timedelta(hours=-5))
        cases = ((datetime(2026, 11, 1, 0, 30, tzinfo=edt), "1:30am", 60),     # the first reading
                 (datetime(2026, 11, 1, 1, 45, tzinfo=edt), "1:30am", 45),     # the second, still today
                 (datetime(2026, 11, 1, 1, 45, tzinfo=est), "1:30am", 1425),   # both gone: tomorrow's
                 (datetime(2026, 11, 1, 3, 0, tzinfo=est), "1:30am", 1350),    # after the change
                 (datetime(2026, 11, 1, 1, 45, tzinfo=edt), "1:30", 45),       # no am/pm said
                 (datetime(2026, 11, 1, 1, 45, tzinfo=edt), "1:30am today", 45),
                 (datetime(2026, 11, 1, 1, 45, tzinfo=edt), "sunday at 1:30am", 45),
                 (datetime(2026, 11, 1, 1, 45, tzinfo=edt), "this sunday at 1:30am", 45),
                 (datetime(2026, 11, 1, 1, 45, tzinfo=edt), "next sunday at 1:30am", 10125),   # never today
                 (datetime(2026, 11, 1, 1, 45, tzinfo=est), "1:30am today", -15),   # the later one went
                 (datetime(2026, 10, 31, 1, 45, tzinfo=edt), "sunday at 1:30am", 1425),
                 (datetime(2026, 10, 2, 18, 0, tzinfo=edt), "friday at 5pm", 10020),  # a week, not today
                 (datetime(2026, 9, 26, 10, 0, tzinfo=edt), "tomorrow at 5pm", 1860),  # not today's 5pm
                 (datetime(2027, 3, 14, 1, 0, tzinfo=est), "2:30am", 90))      # skipped in spring: 3:30
        with patch("laptop_agent.timeparse.LOCAL_ZONE", _NEW_YORK):
            for now, what, minutes in cases:
                with self.subTest(now=now.isoformat(), what=what):
                    moment = until_moment(what, "", now)
                    self.assertEqual(round((moment - now).total_seconds() / 60), minutes)

    @unittest.skipIf(_NEW_YORK is None, "no zone data here; CI installs tzdata")
    def test_a_named_day_keeps_the_second_reading_through_the_assistant(self) -> None:
        """Codex's second review: "1:30am today" said "was 15 minutes ago" and "sunday at 1:30am"
        "7 days 45 minutes" at the first 1:45, with the second 1:30 still 45 minutes away."""
        first = datetime(2026, 11, 1, 1, 45, tzinfo=timezone(timedelta(hours=-4)))

        class FirstQuarterToTwo(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return first.astimezone(tz)
                naive = first.astimezone().replace(tzinfo=None)    # read as this machine reads its clock
                return naive if naive.astimezone() == first else naive.replace(fold=1)

        with tempfile.TemporaryDirectory() as tmp, \
                patch("laptop_agent.agents.orchestrator.datetime", FirstQuarterToTwo), \
                patch("laptop_agent.timeparse.LOCAL_ZONE", _NEW_YORK):
            everyday = Everyday(Path(tmp))
            for text in ("how long until 1:30am", "how long until 1:30am today", "how long until sunday at 1:30am"):
                with self.subTest(text):
                    self.assertIn("**45 minutes**", everyday.say(text, stream=False)[0].message)

    def test_through_the_assistant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("laptop_agent.agents.orchestrator.datetime", EveningClock):
            everyday = Everyday(Path(tmp))
            for text, expected in (("how long until midnight", "**4 hours 25 minutes** until midnight"),
                                   ("how many minutes until midnight", "**265 minutes**"),
                                   ("how long until 5pm", "**21 hours 25 minutes** until 5pm (tomorrow at 5:00 PM)"),
                                   ("how much time until 9:30", "**1 hour 55 minutes** until 9:30 (today at 9:30 PM)"),
                                   ("how long until 5pm today", "5pm today was **2 hours 35 minutes ago**"),
                                   ("how long until friday", "**6 days** until friday"),
                                   ("how long until friday at 5pm", "**5 days 21 hours 25 minutes**"),
                                   ("how long until my timer goes off", "no timers")):
                with self.subTest(text):
                    result, ran = everyday.say(text, stream=False)
                    self.assertIn(expected, result.message)


if __name__ == "__main__":
    unittest.main()
