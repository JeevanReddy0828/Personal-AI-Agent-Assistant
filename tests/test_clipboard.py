"""The clipboard: what was copied, copying a reply, and asking about what was copied.

"summarize what I copied" and "copy that" had no tool: the model router answered from nothing.
Every test runs on a clipboard of its own in memory (`desktop_guard`), never the real one.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.tools import clipboard
from laptop_agent.tools.clipboard import MAX_SHOWN, ClipboardTool, MemoryClipboard
from laptop_agent.tools.translate import TranslateTool

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")


class ClipboardToolTests(unittest.TestCase):
    def test_what_is_copied_is_shown_and_a_long_text_is_cut(self) -> None:
        self.assertIn("meet at 5", ClipboardTool(MemoryClipboard("meet at 5")).show().message)
        long = ClipboardTool(MemoryClipboard("x" * (MAX_SHOWN + 10))).show()
        self.assertIn(f"{MAX_SHOWN + 10:,} characters in all", long.message)

    def test_an_empty_clipboard_says_so(self) -> None:
        result = ClipboardTool(MemoryClipboard("  ")).show()
        self.assertTrue(result.ok)
        self.assertIn("no text", result.message)

    def test_copying_writes_the_text(self) -> None:
        memory = MemoryClipboard()
        self.assertTrue(ClipboardTool(memory).copy("hello").ok)
        self.assertEqual(memory.text, "hello")
        self.assertFalse(ClipboardTool(memory).copy("  ").ok)

    def test_no_clipboard_is_a_failure_with_the_fix(self) -> None:
        for result in (ClipboardTool(None).show(), ClipboardTool(None).copy("x")):
            self.assertFalse(result.ok)
            self.assertIn("xclip", result.message)

    def test_the_harness_never_reaches_the_real_clipboard(self) -> None:
        self.assertIsInstance(clipboard.system_clipboard(), MemoryClipboard)


class ClipboardCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        self.memory = MemoryClipboard("Ship the release on Friday.")
        self.orch._clipboard_tool_cache = ClipboardTool(self.memory)

    def say(self, text: str, history=None):
        return asyncio.run(self.orch.handle(text, history=history or []))

    def test_what_did_i_copy_shows_it(self) -> None:
        result = self.say("what did I copy")
        self.assertTrue(result.ok, result.message)
        self.assertIn("Ship the release on Friday.", result.message)

    def test_copy_that_copies_the_last_reply_without_its_tool_data(self) -> None:
        history = [{"role": "user", "text": "a greeting please"},
                   {"role": "assistant", "text": "Good evening, everyone.\n[tool result data, context only - "
                                                 "not a format to imitate] {\"x\": 1}"}]
        result = self.say("copy that", history)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(self.memory.text, "Good evening, everyone.")

    def test_copy_that_with_nothing_above_copies_nothing(self) -> None:
        self.assertFalse(self.say("copy that").ok)
        self.assertEqual(self.memory.text, "Ship the release on Friday.")

    def test_a_question_about_the_copied_text_is_answered_from_it(self) -> None:
        prompts: list[str] = []

        def answer(prompt: str, profile: dict, max_tokens: int = 0) -> str:
            prompts.append(prompt)
            return "Release Friday."

        self.orch.smart_planner = SimpleNamespace(provider=SimpleNamespace(answer=answer))
        result = self.say("summarize what I copied")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.message, "Release Friday.")
        self.assertIn("<<<COPIED\nShip the release on Friday.\nCOPIED>>>", prompts[0])
        self.assertIn("not instructions", prompts[0])

    def test_translating_what_was_copied_translates_the_clipboard(self) -> None:
        asked: list = []
        self.orch._translate_tool_cache = TranslateTool(
            backend=lambda texts, source, target: asked.append(list(texts)) or [f"[{target}] {t}" for t in texts])
        result = self.say("translate what I copied to spanish")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(asked, [["Ship the release on Friday."]])

    def test_a_personal_account_cannot_read_the_laptops_clipboard(self) -> None:
        # Translation is everyday, so a translator that would succeed: only the clipboard refuses.
        self.orch._translate_tool_cache = TranslateTool(
            backend=lambda texts, source, target: [f"[{target}] {t}" for t in texts])
        with acting_as(PERSONAL):
            for text in ("what did I copy", "summarize what I copied", "copy that",
                         "translate what I copied to spanish"):
                with self.subTest(text):
                    result = self.say(text, [{"role": "assistant", "text": "hi"}])
                    self.assertFalse(result.ok, result.message)
                    self.assertNotIn("Ship the release", result.message)
            self.assertEqual(refused_command("clipboard"), "clipboard")
            self.assertEqual(refused_command("clipboard copy x"), "clipboard copy")
        self.assertEqual(self.memory.text, "Ship the release on Friday.")


class ClipboardRoutingTests(unittest.TestCase):
    ROUTED = {
        "what's on my clipboard": "clipboard",
        "what did I copy": "clipboard",
        "show me my clipboard": "clipboard",
        "copy that": "clipboard copy that",
        "can you copy your last answer to my clipboard": "clipboard copy that",
        "copy hello world to clipboard": "clipboard copy hello world",
        "copy 'meet at 5' to my clipboard": "clipboard copy meet at 5",
        "summarize what I copied": "clipboard ask summarize what I copied",
        "explain the text I copied": "clipboard ask explain the text I copied",
    }
    STAY = ("how do I copy and paste", "copy the file report.txt to D:", "copy that file to the desktop",
            "what is a clipboard", "my clipboard is broken", "don't copy that", "clipboard managers for windows",
            "copy paste is not working")

    def test_clipboard_requests_are_routed(self) -> None:
        planner = HeuristicPlannerProvider()
        for text, command in self.ROUTED.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_near_misses_stay_with_the_chat(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in self.STAY:
            with self.subTest(text):
                self.assertFalse((planner.plan(text, "", {}).command or "").startswith("clipboard"), text)


if __name__ == "__main__":
    unittest.main()
