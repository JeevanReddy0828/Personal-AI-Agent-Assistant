"""Keys and typing sent to another app, through a recording backend: nothing reaches this desktop."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools import keys
from laptop_agent.tools.keys import MAX_TYPED, KeysTool
from laptop_agent.tools.windows import DesktopWindow

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")
JARVIS = DesktopWindow(1, "J.A.R.V.I.S", "msedgewebview2.exe")
NOTEPAD = DesktopWindow(2, "notes.txt - Notepad", "notepad.exe")
CHROME = DesktopWindow(3, "Inbox - Gmail - Google Chrome", "chrome.exe")


class FakeKeys:
    def __init__(self) -> None:
        self.open = [JARVIS, NOTEPAD, CHROME]
        self.focused: list[str] = []
        self.pressed: list[tuple[str, ...]] = []
        self.typed: list[str] = []
        self.focus_ok = True

    def windows(self) -> list[DesktopWindow]:
        return list(self.open)

    def focus(self, window: DesktopWindow) -> bool:
        self.focused.append(window.title)
        return self.focus_ok

    def press(self, combo: tuple[str, ...]) -> None:
        self.pressed.append(combo)

    def type_text(self, text: str) -> None:
        self.typed.append(text)


class KeysToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.backend = FakeKeys()
        self.cards: list = []
        self.approve = True

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        self.tool = KeysTool(ApprovalGate(ask=ask), self.backend)

    def test_unnamed_goes_to_the_window_the_user_was_in_never_ours(self) -> None:
        result = self.tool.shortcut("new tab")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.backend.focused, [NOTEPAD.title])
        self.assertEqual(self.backend.pressed, [("ctrl", "t")])
        self.assertIn("Notepad", result.message)

    def test_a_named_app_is_found_by_title_or_program(self) -> None:
        self.tool.shortcut("back", "chrome")
        self.assertEqual((self.backend.focused, self.backend.pressed), ([CHROME.title], [("alt", "left")]))

    def test_an_app_not_open_or_a_window_that_will_not_come_forward_presses_nothing(self) -> None:
        self.assertFalse(self.tool.shortcut("new tab", "spotify").ok)
        self.backend.focus_ok = False
        self.assertFalse(self.tool.shortcut("new tab").ok)
        self.assertEqual(self.backend.pressed, [])

    def test_only_jarvis_open_has_nothing_to_send_to(self) -> None:
        self.backend.open = [JARVIS]
        self.assertFalse(self.tool.shortcut("scroll down").ok)
        self.assertEqual(self.backend.focused, [])

    def test_losing_work_and_typing_ask_first_the_rest_run(self) -> None:
        for action, risk in (("new tab", RiskLevel.MEDIUM), ("close window", RiskLevel.HIGH), ("enter", RiskLevel.HIGH)):
            self.cards.clear()
            self.tool.shortcut(action)
            self.assertIs(self.cards[0].risk, risk, action)
        self.cards.clear()
        self.tool.type_text("hello")
        self.assertIs(self.cards[0].risk, RiskLevel.HIGH)
        self.assertEqual(self.cards[0].preview, "hello")

    def test_a_denied_card_presses_nothing(self) -> None:
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool.type_text("rm -rf", "notepad")
        self.assertEqual((self.backend.focused, self.backend.typed), ([], []))

    def test_typing_is_one_line_and_bounded(self) -> None:
        self.tool.type_text("line one\nline two\r\n")
        self.assertEqual(self.backend.typed, ["line one line two"])
        self.assertFalse(self.tool.type_text("x" * (MAX_TYPED + 1)).ok)
        self.assertFalse(self.tool.type_text("  \n ").ok)

    def test_an_unknown_action_names_the_known_ones(self) -> None:
        result = self.tool.shortcut("launch missiles")
        self.assertFalse(result.ok)
        self.assertIn("new tab", result.message)
        self.assertEqual(self.cards, [])

    def test_without_a_backend_it_says_windows_only(self) -> None:
        self.assertIn("Windows only", KeysTool(ApprovalGate(ask=lambda r: True), None).shortcut("new tab").message)

    def test_the_harness_never_reaches_the_real_keyboard(self) -> None:
        self.assertIsNone(keys.keys_backend())


class KeysCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        self.backend = FakeKeys()
        self.orch._keys_cache = KeysTool(self.orch.context.web.approval_gate, self.backend)

    def say(self, text: str):
        return asyncio.run(self.orch.handle(text, history=[]))

    def test_spoken_keys_and_typing_reach_the_named_app(self) -> None:
        self.assertTrue(self.say("new tab in chrome").ok)
        self.assertTrue(self.say("type hello there in notepad").ok)
        self.assertEqual(self.backend.pressed, [("ctrl", "t")])
        self.assertEqual(self.backend.focused, [CHROME.title, NOTEPAD.title])
        self.assertEqual(self.backend.typed, ["hello there"])

    def test_keys_are_developer_only(self) -> None:
        with acting_as(PERSONAL):
            self.assertEqual(refused_command("keys new tab"), "keys")
            self.assertEqual(refused_command("keys type :: hi"), "keys")
            self.assertFalse(self.say("new tab").ok)
        self.assertEqual(self.backend.pressed, [])


class KeysRoutingTests(unittest.TestCase):
    ROUTED = {
        "new tab": "keys new tab", "close this tab": "keys close tab", "go back": "keys back",
        "refresh the page": "keys refresh", "scroll to the bottom": "keys bottom", "paste it": "keys paste",
        "scroll down in edge": "keys scroll down in edge", "minimize this window": "keys minimize",
        'type "hello world"': "keys type :: hello world", "type hello in notepad": "keys type notepad :: hello",
    }
    STAY = ("type 2 diabetes symptoms", "go back to sleep", "I need to go back home", "save the planet",
            "scroll down the list of options for me", "what does select all do", "zoom in on the map of paris")

    def test_key_requests_are_routed(self) -> None:
        planner = HeuristicPlannerProvider()
        for text, command in self.ROUTED.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_near_misses_press_nothing(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in self.STAY:
            with self.subTest(text):
                self.assertFalse((planner.plan(text, "", {}).command or "").startswith("keys"), text)


if __name__ == "__main__":
    unittest.main()
