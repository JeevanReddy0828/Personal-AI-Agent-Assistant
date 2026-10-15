"""Running a Python script, and fixing it from its own error with the change shown first."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools.codefix import CodeFixTool, corrected, run_python

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")
BROKEN = "total = 1 + '2'\nprint(total)\n"
FIXED = "total = 1 + int('2')\nprint(total)\n"


class CodeFixTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.script = self.root / "add.py"
        self.script.write_text(BROKEN, encoding="utf-8")
        self.cards: list = []
        self.approve = True
        self.prompts: list[str] = []
        self.reply = f"Here you go:\n```python\n{FIXED}```"
        self.runs: list[Path] = []
        self.exit = (1, "TypeError: unsupported operand type(s) for +: 'int' and 'str'")

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        def writer(prompt: str) -> str:
            self.prompts.append(prompt)
            return self.reply

        def runner(path: Path):
            self.runs.append(path)
            return self.exit

        self.tool = CodeFixTool(writer, ApprovalGate(ask=ask), self.root / "backups", runner)

    def test_a_real_script_runs_in_its_folder_with_its_output(self) -> None:
        (self.root / "ok.py").write_text("import os\nprint(os.path.basename(os.getcwd()))\n", encoding="utf-8")
        self.assertEqual(run_python(self.root / "ok.py"), (0, self.root.name))
        code, output = run_python(self.script)
        self.assertNotEqual(code, 0)
        self.assertIn("TypeError", output)

    def test_running_asks_first_and_a_denial_runs_nothing(self) -> None:
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool.run(str(self.script))
        self.assertEqual(self.runs, [])
        self.assertIs(self.cards[0].risk, RiskLevel.HIGH)

    def test_a_fix_sees_the_file_and_the_error_and_shows_a_diff_before_writing(self) -> None:
        result = self.tool.fix(str(self.script))
        self.assertTrue(result.ok, result.message)
        self.assertIn(BROKEN, self.prompts[0])
        self.assertIn("unsupported operand", self.prompts[0])
        write_card = self.cards[-1]
        self.assertIs(write_card.risk, RiskLevel.HIGH)
        self.assertIn("+total = 1 + int('2')", write_card.preview)
        self.assertEqual(self.script.read_text(encoding="utf-8"), FIXED)
        backups = list((self.root / "backups").glob("add-*.py"))
        self.assertEqual([path.read_text(encoding="utf-8") for path in backups], [BROKEN])

    def test_a_denied_fix_writes_nothing(self) -> None:
        answers = iter([True, False])
        tool = CodeFixTool(lambda prompt: self.reply, ApprovalGate(ask=lambda request: next(answers)), self.root / "backups",
                           lambda path: self.exit)
        with self.assertRaises(ApprovalDenied):
            tool.fix(str(self.script))
        self.assertEqual(self.script.read_text(encoding="utf-8"), BROKEN)
        self.assertFalse((self.root / "backups").exists())

    def test_a_script_that_runs_needs_no_fix_and_no_model(self) -> None:
        self.exit = (0, "3")
        self.assertIn("nothing to fix", self.tool.fix(str(self.script)).message)
        self.assertEqual(self.prompts, [])

    def test_an_unchanged_or_missing_answer_writes_nothing(self) -> None:
        for reply in ("I can't help with that.", f"```python\n{BROKEN}```"):
            self.reply = reply
            self.assertFalse(self.tool.fix(str(self.script)).ok)
        self.assertEqual(self.script.read_text(encoding="utf-8"), BROKEN)

    def test_only_python_files_that_exist(self) -> None:
        (self.root / "notes.txt").write_text("x", encoding="utf-8")
        for words in (str(self.root / "notes.txt"), str(self.root / "missing.py")):
            self.assertFalse(self.tool.run(words).ok)
        self.assertEqual(self.cards, [])

    def test_the_fenced_file_is_what_is_read(self) -> None:
        self.assertEqual(corrected("text\n```py\nprint(1)\n```\nmore"), "print(1)\n")
        self.assertIsNone(corrected("no code here"))


class CodeCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def test_code_commands_are_developer_only(self) -> None:
        with acting_as(PERSONAL):
            self.assertEqual(refused_command("code run x.py"), "code run")
            self.assertEqual(refused_command("code fix x.py"), "code fix")

    def test_fix_my_script_reaches_the_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = self.build(Path(tmp))
            script = Path(tmp) / "add.py"
            script.write_text(BROKEN, encoding="utf-8")
            orch._code_fix_cache = CodeFixTool(lambda prompt: f"```python\n{FIXED}```", orch.context.web.approval_gate,
                                               Path(tmp) / "backups", lambda path: (1, "TypeError"))
            result = asyncio.run(orch.handle(f"fix my script {script}", history=[]))
            self.assertTrue(result.ok, result.message)
            self.assertEqual(script.read_text(encoding="utf-8"), FIXED)


class CodeRoutingTests(unittest.TestCase):
    def test_scripts_named_with_py_are_routed(self) -> None:
        planner = HeuristicPlannerProvider()
        for text, command in {"run hello.py": "code run hello.py", "fix my script hello.py": "code fix hello.py",
                              "debug hello.py": "code fix hello.py", "what's wrong with hello.py": "code fix hello.py",
                              'fix "my app.py"': 'code fix "my app.py"'}.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_coding_questions_are_not_scripts(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in ("how do I fix a python script", "fix my code", "run python", "what is a .py file", "run the tests",
                     "fix my bike"):
            with self.subTest(text):
                self.assertFalse((planner.plan(text, "", {}).command or "").startswith("code "), text)


if __name__ == "__main__":
    unittest.main()
