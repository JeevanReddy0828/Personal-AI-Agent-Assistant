"""Plugins: the owner's own commands from <data dir>/plugins, loaded only when switched on."""

from __future__ import annotations

import asyncio
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools.plugins import ENABLE, PluginTool, load

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")
GOOD = """
COMMAND = "shout"
DESCRIPTION = "Says it louder."
RISK = "low"
def run(argument):
    return argument.upper()
"""


def write(folder: Path, name: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(textwrap.dedent(body), encoding="utf-8")


class PluginToolTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name) / "plugins"
        self.cards: list = []
        self.approve = True

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        self.gate = ApprovalGate(ask=ask)

    def tool(self, on: bool = True) -> PluginTool:
        return PluginTool(self.gate, self.folder, on=on)

    def test_a_good_plugin_runs(self) -> None:
        write(self.folder, "shout.py", GOOD)
        result = self.tool().run("shout", "hello")
        self.assertEqual((result.ok, result.message), (True, "HELLO"))

    def test_off_loads_nothing_at_all(self) -> None:
        write(self.folder, "boom.py", "raise SystemExit('loaded while off')\n" + GOOD)
        tool = self.tool(on=False)
        self.assertIn(ENABLE, tool.run("shout", "x").message, "off should say how to switch it on")
        self.assertIn(ENABLE, tool.listing().message)

    def test_the_switch_is_read_from_the_environment(self) -> None:
        write(self.folder, "shout.py", GOOD)
        with patch.dict(os.environ, {ENABLE: ""}):
            self.assertFalse(PluginTool(self.gate, self.folder).run("shout", "x").ok)
        with patch.dict(os.environ, {ENABLE: "1"}):
            self.assertTrue(PluginTool(self.gate, self.folder).run("shout", "x").ok)

    def test_the_declared_risk_goes_to_the_gate_and_a_denial_runs_nothing(self) -> None:
        write(self.folder, "wipe.py", GOOD.replace('"shout"', '"wipe"').replace('"low"', '"critical"')
              .replace("return argument.upper()", "raise AssertionError('ran without approval')"))
        tool = self.tool()
        tool.listing()                 # the folder is loaded (its own card, approved)
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            tool.run("wipe", "everything")
        self.assertIs(self.cards[-1].risk, RiskLevel.CRITICAL)

    def test_broken_plugins_are_listed_with_the_reason_and_the_rest_still_load(self) -> None:
        write(self.folder, "a_syntax.py", "def run(:\n")
        write(self.folder, "b_norisk.py", GOOD.replace('RISK = "low"', ""))
        write(self.folder, "c_badname.py", GOOD.replace('"shout"', '"Bad Name!"'))
        write(self.folder, "d_shout.py", GOOD)
        write(self.folder, "e_shout_again.py", GOOD)
        write(self.folder, "_helper.py", "raise SystemExit('helpers are not plugins')")
        plugins = {p.file: p for p in load(self.folder)}
        self.assertNotIn("_helper.py", plugins)
        self.assertIn("SyntaxError", plugins["a_syntax.py"].error)
        self.assertIn("RISK", plugins["b_norisk.py"].error)
        self.assertIn("COMMAND", plugins["c_badname.py"].error)
        self.assertIsNotNone(plugins["d_shout.py"].run)
        self.assertIn("already uses", plugins["e_shout_again.py"].error)
        self.assertIn("not loaded", self.tool().listing().message)

    def test_a_plugin_that_raises_is_a_failure_not_a_crash(self) -> None:
        write(self.folder, "fail.py", GOOD.replace('"shout"', '"fail"').replace("return argument.upper()", "1 / 0"))
        result = self.tool().run("fail", "x")
        self.assertFalse(result.ok)
        self.assertIn("ZeroDivisionError", result.message)


class PluginCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.orch = self.build(Path(temp.name))
        write(self.orch.data_dir / "plugins", "shout.py", GOOD)

    def say(self, text: str):
        return asyncio.run(self.orch.handle(text, history=[]))

    def test_plugin_commands_reach_the_files_in_the_data_folder(self) -> None:
        with patch.dict(os.environ, {ENABLE: "1"}):
            self.assertIn("shout", self.say("plugins").message)
            self.assertEqual(self.say("plugin shout good morning").message, "GOOD MORNING")

    def test_plugins_are_developer_only(self) -> None:
        with acting_as(PERSONAL):
            self.assertEqual(refused_command("plugins"), "plugins")
            self.assertEqual(refused_command("plugin shout hi"), "plugin")


