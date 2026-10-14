from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.workspace import MAX_FILES, WorkspaceTool, file_plan, folder_and_place, safe_relative

PERSONAL = Principal("p1", "family", "personal")
REQUEST = "create a workspace folder and fill files with python basics"


def plan_json(*files: tuple[str, str]) -> str:
    return json.dumps({"files": [{"path": path, "content": content} for path, content in files]})


class NameAndPlaceTests(unittest.TestCase):
    home = Path("/home/x")

    def test_a_named_folder_on_the_desktop(self) -> None:
        self.assertEqual(folder_and_place("make a folder called notes on my desktop", self.home),
                         ("notes", self.home / "Desktop"))

    def test_the_default_is_a_workspace_in_documents(self) -> None:
        self.assertEqual(folder_and_place(REQUEST, self.home), ("workspace", self.home / "Documents"))

    def test_a_two_word_kind_names_the_folder_and_new_is_dropped(self) -> None:
        self.assertEqual(folder_and_place("create a new python basics folder", self.home)[0], "python basics")

    def test_a_place_is_never_taken_for_the_name(self) -> None:
        self.assertEqual(folder_and_place("make a folder in my documents folder", self.home)[0], "workspace")


class PathSafetyTests(unittest.TestCase):
    def test_plain_relative_paths_pass(self) -> None:
        self.assertEqual(safe_relative("01_variables.py"), "01_variables.py")
        self.assertEqual(safe_relative("src\\app.py"), "src/app.py")

    def test_traversal_absolute_and_drive_paths_are_refused(self) -> None:
        for bad in ("../x.py", "a/../../x.py", "/etc/passwd", "C:/x.py", "C:x.py", "", "a//b.py", ".hidden"):
            self.assertIsNone(safe_relative(bad), bad)

    def test_a_name_windows_would_rewrite_is_refused(self) -> None:
        for bad in ("notes.", "dir./x.py", "con.txt", "NUL", "a/b/c/d/e.py"):
            self.assertIsNone(safe_relative(bad), bad)


class FilePlanTests(unittest.TestCase):
    def test_a_fenced_reply_is_read(self) -> None:
        reply = "```json\n" + plan_json(("a.py", "print(1)")) + "\n```"
        self.assertEqual(file_plan(reply), [("a.py", "print(1)")])

    def test_prose_and_bad_json_are_not_a_plan(self) -> None:
        for reply in ("Here you go!", "{not json}", '{"files": []}', '{"files": "x"}', ""):
            self.assertIsNone(file_plan(reply), reply)

    def test_one_unsafe_path_refuses_the_whole_plan(self) -> None:
        self.assertIsNone(file_plan(plan_json(("ok.py", "1"), ("../evil.py", "2"))))

    def test_too_many_files_duplicates_and_size_are_refused(self) -> None:
        self.assertIsNone(file_plan(plan_json(*((f"f{n}.py", "x") for n in range(MAX_FILES + 1)))))
        self.assertIsNone(file_plan(plan_json(("A.py", "1"), ("a.py", "2"))))
        self.assertIsNone(file_plan(plan_json(("big.py", "x" * 20_001))))
        self.assertIsNone(file_plan(plan_json(*((f"f{n}.py", "x" * 15_000) for n in range(9)))))


class WorkspaceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.cards: list = []
        self.prompts: list[str] = []

    def tool(self, reply: str | None = None, approve: bool = True, writer=None) -> WorkspaceTool:
        def ask(request) -> bool:
            self.cards.append(request)
            return approve

        def write(prompt: str) -> str:
            self.prompts.append(prompt)
            return reply or ""

        return WorkspaceTool(writer or (write if reply is not None else None), ApprovalGate(ask=ask), home=self.home)

    def test_the_files_are_written_after_one_card_that_lists_them(self) -> None:
        reply = plan_json(("01_variables.py", "x = 1\n"), ("README.md", "# Python basics\n"))
        result = self.tool(reply).create(REQUEST)
        self.assertTrue(result.ok, result.message)
        folder = self.home / "Documents" / "workspace"
        self.assertEqual((folder / "01_variables.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertTrue((folder / "README.md").exists())
        self.assertEqual(len(self.cards), 1)
        self.assertIs(self.cards[0].risk, RiskLevel.HIGH)
        for text in (str(folder), "01_variables.py", "README.md"):
            self.assertIn(text, self.cards[0].preview)

    def test_a_denied_card_writes_nothing(self) -> None:
        tool = self.tool(plan_json(("a.py", "1")), approve=False)
        with self.assertRaises(ApprovalDenied):
            tool.create(REQUEST)
        self.assertFalse((self.home / "Documents").exists())

    def test_an_existing_folder_is_never_written_into(self) -> None:
        (self.home / "Documents" / "workspace").mkdir(parents=True)
        result = self.tool(plan_json(("a.py", "1"))).create(REQUEST)
        self.assertTrue(result.ok)
        self.assertEqual(list((self.home / "Documents" / "workspace").iterdir()), [])
        self.assertTrue((self.home / "Documents" / "workspace-2" / "a.py").exists())

    def test_a_folder_with_no_contents_asked_is_made_empty_and_needs_no_model(self) -> None:
        result = self.tool().create("make a folder called taxes on my desktop")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(list((self.home / "Desktop" / "taxes").iterdir()), [])

    def test_filling_without_a_model_says_so_and_asks_nothing(self) -> None:
        result = self.tool().create(REQUEST)
        self.assertFalse(result.ok)
        self.assertEqual(self.cards, [])

    def test_a_reply_that_is_not_a_plan_writes_nothing_and_asks_nothing(self) -> None:
        for reply in ("sorry, I can't", plan_json(("../x.py", "1"))):
            result = self.tool(reply).create(REQUEST)
            self.assertFalse(result.ok)
        self.assertEqual(self.cards, [])
        self.assertFalse((self.home / "Documents").exists())

    def test_a_model_that_raises_is_a_failure_not_a_crash(self) -> None:
        def broken(prompt: str) -> str:
            raise RuntimeError("tier busy")

        result = self.tool(writer=broken).create(REQUEST)
        self.assertFalse(result.ok)
        self.assertIn("tier busy", result.message)

    def test_a_personal_account_is_refused_before_a_card_and_nothing_is_written(self) -> None:
        tool = self.tool(plan_json(("a.py", "1")))
        with acting_as(PERSONAL), self.assertRaises(ApprovalDenied):
            tool.create(REQUEST)
        self.assertEqual(self.cards, [])
        self.assertFalse((self.home / "Documents").exists())

    def test_the_command_is_developer_only(self) -> None:
        with acting_as(PERSONAL):
            self.assertEqual(refused_command("workspace make a folder"), "workspace")


class RoutingTests(unittest.TestCase):
    def route(self, text: str):
        return HeuristicPlannerProvider().plan(text, "", {})

    def test_a_folder_with_files_is_a_workspace_not_a_document(self) -> None:
        for text in (REQUEST, "make a folder called notes on my desktop", "can you create a new python basics folder",
                     "create a workspace and fill it with rust examples", "I want you to make a folder on my desktop"):
            decision = self.route(text)
            self.assertTrue(decision.is_command and decision.command.startswith("workspace "), text)

    def test_near_misses_stay_with_the_chat(self) -> None:
        for text in ("how do I create a folder in python", "create a folder in python", "create a folder using powershell",
                     "how to make a folder", "create a slack workspace", "create a project plan", "don't create a folder"):
            self.assertFalse(self.route(text).is_command, text)

    def test_a_pdf_is_still_a_document(self) -> None:
        self.assertTrue(self.route("make a pdf about folders").command.startswith("document "))


class DispatchTests(unittest.TestCase):
    def test_the_sentence_reaches_the_workspace_tool_through_handle(self) -> None:
        from test_everyday_requests import Everyday

        class Recorder:
            requests: list[str] = []

            def create(self, request: str) -> ToolResult:
                self.requests.append(request)
                return ToolResult.success("made it")

        with tempfile.TemporaryDirectory() as tmp:
            everyday = Everyday(Path(tmp))
            everyday.orchestrator._workspace_tool_cache = Recorder()
            result, ran = everyday.say(REQUEST)
        self.assertEqual(Recorder.requests, [REQUEST])
        self.assertTrue(ran.startswith("workspace "), ran)


if __name__ == "__main__":
    unittest.main()
