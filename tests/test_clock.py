from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from laptop_agent.tools.clock import ClockTool, asks_the_time, prompt_stamp

# A fixed moment so none of this is a clock race: 6:26 PM on 11 September 2026,
# US Eastern, which is on EDT (UTC-4) that day. This is the exact moment the reported
# failure happened.
EASTERN_SUMMER = timezone(timedelta(hours=-4), "EDT")
MOMENT = datetime(2026, 9, 11, 18, 26, 27, tzinfo=EASTERN_SUMMER)


def clock() -> ClockTool:
    return ClockTool(now=lambda: MOMENT)


class MissingZoneDataTests(unittest.TestCase):
    """Without `tzdata` the zone lookup raises, and the failure is meant to say so and
    still give the local time. It used `%-I`, which is a glibc extension: on Windows
    strftime raises `ValueError: Invalid format string`, so the message explaining how to
    fix a missing time zone database crashed on the one platform where it is usually
    missing — including a packaged JARVIS.exe. `_describe` already carries a comment
    saying %-d is not portable and to strip the zero by hand; this line had the
    `.lstrip('0')` and the `-` both."""

    def test_the_message_renders_when_the_zone_database_is_missing(self) -> None:
        from unittest.mock import patch

        def no_tzdata(name):
            raise ModuleNotFoundError("No module named 'tzdata'")

        with patch("laptop_agent.tools.clock._load_zone", no_tzdata):
            result = clock().now("time in Asia/Kolkata")

        self.assertFalse(result.ok, "a missing zone database should not look like success")
        self.assertIn("tzdata", result.message, "the message does not say what to install")
        self.assertIn("Asia/Kolkata", result.message, "the message does not name the zone")
        # The whole point of the fallback: it still tells you the local time.
        self.assertIn("6:26 PM", result.message, "the local time was dropped from the message")
        self.assertNotIn("%", result.message, "a format directive leaked into the message")


class LocalTimeTests(unittest.TestCase):
    """Asked "what is the current date and time in EST", the assistant web-searched,
    scraped a stale page and answered 1:00 PM while the machine's clock read 6:26 PM.
    Told it was wrong, it explained 24-hour clocks and cited a source that does not
    exist. The time is in the operating system, not on the web."""

    def test_local_time_comes_from_the_machine(self) -> None:
        result = clock().now("")
        self.assertTrue(result.ok)
        self.assertIn("6:26 PM", result.message)
        self.assertIn("Friday, 11 September 2026", result.message)

    def test_the_data_carries_a_machine_readable_moment(self) -> None:
        data = clock().now("").data
        self.assertTrue(str(data["iso"]).startswith("2026-09-11T18:26"))
        self.assertEqual(data["day_of_week"], "Friday")
        self.assertEqual(data["offset"], "UTC-04:00")


class ZoneTests(unittest.TestCase):
    def test_a_named_zone_is_converted(self) -> None:
        result = clock().now("time in tokyo")
        self.assertTrue(result.ok)
        # 18:26 EDT on the 11th is 07:26 JST on the 12th.
        self.assertIn("7:26 AM", result.message)
        self.assertIn("Saturday, 12 September 2026", result.message)

    def test_the_local_time_is_also_shown_when_zones_differ(self) -> None:
        self.assertIn("6:26 PM", clock().now("time in tokyo").message)

    def test_asking_for_est_in_september_gets_edt_and_is_told_why(self) -> None:
        # The reported question. The right answer is Eastern time, which is on EDT.
        result = clock().now("what time is it in EST")
        self.assertTrue(result.ok)
        self.assertIn("6:26 PM", result.message)
        self.assertIn("EDT", result.message)
        self.assertIn("You asked for EST", result.message)

    def test_an_iana_name_works_directly(self) -> None:
        self.assertIn("3:56 AM", clock().now("time in Asia/Kolkata").message)

    def test_states_countries_and_any_city_the_zone_database_names(self) -> None:
        # "what time is it in california" was refused as an unknown zone.
        self.assertIn("America/Los_Angeles", clock().now("what time is it in california").message)
        self.assertIn("Asia/Seoul", clock().now("time in south korea").message)
        # In no table here: found by the zone database's own city names.
        self.assertIn("Africa/Nairobi", clock().now("what time is it in nairobi").message)
        self.assertIn("Asia/Kathmandu", clock().now("time in kathmandu right now").message)

    def test_an_unknown_zone_is_refused_rather_than_guessed(self) -> None:
        # "Factory" is a zone-database placeholder, not a place; a city is always Area/City.
        self.assertFalse(clock().now("time in factory").ok)
        result = clock().now("time in narnia")
        self.assertFalse(result.ok)
        self.assertIn("narnia", result.message)
        # A wrong time stated confidently is the failure this replaces.
        self.assertNotIn("6:26 PM", result.message)


