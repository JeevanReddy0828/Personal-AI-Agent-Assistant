"""The laptop's own controls: lock, sleep, power, brightness, wallpaper, processes, Settings.

Every test drives a recording backend. The harness switches the real one off (`desktop_guard`),
so nothing here can lock, dim or shut down this laptop.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools import system
from laptop_agent.tools.system import SystemTool, settings_page

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")


class FakeSystem:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.level: int | None = 50
        self.paper = r"C:\Windows\Web\old.jpg"
        self.running = [{"name": "chrome.exe", "pid": 10, "memory_mb": 900},
                        {"name": "chrome.exe", "pid": 11, "memory_mb": 300},
                        {"name": "notepad.exe", "pid": 12, "memory_mb": 20},
                        {"name": "svchost.exe", "pid": 4, "memory_mb": 40}]

    def lock(self) -> None:
        self.calls.append(("lock",))

    def sleep(self) -> None:
        self.calls.append(("sleep",))

    def power(self, action: str) -> None:
        self.calls.append(("power", action))

    def brightness(self) -> int | None:
        return self.level

    def set_brightness(self, percent: int) -> None:
        self.calls.append(("brightness", percent))

    def wallpaper(self) -> str:
        return self.paper

    def set_wallpaper(self, path: str) -> None:
        self.calls.append(("wallpaper", path))
        self.paper = path

    def processes(self) -> list[dict[str, object]]:
        return list(self.running)

    def end_process(self, name: str) -> int:
        self.calls.append(("end", name))
        return 2

    def open_uri(self, uri: str) -> None:
        self.calls.append(("open", uri))


class SystemToolTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.backend = FakeSystem()
        self.cards: list = []
        self.approve = True

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        self.tool = SystemTool(ApprovalGate(ask=ask), self.backend, self.root)

    def risk_of(self, action) -> RiskLevel:
        self.cards.clear()
        action()
        return self.cards[0].risk

    def test_each_control_keeps_the_risk_its_effect_has(self) -> None:
        self.assertIs(self.risk_of(self.tool.lock), RiskLevel.MEDIUM)
        self.assertIs(self.risk_of(lambda: self.tool.brightness("30")), RiskLevel.MEDIUM)
        self.assertIs(self.risk_of(self.tool.sleep), RiskLevel.HIGH)
        self.assertIs(self.risk_of(lambda: self.tool.power("shutdown")), RiskLevel.CRITICAL)
        self.assertIs(self.risk_of(lambda: self.tool.power("restart")), RiskLevel.CRITICAL)
        self.assertIs(self.risk_of(lambda: self.tool.end_process("notepad")), RiskLevel.CRITICAL)

    def test_a_denied_shutdown_does_nothing(self) -> None:
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool.power("shutdown")
        self.assertEqual(self.backend.calls, [])

    def test_cancelling_a_shutdown_asks_nothing(self) -> None:
        self.assertTrue(self.tool.power("cancel").ok)
        self.assertEqual((self.cards, self.backend.calls), ([], [("power", "cancel")]))

    def test_brightness_steps_and_stays_in_range(self) -> None:
        self.backend.level = 90
        self.tool.brightness("up")
        self.backend.level = 10
        self.tool.brightness("down")
        self.tool.brightness("250%")
        self.assertEqual(self.backend.calls, [("brightness", 100), ("brightness", 0), ("brightness", 100)])
        self.assertIn("50%", SystemTool(ApprovalGate(ask=lambda r: True), FakeSystem(), self.root).brightness().message)

    def test_a_screen_that_reports_no_brightness_says_so(self) -> None:
        self.backend.level = None
        self.assertFalse(self.tool.brightness("up").ok)
        self.assertFalse(self.tool.brightness().ok)
        self.assertEqual(self.backend.calls, [])

    def test_a_wallpaper_needs_a_real_picture_and_can_be_put_back(self) -> None:
        for words in ("a beach photo", "blue", "beach.jpg", str(self.root / "missing.jpg"), str(self.root / "notes.txt")):
            with self.subTest(words):
                self.assertFalse(self.tool.set_wallpaper(words).ok)
        self.assertEqual((self.cards, self.backend.calls), ([], []))
        old, picture = self.root / "old.jpg", self.root / "beach.jpg"
        old.write_bytes(b"jpg")
        picture.write_bytes(b"jpg")
        self.backend.paper = str(old)
        self.assertTrue(self.tool.set_wallpaper(f'"{picture}"').ok)
        self.assertIs(self.cards[0].risk, RiskLevel.HIGH)
        self.assertTrue(self.tool.set_wallpaper("restore").ok)
        self.assertEqual(self.backend.calls, [("wallpaper", str(picture)), ("wallpaper", str(old))])

    def test_restore_with_nothing_changed_says_so(self) -> None:
        self.assertIn("nothing to put back", self.tool.set_wallpaper("restore").message)

    def test_processes_are_grouped_by_name_largest_first(self) -> None:
        result = self.tool.processes()
        self.assertEqual([row["name"] for row in result.data["processes"]], ["chrome.exe", "svchost.exe", "notepad.exe"])
        self.assertIn("chrome.exe ×2: 1,200 MB", result.message)

    def test_windows_own_processes_and_this_one_are_never_ended(self) -> None:
        # Listed as running, so only the guards themselves can refuse them.
        self.backend.running += [{"name": Path(sys.executable).name, "pid": 20, "memory_mb": 80},
                                 {"name": "notepad & del *.exe", "pid": 21, "memory_mb": 1}]
        for name in ("svchost", "explorer.exe", "lsass", Path(sys.executable).stem):
            with self.subTest(name):
                self.assertFalse(self.tool.end_process(name).ok)
        self.assertFalse(self.tool.end_process("spotify").ok, "nothing by that name is running")
        self.assertFalse(self.tool.end_process("notepad & del *").ok)
        self.assertEqual((self.cards, self.backend.calls), ([], []))
        self.assertTrue(self.tool.end_process("notepad").ok)
        self.assertEqual(self.backend.calls, [("end", "notepad.exe")])

    def test_settings_pages_are_named_or_refused(self) -> None:
        self.assertEqual(settings_page("bluetooth"), "bluetooth")
        self.assertEqual(settings_page("wi-fi settings"), "network-wifi")
        self.assertIsNone(settings_page("rocket"))
        self.assertTrue(self.tool.open_settings("bluetooth").ok)
        self.assertFalse(self.tool.open_settings("rocket").ok)
        self.assertEqual(self.backend.calls, [("open", "ms-settings:bluetooth")])

    def test_without_a_backend_every_control_says_windows_only(self) -> None:
        tool = SystemTool(ApprovalGate(ask=lambda r: True), None, self.root)
        for result in (tool.lock(), tool.sleep(), tool.power("shutdown"), tool.brightness("up"), tool.processes(),
                       tool.set_wallpaper("x"), tool.end_process("x"), tool.open_settings("x")):
            self.assertFalse(result.ok)
            self.assertIn("Windows only", result.message)

    def test_the_harness_never_reaches_the_real_controls(self) -> None:
        self.assertIsNone(system.system_backend())


class SystemCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        self.backend = FakeSystem()
        self.orch._system_tool_cache = SystemTool(self.orch.context.web.approval_gate, self.backend, Path(temp.name))

    def say(self, text: str):
        return asyncio.run(self.orch.handle(text, history=[]))

    def test_spoken_controls_reach_the_tool(self) -> None:
        for text in ("lock my computer", "turn the brightness down", "open bluetooth settings"):
            with self.subTest(text):
                self.assertTrue(self.say(text).ok, text)
        self.assertEqual(self.backend.calls, [("lock",), ("brightness", 30), ("open", "ms-settings:bluetooth")])

    def test_a_wallpaper_without_a_picture_asks_for_one_instead_of_drawing_it(self) -> None:
        result = self.say("set my wallpaper to a beach photo")
        self.assertFalse(result.ok)
        self.assertIn("Which picture?", result.message)
        self.assertEqual(self.backend.calls, [])

    def test_every_control_is_developer_only(self) -> None:
        with acting_as(PERSONAL):
            for command in ("lock screen", "power sleep", "power shutdown", "power restart", "power cancel",
                            "brightness", "brightness up", "wallpaper x", "processes", "end process x",
                            "settings", "settings bluetooth"):
                with self.subTest(command):
                    self.assertIsNotNone(refused_command(command))
            self.assertFalse(self.say("lock my computer").ok)
        self.assertEqual(self.backend.calls, [])


class SystemRoutingTests(unittest.TestCase):
    ROUTED = {
        "lock my computer": "lock screen",
        "put my laptop to sleep": "power sleep",
        "shut down my computer": "power shutdown",
        "restart my pc": "power restart",
        "cancel the shutdown": "power cancel",
        "set brightness to 40": "brightness 40",
        "turn up the brightness": "brightness up",
        "dim my screen brightness": "brightness down",
        "make the screen brighter": "brightness up",
        "what's my brightness": "brightness",
        "change my desktop background to blue": "wallpaper blue",
        "restore my old wallpaper": "wallpaper restore",
        "what's using all my memory": "processes",
        "kill the chrome process": "end process chrome",
        "force quit spotify": "end process spotify",
        "open display settings": "settings display",
    }
    STAY = ("lock it", "go to sleep", "i need to shut down for the night", "how do I restart my pc",
            "my screen is too bright", "what is a process", "end the meeting", "the brightness is fine",
            "restart the timer", "turn off the lights", "open the settings app on my phone")

    def test_controls_are_routed(self) -> None:
        planner = HeuristicPlannerProvider()
        for text, command in self.ROUTED.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_near_misses_stay_with_the_chat(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in self.STAY:
            with self.subTest(text):
                command = planner.plan(text, "", {}).command or ""
                self.assertFalse(command.startswith(("lock", "power", "brightness", "wallpaper", "processes",
                                                     "end process", "settings")), f"{text} -> {command}")


if __name__ == "__main__":
    unittest.main()
