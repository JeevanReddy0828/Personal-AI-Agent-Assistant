"""Links the user finishes: a WhatsApp or Telegram message opened ready to send, and a flights search.

Nothing here sends anything: the message is opened in the app's own share link after a card,
and the user presses send. The browser is a recorder in every test.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools import messaging
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.messaging import MAX_TEXT, MessagingTool, share_link

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")


class MessagingToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cards: list = []
        self.opened: list[str] = []
        self.approve = True

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        self.tool = MessagingTool(ApprovalGate(ask=ask))
        opener = patch.object(messaging.webbrowser, "open", side_effect=lambda url: self.opened.append(url) or True)
        opener.start()
        self.addCleanup(opener.stop)

    def test_the_card_shows_the_message_and_asks_high(self) -> None:
        self.assertTrue(self.tool.prepare("whatsapp", "mom", "I'm running late").ok)
        self.assertIs(self.cards[0].risk, RiskLevel.HIGH)
        self.assertEqual(self.cards[0].preview, "I'm running late")
        self.assertEqual(self.opened, ["https://wa.me/?text=I%27m%20running%20late"])

    def test_a_number_opens_that_chat_and_a_name_asks_for_it(self) -> None:
        to_number = self.tool.prepare("whatsapp", "+1 (555) 123-4567", "on my way")
        self.assertEqual(self.opened[-1], "https://wa.me/15551234567?text=on%20my%20way")
        self.assertIn("press send", to_number.message)
        to_name = self.tool.prepare("whatsapp", "Priya", "happy birthday")
        self.assertIn("Choose Priya's chat", to_name.message)

    def test_telegram_has_its_own_share_link(self) -> None:
        self.tool.prepare("telegram", "", "game moved to 7")
        self.assertEqual(self.opened, [share_link("telegram", "game moved to 7")])
        self.assertTrue(self.opened[0].startswith("https://t.me/share/url?"))

    def test_a_denied_card_opens_nothing(self) -> None:
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool.prepare("whatsapp", "mom", "hi")
        self.assertEqual(self.opened, [])

    def test_empty_long_and_unknown_apps_are_refused_before_a_card(self) -> None:
        for app, text in (("whatsapp", "  "), ("whatsapp", "x" * (MAX_TEXT + 1)), ("signal", "hi")):
            self.assertFalse(self.tool.prepare(app, "", text).ok)
        self.assertEqual((self.cards, self.opened), ([], []))


class LinkCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        self.opened: list[str] = []
        opener = patch.object(messaging.webbrowser, "open", side_effect=lambda url: self.opened.append(url) or True)
        opener.start()
        self.addCleanup(opener.stop)

    def say(self, text: str):
        return asyncio.run(self.orch.handle(text, history=[]))

    def test_a_spoken_message_is_opened_ready_to_send(self) -> None:
        result = self.say("send a whatsapp to mom saying i'm running late")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.opened, ["https://wa.me/?text=i%27m%20running%20late"])

    def test_a_flights_search_leads_with_a_google_flights_link(self) -> None:
        with patch.object(self.orch.context.websearch, "search", return_value=ToolResult.success("Some pages.")):
            result = self.say("flights from austin to tokyo")
        self.assertTrue(result.message.startswith("[Search live fares on Google Flights](https://www.google.com/travel/flights?q="))
        self.assertIn("austin%20to%20tokyo", result.data["flights_link"])

    def test_other_searches_get_no_flights_link(self) -> None:
        with patch.object(self.orch.context.websearch, "search", return_value=ToolResult.success("Some pages.")):
            result = self.say("web search flight attendant salary")
        self.assertNotIn("flights_link", result.data)

    def test_messages_are_developer_only(self) -> None:
        with acting_as(PERSONAL):
            self.assertEqual(refused_command("message whatsapp :: hi"), "message")
            self.assertFalse(self.say("send a whatsapp to mom saying hi").ok)
        self.assertEqual(self.opened, [])


class MessageRoutingTests(unittest.TestCase):
    ROUTED = {
        "send a whatsapp to mom saying i'm running late": "message whatsapp to mom :: i'm running late",
        "send a WhatsApp message to +1 555 123 4567 saying on my way": "message whatsapp to +1 555 123 4567 :: on my way",
        "text john on telegram that the game moved to 7": "message telegram to john :: the game moved to 7",
        "message priya on whatsapp saying happy birthday": "message whatsapp to priya :: happy birthday",
        "write a telegram message: meeting moved": "message telegram :: meeting moved",
    }
    STAY = ("text john", "text john saying i'm late", "send a message to mom", "what is whatsapp", "how do I send a whatsapp message",
            "whatsapp on the left and chrome on the right")

    def test_messages_are_routed(self) -> None:
        planner = HeuristicPlannerProvider()
        for text, command in self.ROUTED.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_near_misses_are_not_messages(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in self.STAY:
            with self.subTest(text):
                self.assertFalse((planner.plan(text, "", {}).command or "").startswith("message "), text)


if __name__ == "__main__":
    unittest.main()
