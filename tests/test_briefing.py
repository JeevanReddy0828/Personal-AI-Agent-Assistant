from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from laptop_agent.access import acting_as
from laptop_agent.accounts import Principal
from laptop_agent.briefing import Section, describe_format, news_lines, parse_format
from laptop_agent.cancellation import OperationCancelled
from laptop_agent.failures import FAILURES
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.news import NewsTool
from laptop_agent.tools.weather import WeatherTool
from test_everyday_requests import Everyday
from test_news import FEED
from test_weather import FORECAST, GEO


class FormatTests(unittest.TestCase):
    def test_a_format_is_read_the_way_it_is_said(self) -> None:
        cases = {
            "weather in Boston, AI news, my shopping list and reminders": [
                Section("weather", "Boston"), Section("news", "AI"), Section("list", "shopping"), Section("reminders")],
            "the news about nvidia then my timers": [Section("news", "nvidia"), Section("reminders")],
            "to have today's top headlines plus my inbox": [Section("news"), Section("mail")],
            "world news & the forecast for Paris": [Section("news"), Section("weather", "Paris")],
            "laptop status, unread email, the to-do list": [Section("laptop"), Section("mail"), Section("list", "to-do")],
            "my reminders list": [Section("reminders")],
        }
        for text, expected in cases.items():
            with self.subTest(text):
                self.assertEqual(parse_format(text), (expected, []))

    def test_what_it_cannot_read_is_returned_not_dropped(self) -> None:
        self.assertEqual(parse_format("weather, stocks and my calendar"),
                         ([Section("weather")], ["stocks", "my calendar"]))

    def test_each_section_once_in_the_order_first_said(self) -> None:
        sections, _ = parse_format("news, weather, news, reminders, weather")
        self.assertEqual(sections, [Section("news"), Section("weather"), Section("reminders")])

    def test_the_saved_text_reads_back_as_the_same_sections(self) -> None:
        sections, _ = parse_format("weather in Boston, AI news, shopping list, reminders, mail, laptop")
        self.assertEqual(parse_format(describe_format(sections)), (sections, []))

    def test_a_google_title_does_not_repeat_its_source(self) -> None:
        self.assertEqual(news_lines([{"title": "Deal - The New York Times", "url": "https://x", "source": "The New York Times"}]),
                         ["1. [Deal](https://x) — The New York Times"])


class BriefingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.everyday = Everyday(Path(self._tmp.name), llm="none")
        self.orchestrator = self.everyday.orchestrator
        self.places: list[str] = []
        self.topics: list[str] = []
        gate = self.orchestrator.context.web.approval_gate

        def weather(url: str):
            if "geocoding" in url:
                self.places.append(urllib.parse.unquote(url.split("name=")[1].split("&")[0]))
                return GEO
            return FORECAST

        def news(url: str):
            if "search?q=" in url:
                self.topics.append(urllib.parse.unquote(url.split("q=")[1].split("&")[0]))
            return FEED

        self.orchestrator._weather_tool_cache = WeatherTool(transport=weather, approval_gate=gate)
        self.orchestrator._news_tool_cache = NewsTool(backend=news, page_reader=lambda url: "", approval_gate=gate)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def say(self, text: str):
        return self.everyday.say(text)

    def test_the_default_briefing_reads_the_day(self) -> None:
        reminders = self.orchestrator.context.reminders
        now = datetime.now().astimezone()
        reminders.add((now - timedelta(minutes=5)).isoformat(), "Call Alex")
        self.say("set a timer for 10 minutes")
        self.say("remember my city is Austin")
        result, ran = self.say("what's my morning briefing")
        self.assertEqual(ran, "briefing")
        message = result.message
        self.assertIn("- Due: Call Alex", message)
        self.assertRegex(message, r"- Timer: .+ left")
        self.assertIn("**Weather** — Austin, Texas, United States: mainly clear, 92°F. Today 97° / 74°F, 5% chance of rain.",
                      message)
        self.assertIn("**News**\n1. [Houthis seize Red Sea port of Mokha]", message)
        self.assertIn("**Laptop** — CPU", message)
        self.assertNotIn("**Mail**", message, "mail is in the default only when an inbox is connected")
        self.assertEqual(self.places, ["Austin"])
        self.assertLess(message.index("**Reminders**"), message.index("**Weather**"))

    def test_a_reminder_later_today_and_one_next_week(self) -> None:
        now = datetime.now().astimezone()
        later = now + timedelta(seconds=30)
        if later.date() != now.date():
            self.skipTest("too close to midnight for 'later today'")
        reminders = self.orchestrator.context.reminders
        reminders.add((now + timedelta(days=3)).isoformat(), "Pay rent")
        quiet = self.orchestrator._briefing().message
        self.assertIn("- Nothing due today. Next: Pay rent,", quiet)
        reminders.add(later.isoformat(), "Dentist")
        busy = self.orchestrator._briefing().message
        self.assertIn("- Dentist — today at", busy)
        self.assertNotIn("Pay rent", busy)

    def test_the_users_format_is_saved_and_followed_in_order(self) -> None:
        self.say("add milk and eggs to my shopping list")
        changed, ran = self.say("set my briefing to weather in Boston, AI news, my shopping list and reminders")
        self.assertEqual(ran, "briefing set weather in Boston, AI news, my shopping list and reminders")
        self.assertIn("Your briefing is now: weather in Boston, news about AI, shopping list, reminders.", changed.message)
        self.assertEqual(self.orchestrator.context.memory.get_profile()["briefing"],
                         "weather in Boston, news about AI, shopping list, reminders")
        message = self.say("brief me")[0].message
        order = [message.index(label) for label in ("**Weather**", "**News about AI**", "**Shopping list** — milk, eggs.",
                                                    "**Reminders**")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("**Laptop**", message)
        self.assertNotIn("Change what's in it", message)
        self.assertEqual((self.places, self.topics), (["Boston"], ["AI"]))

    def test_a_format_joined_by_and_is_one_request(self) -> None:
        # Split at "and", "set my briefing to weather" ran and then the news was read out.
        result, ran = self.say("set my briefing to weather and news")
        self.assertEqual(ran, "briefing set weather and news")
        self.assertIn("Your briefing is now: weather, news.", result.message)
        # A briefing beside another request is still two requests.
        both, ran = self.say("give me my briefing and set a timer for 10 minutes")
        self.assertEqual(ran, "briefing ;; timer set a timer for 10 minutes")

    def test_add_remove_show_and_reset(self) -> None:
        self.assertIn("Your briefing is the default: reminders, weather, news, laptop.",
                      self.say("what's in my briefing?")[0].message)
        self.assertIn("now: reminders, weather, news, laptop, mail.", self.say("add mail to my briefing")[0].message)
        self.assertIn("now: reminders, weather, laptop, mail.", self.say("take the news out of my briefing")[0].message)
        self.assertIn("Your briefing has: reminders, weather, laptop, mail.", self.say("show my briefing settings")[0].message)
        missing = self.say("remove the news from my briefing")[0]
        self.assertFalse(missing.ok)
        self.assertIn("has no news in it", missing.message)
        self.assertIn("back to the default: reminders, weather, news, laptop.", self.say("reset my briefing")[0].message)
        self.assertNotIn("briefing", self.orchestrator.context.memory.get_profile())

    def test_a_part_it_cannot_read_saves_nothing(self) -> None:
        self.say("set my briefing to weather and news")
        refused, ran = self.say("set my briefing to stocks and weather")
        self.assertEqual(ran, "briefing set stocks and weather")
        self.assertFalse(refused.ok)
        self.assertIn("I can't put 'stocks' in a briefing. It can include reminders", refused.message)
        self.assertEqual(self.orchestrator.context.memory.get_profile()["briefing"], "weather, news")
        emptied = self.say("briefing remove weather and news")[0]
        self.assertFalse(emptied.ok)
        self.assertIn("would leave your briefing empty", emptied.message)

    def test_mail_says_how_to_connect_and_joins_the_default_once_connected(self) -> None:
        self.say("set my briefing to mail and reminders")
        self.assertIn("**Mail** — not connected yet.", self.say("briefing")[0].message)
        self.say("reset my briefing")
        email = self.orchestrator.context.email
        inbox = ToolResult.success("Found 2.", messages=[{"from": "Ana <ana@x.com>", "subject": "Lunch?"},
                                                        {"from": "bank@x.com", "subject": ""}])
        with patch.object(email, "config", SimpleNamespace(imap_host="h", imap_username="u", imap_password="p")), \
                patch.object(email, "search_inbox", return_value=inbox):
            message = self.say("briefing")[0].message
        self.assertIn("**Mail**\n2 unread. Newest:\n- Ana: Lunch?\n- bank@x.com: (no subject)", message)
        self.assertLess(message.index("**Reminders**"), message.index("**Mail**"))

    def test_the_network_sections_are_fetched_at_once(self) -> None:
        news_started = threading.Event()
        waited: list[bool] = []
        gate = self.orchestrator.context.web.approval_gate

        def weather(url: str):
            waited.append(news_started.wait(2))   # one after another, the news has not started
            return GEO if "geocoding" in url else FORECAST

        def news(url: str):
            news_started.set()
            return FEED

        self.orchestrator._weather_tool_cache = WeatherTool(transport=weather, approval_gate=gate)
        self.orchestrator._news_tool_cache = NewsTool(backend=news, page_reader=lambda url: "", approval_gate=gate)
        self.say("set my briefing to weather in Boston and news")
        self.orchestrator._briefing()
        self.assertTrue(waited and all(waited), "the weather waited for the news: fetched one after another")

    def test_a_failing_section_is_one_line_and_a_stop_still_stops(self) -> None:
        gate = self.orchestrator.context.web.approval_gate

        def broken(url: str):
            raise ValueError("feed exploded")

        self.orchestrator._news_tool_cache = NewsTool(backend=lambda url: FEED, page_reader=lambda url: "",
                                                      approval_gate=gate)
        self.orchestrator._weather_tool_cache = WeatherTool(transport=broken, approval_gate=gate)
        self.say("set my briefing to weather in Boston and news")
        before = len(FAILURES.recent(200))
        with patch.object(WeatherTool, "forecast", side_effect=ValueError("weather exploded")):
            result = self.orchestrator._briefing()
        self.assertTrue(result.ok)
        self.assertIn("**Weather** — couldn't check just now.", result.message)
        # The tool's own failure carries the exception text, which voice would read out.
        with patch.object(WeatherTool, "forecast", return_value=ToolResult.failure(
                "Couldn't reach the weather service: <urlopen error Tunnel connection failed: 403 Forbidden>")):
            unreached = self.orchestrator._briefing().message
        self.assertIn("**Weather** — couldn't reach the weather service just now.", unreached)
        self.assertNotIn("urlopen", unreached)
        with patch.object(WeatherTool, "forecast", return_value=ToolResult.failure("I couldn't find a place called 'Bostn'.")):
            self.assertIn("I couldn't find a place called 'Bostn'.", self.orchestrator._briefing().message)
        self.assertIn("**News**\n1.", result.message)
        self.assertGreater(len(FAILURES.recent(200)), before, "a swallowed failure was not recorded")
        with patch.object(WeatherTool, "forecast", side_effect=OperationCancelled("stopped")):
            with self.assertRaises(OperationCancelled):
                self.orchestrator._briefing()

    def test_brief_me_on_something_is_not_the_briefing(self) -> None:
        _result, ran = self.say("brief me on the quarterly plan")
        self.assertNotEqual(ran, "briefing")

    def test_a_personal_account_cannot_change_it(self) -> None:
        with acting_as(Principal("p1", "family", "personal")):
            direct = asyncio.run(self.orchestrator.handle("briefing set weather", _allow_planner=False))
        self.assertIn("needs a developer account", direct.message)
        self.assertNotIn("briefing", self.orchestrator.context.memory.get_profile())


if __name__ == "__main__":
    unittest.main()
