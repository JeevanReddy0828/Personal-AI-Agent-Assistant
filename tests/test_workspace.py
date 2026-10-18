from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools.base import ToolResult
from laptop_agent.planner.core import CutOff
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

    def test_a_file_that_is_also_a_folder_is_refused_in_either_order(self) -> None:
        # "src" as a file and "src/app.py" beneath it: the second write raised FileExistsError after the
        # card was approved, leaving half a workspace (Codex's review of #282). Windows ignores case.
        for files in ((("src", "x"), ("src/app.py", "y")), (("src/app.py", "y"), ("src", "x")),
                      (("SRC", "x"), ("src/app.py", "y")), (("a/b", "x"), ("A/b/c.py", "y"))):
            with self.subTest(files):
                self.assertIsNone(file_plan(plan_json(*files)))
        self.assertIsNotNone(file_plan(plan_json(("src/a.py", "1"), ("src/b.py", "2"), ("srcx.py", "3"))))

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


class ReviewOf282Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.cards: list = []

    def tool(self, reply: str) -> WorkspaceTool:
        def ask(request) -> bool:
            self.cards.append(request)
            return True

        return WorkspaceTool(lambda prompt: reply, ApprovalGate(ask=ask), home=self.home)

    def route(self, text: str):
        return HeuristicPlannerProvider().plan(text, "", {})

    def test_only_a_whole_sentence_asking_for_a_folder_routes(self) -> None:
        for text in ("make my downloads folder less cluttered", "create a folder structure for a react app",
                     "set up a directory of plumbers", "start a project with my manager",
                     "write a report on folders as a pdf and make a folder", "create a notion workspace with pages"):
            self.assertFalse(self.route(text).is_command and self.route(text).command.startswith("workspace "), text)
        for text in ("create a project folder with flask starter files", "make a folder called notes on my desktop.",
                     "make an empty folder on my desktop", "create a folder called rust, with examples"):
            self.assertTrue(self.route(text).command.startswith("workspace "), text)

    def test_the_name_and_place_come_from_before_the_contents(self) -> None:
        home = Path("/h")
        self.assertEqual(folder_and_place("create a folder with notes on home loans in my downloads", home),
                         ("workspace", home / "Documents"))
        self.assertEqual(folder_and_place("make a folder with notes on a book called dune", home)[0], "workspace")
        self.assertEqual(folder_and_place("make an empty folder on my desktop", home), ("workspace", home / "Desktop"))
        self.assertEqual(folder_and_place("create a folder with the name rust", home)[0], "rust")
        self.assertEqual(folder_and_place("create a folder called downloads", home)[0], "downloads")
        self.assertEqual(folder_and_place('create a folder in "C:/My Projects" called demo', home),
                         ("demo", Path("C:/My Projects")))
        self.assertEqual(folder_and_place("make a folder at /tmp/x.", home)[1], Path("/tmp/x"))
        self.assertEqual(folder_and_place("make a folder with a script that cleans files in /tmp/junk", home)[1],
                         home / "Documents")

    def test_a_readme_holding_a_fence_and_prose_after_still_parse(self) -> None:
        reply = "Sure!\n" + plan_json(("README.md", "```py\nprint(1)\n```\n")) + "\nEnjoy."
        self.assertEqual(file_plan(reply), [("README.md", "```py\nprint(1)\n```\n")])

    def test_files_windows_acts_on_and_null_paths_are_refused(self) -> None:
        for bad in ("evil.url", "x.LNK", "a.scf", "run.bat", "s.ps1", "desktop.ini", "Desktop.INI", "x.library-ms", None):
            self.assertIsNone(safe_relative(bad), bad)

    def test_an_unmade_place_is_refused_not_created(self) -> None:
        result = self.tool(plan_json(("a.py", "1"))).create(f"make a folder in {self.home / 'nope'} with python basics")
        self.assertFalse(result.ok)
        self.assertFalse((self.home / "nope").exists())
        self.assertEqual(self.cards, [])

    def test_a_folder_made_while_the_card_was_open_is_never_written_into(self) -> None:
        def ask(request) -> bool:
            (self.home / "Documents" / "workspace").mkdir(parents=True)
            return True

        tool = WorkspaceTool(lambda prompt: plan_json(("a.py", "1")), ApprovalGate(ask=ask), home=self.home)
        result = tool.create(REQUEST)
        self.assertTrue(result.ok, result.message)
        self.assertEqual(list((self.home / "Documents" / "workspace").iterdir()), [])
        self.assertTrue((self.home / "Documents" / "workspace-2" / "a.py").exists())

    def test_a_failed_write_removes_the_folder_and_is_a_failure(self) -> None:
        tool = self.tool(plan_json(("a.py", "1"), ("b.py", "\ud800")))
        result = tool.create(REQUEST)
        self.assertFalse(result.ok)
        self.assertEqual(list((self.home / "Documents").iterdir()), [])

    def test_a_folder_the_cleanup_could_not_remove_is_named_not_denied(self) -> None:
        tool = self.tool(plan_json(("a.py", "1"), ("b.py", "\ud800")))
        with mock.patch("laptop_agent.tools.workspace.shutil.rmtree"):   # a file held open by another program
            result = tool.create(REQUEST)
        folder = self.home / "Documents" / "workspace"
        self.assertTrue((folder / "a.py").exists())
        self.assertFalse(result.ok)
        self.assertIn(str(folder), result.message)
        self.assertNotIn("nothing was left", result.message)

    def test_an_outage_and_a_cut_off_plan_say_different_things(self) -> None:
        self.assertIn("No language model answered", self.tool("").create(REQUEST).message)
        cut = CutOff(plan_json(("a.py", "1")))
        self.assertIn("length limit", self.tool(cut).create(REQUEST).message)
        self.assertEqual(self.cards, [])

    def test_the_card_shows_what_the_files_hold(self) -> None:
        self.tool(plan_json(("a.py", "print('hello there')"))).create(REQUEST)
        self.assertIn("print('hello there')", self.cards[0].preview)

    def test_the_prompt_states_the_limits(self) -> None:
        prompts: list[str] = []
        WorkspaceTool(lambda prompt: prompts.append(prompt) or "", ApprovalGate(ask=lambda r: True),
                      home=self.home).create(REQUEST)
        self.assertIn(str(MAX_FILES), prompts[0])
        self.assertIn("12,000", prompts[0])

    def test_the_writer_never_asks_past_the_endpoint_cap(self) -> None:
        from laptop_agent.agents.orchestrator import AgentOrchestrator

        asked: list[int] = []

        class Provider:
            max_output_tokens = 4096

            def answer(self, prompt, context, max_tokens=None):
                asked.append(max_tokens)
                return "ok"

        holder = AgentOrchestrator.__new__(AgentOrchestrator)
        holder.smart_planner = None
        holder.planner = type("Planner", (), {"provider": Provider()})()
        holder._build_document_writer()("x")
        self.assertEqual(asked, [4096])


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
