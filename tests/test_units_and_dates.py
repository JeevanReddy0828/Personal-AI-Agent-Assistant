"""Unit conversions and date counting: values with one right answer, computed.

Both used to reach a chat model: "convert 5 miles to km", "how many ounces in a pound",
"how many days until christmas", "when is thanksgiving".
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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

    def test_us_volumes_say_so(self) -> None:
        self.assertIn("(US measure)", UnitTool().convert("convert 1 gallon to liters").message)

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

    def test_a_personal_date_comes_from_memory_or_a_reminder(self) -> None:
        self.say("remember my wife's birthday is june 5")
        self.assertIn("5 June", self.say("when is my wife's birthday")[0].message)
        self.say("add dentist appointment to my calendar next tuesday at 9")
        self.assertIn("You have a reminder for that: dentist appointment",
                      self.say("when is my dentist appointment")[0].message)
        self.assertIn("haven't told me", self.say("when is my anniversary")[0].message)

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
        self.assertEqual(planner.plan("when is thanksgiving?", "", {}).command, "when is thanksgiving")
        self.assertEqual(planner.plan("what day is it", "", {}).command, "time what day is it")


if __name__ == "__main__":
    unittest.main()
