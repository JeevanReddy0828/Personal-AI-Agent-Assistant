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
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool().run("wipe", "everything")
        self.assertIs(self.cards[0].risk, RiskLevel.CRITICAL)

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


if __name__ == "__main__":
    unittest.main()