class ReviewOf292Tests(PluginToolTests):
    """The review of #292, each finding reproduced first."""

    def test_a_low_or_medium_plugin_still_gets_a_card(self) -> None:
        for declared in ("low", "medium"):
            with self.subTest(declared):
                write(self.folder, "shout.py", GOOD.replace('"low"', f'"{declared}"'))
                self.cards.clear()
                self.tool().run("shout", "hi")
                self.assertIs(self.cards[-1].risk, RiskLevel.HIGH)

    def test_loading_the_folder_asks_first_and_a_refusal_runs_no_file(self) -> None:
        write(self.folder, "shout.py", GOOD + "\nimport pathlib\npathlib.Path(__file__).with_name('ran').write_text('x')\n")
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool().listing()
        self.assertFalse((self.folder / "ran").exists(), "a file ran before anyone said yes")
        self.assertIn("Load your plugins", self.cards[0].action)

    def test_sys_exit_in_a_plugin_is_a_failure_not_the_end(self) -> None:
        write(self.folder, "bye.py", GOOD.replace('"shout"', '"bye"').replace("return argument.upper()", "raise SystemExit(0)"))
        write(self.folder, "quits.py", "raise SystemExit('at import')")
        result = self.tool().run("bye", "now")
        self.assertFalse(result.ok)
        self.assertIn("SystemExit", result.message)
        self.assertIn("quits.py", self.tool().listing().message)

    def test_a_dataclass_a_relative_helper_and_the_projects_risk_load(self) -> None:
        write(self.folder, "_helper.py", "WORD = 'helped'\n")
        write(self.folder, "rich.py", (
            "from __future__ import annotations\nfrom dataclasses import dataclass\nfrom . import _helper\n"
            "from laptop_agent.safety import RiskLevel\n"
            "COMMAND = 'rich'\nDESCRIPTION = 'x'\nRISK = RiskLevel.CRITICAL\n"
            "@dataclass\nclass Box:\n    word: str\n"
            "def run(argument):\n    return Box(_helper.WORD).word\n"))
        tool = self.tool()
        self.assertEqual(tool.run("rich", "").message, "helped")
        self.assertIs(self.cards[-1].risk, RiskLevel.CRITICAL)

    def test_a_helper_comes_from_its_own_folder_not_an_earlier_one(self) -> None:
        source = ("from . import _review_helper\nCOMMAND = 'which'\nDESCRIPTION = 'x'\nRISK = 'high'\n"
                  "def run(argument):\n    return _review_helper.WORD\n")
        other = self.folder.parent / "other-data" / "plugins"
        for folder, word in ((self.folder, "first"), (other, "second")):
            write(folder, "_review_helper.py", f"WORD = '{word}'\n")
            write(folder, "which.py", source)
        self.assertEqual(self.tool().run("which", "").message, "first")
        self.assertEqual(PluginTool(self.gate, other, on=True).run("which", "").message, "second")

    def test_an_async_run_is_refused_at_load(self) -> None:
        write(self.folder, "later.py", GOOD.replace('"shout"', '"later"').replace("def run(", "async def run("))
        self.assertIn("not async", self.tool().listing().message)

    def test_a_cut_reply_says_so(self) -> None:
        write(self.folder, "big.py", GOOD.replace('"shout"', '"big"').replace("return argument.upper()", "return 'x' * 30000"))
        self.assertIn("cut: the plugin's reply was 30,000 characters", self.tool().run("big", "").message)

    def test_names_are_known_before_the_folder_is_loaded(self) -> None:
        write(self.folder, "shout.py", GOOD)
        tool = self.tool()
        self.assertEqual(tool.names(), {"shout"})
        self.assertEqual(self.cards, [], "finding a name ran the files")


class ReviewOf292CommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def test_a_sentence_about_plugins_is_left_to_chat_and_the_agent_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = self.build(Path(tmp))
            for text in ("plugin installation failed in vs code, how do i fix it?", "plugin for chrome that blocks ads?"):
                with self.subTest(text):
                    self.assertTrue(orch._reads_as_prose(text, text.lower()))
            self.assertFalse(any("plugin" in entry for entry in orch._AGENT_COMMANDS))

    def test_a_named_plugin_typed_directly_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = self.build(Path(tmp))
            folder = Path(tmp) / "plugins"
            write(folder, "shout.py", GOOD)
            orch._plugins_cache = PluginTool(orch.context.web.approval_gate, folder, on=True)
            self.assertFalse(orch._reads_as_prose("plugin shout\nhello there", "plugin shout\nhello there"))
            result = asyncio.run(orch.handle("plugin shout\nhello there", history=[]))
        self.assertEqual(result.message, "HELLO THERE")


if __name__ == "__main__":
    unittest.main()