class BetweenZonesTests(unittest.TestCase):
    """"convert 9am pst to ist" and "how many hours ahead is tokyo" went to the chat model,
    which has one right answer to compute and daylight saving to get wrong. They are read
    from the zone database, on the fixed moment above (11 September, Eastern on EDT)."""

    def say(self, text: str, moment: datetime = MOMENT) -> str:
        result = ClockTool(now=lambda: moment).now(text)
        self.assertTrue(result.ok, result.message)
        return result.message

    def test_a_time_in_one_zone_read_in_another(self) -> None:
        message = self.say("convert 9am pst to ist")
        self.assertIn("**9:00 AM PDT** in `America/Los_Angeles` is **9:30 PM IST** in `Asia/Kolkata`.", message)
        self.assertIn("You asked for PST, but that zone is on **PDT**", message)
        self.assertIn("**3:00 PM BST** in `Europe/London` is **10:00 AM EDT** in `America/New_York`.",
                      self.say("what's 3pm london time in new york?"))
        # Sydney is on AEST until October: 11pm in London is 8am the next day there.
        self.assertIn("**8:00 AM AEST** the next day in `Australia/Sydney`",
                      self.say("what time is it in sydney when it's 11pm in london"))
        self.assertIn("**9:30 PM EDT** here is **10:30 AM JST** the next day in `Asia/Tokyo`",
                      self.say("what time will it be in tokyo at 9:30 pm my time"))

    def test_daylight_saving_is_read_for_the_day_not_assumed(self) -> None:
        january = datetime(2026, 1, 15, 12, 0, tzinfo=timezone(timedelta(hours=-5), "EST"))
        self.assertIn("is **2:00 PM UTC**", self.say("convert 9am est to utc", january))
        self.assertIn("is **1:00 PM UTC**", self.say("convert 9am est to utc"))     # September: EDT

    def test_how_far_apart_two_zones_are(self) -> None:
        self.assertIn("`Asia/Tokyo` is **13 hours ahead of** you right now", self.say("how many hours ahead is tokyo"))
        self.assertIn("`Asia/Kolkata` is **9 hours 30 minutes ahead of** you", self.say("how far ahead is india"))
        self.assertIn("`America/Los_Angeles` is **3 hours behind** you", self.say("how many hours behind is la"))
        self.assertIn("`Europe/London` is **5 hours ahead of** `America/New_York`",
                      self.say("what is the time difference between london and new york"))

    def test_only_these_shapes_with_known_zones(self) -> None:
        from laptop_agent.tools.clock import zone_question

        for text in ("what is 5 in binary", "convert 9 pst to ist", "what's 3pm in my calendar",
                     "convert 10:30 to minutes", "how far ahead is the project",
                     "how many hours behind is the deadline", "convert 13pm pst to ist", "9am pst to pacific"):
            with self.subTest(text):
                self.assertIsNone(zone_question(text))


class RoutingTests(unittest.TestCase):
    """The guard has to catch clock questions without stealing anything else."""

    def test_clock_questions_are_recognised(self) -> None:
        for text in (
            "what time is it",
            "what is the current date and time in EST",
            "time",
            "date",
            "what day is it",
            "current time",
            "what time is it in tokyo",
            "time in IST",
        ):
            self.assertTrue(asks_the_time(text), text)

    def test_everything_else_is_left_alone(self) -> None:
        for text in (
            "schedule daily at 08:00 :: briefing",
            "remind me to call at 5pm",
            "what time is the meeting",
            "time to refactor this",
            "how long did that take",
            "latency",
            "what is a csv file",
            "add a timestamp to each message",
        ):
            self.assertFalse(asks_the_time(text), text)

    def test_a_sentence_that_only_starts_with_the_word_is_not_a_clock_question(self) -> None:
        # The pattern stopped at the time word, so these read the clock - and "time for a
        # break" failed with "I do not know the time zone 'a break'".
        for text in ("time management tips", "date night ideas", "time for a break",
                     "day trip ideas", "time flies"):
            self.assertFalse(asks_the_time(text), text)
        for text in ("what's the time?", "what day is it today", "time in london right now",
                     "whats the time please", "could you please tell me what time it is",
                     "do you know what time it is", "wat time is it"):
            self.assertTrue(asks_the_time(text), text)


