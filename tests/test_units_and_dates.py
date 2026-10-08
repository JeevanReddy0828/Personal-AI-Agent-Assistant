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

from laptop_agent.planner import HeuristicPlannerProvider
from laptop_agent.tools.dates import answerable, date_question, next_holiday, resolve
from laptop_agent.tools.units import UnitTool, convert, looks_like_conversion, parse
from test_everyday_requests import Everyday

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone(timedelta(hours=-5)))


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


if __name__ == "__main__":
    unittest.main()