class PromptStampTests(unittest.TestCase):
    def test_the_prompt_names_the_moment_and_forbids_searching_for_it(self) -> None:
        stamp = prompt_stamp(now=lambda: MOMENT)
        self.assertIn("Friday, 11 September 2026", stamp)
        self.assertIn("6:26 PM", stamp)
        self.assertIn("never search the web for the current time", stamp)

    def test_the_chat_prompt_actually_carries_it(self) -> None:
        captured: list[dict] = []

        from laptop_agent.planner.openai_compatible import OpenAICompatiblePlannerProvider

        provider = OpenAICompatiblePlannerProvider(
            "k", "m", base_url="https://example.invalid/v1",
            transport=lambda payload: captured.append(payload) or "ok",
        )
        provider.answer("hello", {})
        system = captured[0]["messages"][0]["content"]
        self.assertIn("current date and time on this computer", system)


class RenderingTests(unittest.TestCase):
    def test_the_zone_name_is_not_wrapped_in_italics(self) -> None:
        # An IANA name contains an underscore, so _America/New_York_ rendered in the
        # chat with its underscores showing.
        message = clock().now("time in tokyo").message
        self.assertIn("`Asia/Tokyo`", message)
        self.assertNotIn("_Asia/Tokyo_", message)


if __name__ == "__main__":
    unittest.main()


class TimeCardTests(unittest.TestCase):
    """Reported: "can you generate an image with the current time on it?" web-searched
    (sources about building a Jarvis), asked for approval twice, and then printed its own
    tool JSON to the user. Four separate faults, and underneath them a real one: a
    diffusion model cannot spell, so the time on a generated clock is never the time."""

    def test_a_text_image_request_is_recognised(self) -> None:
        from laptop_agent.tools.textcard import wants_text_rendered

        for subject in (
            "the current time on it",
            "a digital clock showing the time",
            "an image with the current date",
            "a clock display with the time",
        ):
            self.assertTrue(wants_text_rendered(subject), subject)

    def test_an_ordinary_picture_is_left_to_the_image_model(self) -> None:
        from laptop_agent.tools.textcard import wants_text_rendered

        for subject in ("a red fox in snow", "a mountain lake at dawn", "a cat wearing a hat"):
            self.assertFalse(wants_text_rendered(subject), subject)

    def test_the_card_draws_the_lines_given(self) -> None:
        import tempfile
        from pathlib import Path

        from laptop_agent.tools.textcard import render_card

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "card.png"
            result = render_card(["6:26 PM", "Friday, 11 September 2026", "EDT UTC-04:00"], target)
            if not result.ok:
                self.skipTest("Pillow is not installed here: " + result.message)
            self.assertTrue(target.exists())
            self.assertGreater(target.stat().st_size, 1500)
            self.assertEqual(result.data["width"], 1200)

    def test_an_empty_card_is_refused(self) -> None:
        import tempfile
        from pathlib import Path

        from laptop_agent.tools.textcard import render_card

        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(render_card([], Path(tmp) / "x.png").ok)


class BackReferenceSubjectTests(unittest.TestCase):
    """The router invents a subject every time, so the test for "is this a back-reference"
    has to read the user's own words, not the router's output."""

    def test_a_request_that_names_its_subject_is_not_a_back_reference(self) -> None:
        from laptop_agent.agents.orchestrator import _has_own_subject

        self.assertTrue(_has_own_subject("can you generate an image with the current time on it?"))
        self.assertTrue(_has_own_subject("draw a red fox in snow"))

    def test_a_request_that_only_points_is(self) -> None:
        from laptop_agent.agents.orchestrator import _has_own_subject

        for text in ("create an image for this", "draw me a picture of this",
                     "make another one", "image it", "same again please"):
            self.assertFalse(_has_own_subject(text), text)
