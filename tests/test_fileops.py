"""Moving, copying, renaming and deleting files, and undoing the last change.

Every test works in a temporary home folder with a recording Recycle Bin; nothing is erased.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.planner.heuristic import HeuristicPlannerProvider
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools import fileops
from laptop_agent.tools.fileops import FileOps

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")


class FileOpsTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        for place in ("Desktop", "Documents", "Downloads"):
            (self.home / place).mkdir(parents=True)
        self.report = self.home / "Downloads" / "report.pdf"
        self.report.write_text("pdf", encoding="utf-8")
        self.cards: list = []
        self.approve = True
        self.recycled: list[Path] = []

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        def recycle(path: Path) -> None:
            self.recycled.append(path)
            path.unlink() if path.is_file() else None

        self.ops = FileOps(ApprovalGate(ask=ask), Path(temp.name) / "journal.json", home=self.home, recycler=recycle)

    def test_a_bare_name_is_found_and_moved_to_a_named_place(self) -> None:
        result = self.ops.move("report.pdf", "my desktop")
        self.assertTrue(result.ok, result.message)
        self.assertTrue((self.home / "Desktop" / "report.pdf").exists())
        self.assertFalse(self.report.exists())
        self.assertIs(self.cards[0].risk, RiskLevel.HIGH)

    def test_nothing_is_overwritten(self) -> None:
        (self.home / "Desktop" / "report.pdf").write_text("mine", encoding="utf-8")
        self.assertFalse(self.ops.move(str(self.report), "desktop").ok)
        self.assertEqual((self.home / "Desktop" / "report.pdf").read_text(encoding="utf-8"), "mine")
        self.assertEqual(self.cards, [])

    def test_a_name_in_two_places_asks_which(self) -> None:
        (self.home / "Documents" / "report.pdf").write_text("other", encoding="utf-8")
        result = self.ops.move("report.pdf", "desktop")
        self.assertFalse(result.ok)
        self.assertIn("Which one?", result.message)

    def test_the_home_folder_and_a_drive_root_are_never_changed(self) -> None:
        # Never approved: should a guard fail, nothing outside the temporary folder moves.
        self.approve = False
        for change in (lambda: self.ops.delete(self.home.anchor), lambda: self.ops.rename(self.home.anchor, "x"),
                       lambda: FileOps(self.ops._gate, self.home / "j.json", recycler=lambda p: None).delete(str(Path.home()))):
            self.assertFalse(change().ok)
        self.assertEqual(self.cards, [])

    def test_a_denied_card_changes_nothing(self) -> None:
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.ops.move("report.pdf", "desktop")
        self.assertTrue(self.report.exists())
        self.assertIn("no file change", self.ops.undo().message, "a change that never happened was journaled")

    def test_undo_walks_back_one_change_at_a_time(self) -> None:
        self.ops.move("report.pdf", "desktop")
        self.ops.rename(str(self.home / "Desktop" / "report.pdf"), "q3.pdf")
        self.assertTrue(self.ops.undo().ok)
        self.assertTrue((self.home / "Desktop" / "report.pdf").exists())
        self.assertTrue(self.ops.undo().ok)
        self.assertTrue(self.report.exists())
        self.assertFalse((self.home / "Desktop" / "report.pdf").exists())
        self.assertIn("no file change", self.ops.undo().message)

    def test_undo_will_not_overwrite_what_took_the_old_place(self) -> None:
        self.ops.move("report.pdf", "desktop")
        self.report.write_text("new", encoding="utf-8")
        self.assertFalse(self.ops.undo().ok)
        self.assertEqual(self.report.read_text(encoding="utf-8"), "new")

    def test_a_rename_keeps_the_extension_and_undoes(self) -> None:
        self.assertTrue(self.ops.rename("report.pdf", "q3 report").ok)
        renamed = self.home / "Downloads" / "q3 report.pdf"
        self.assertTrue(renamed.exists())
        self.assertFalse(self.ops.rename("q3 report.pdf", "../escape.pdf").ok)
        self.ops.undo()
        self.assertTrue(self.report.exists())

    def test_a_copy_undoes_by_recycling_the_copy_only(self) -> None:
        self.ops.move("report.pdf", "documents", copy=True)
        copy = self.home / "Documents" / "report.pdf"
        self.assertTrue(copy.exists() and self.report.exists())
        self.ops.undo()
        self.assertEqual(self.recycled, [copy])
        self.assertTrue(self.report.exists())

    def test_delete_goes_to_the_recycle_bin_and_undo_says_where(self) -> None:
        self.assertTrue(self.ops.delete("report.pdf").ok)
        self.assertEqual(self.recycled, [self.report])
        undo = self.ops.undo()
        self.assertFalse(undo.ok)
        self.assertIn("Recycle Bin", undo.message)

    def test_without_a_recycle_bin_nothing_is_deleted(self) -> None:
        ops = FileOps(ApprovalGate(ask=lambda r: True), self.home / "j.json", home=self.home)
        self.assertFalse(ops.delete("report.pdf").ok)
        self.assertTrue(self.report.exists())


class FileOpsCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.orch = self.build(root)
        self.home = root / "home"
        (self.home / "Desktop").mkdir(parents=True)
        (self.home / "Downloads").mkdir(parents=True)
        (self.home / "Downloads" / "report.pdf").write_text("pdf", encoding="utf-8")
        self.orch._file_ops_cache = FileOps(self.orch.context.web.approval_gate, root / "journal.json",
                                            home=self.home, recycler=lambda path: None)

    def say(self, text: str, history=None):
        return asyncio.run(self.orch.handle(text, history=history or []))

    def test_move_then_undo_that_puts_it_back(self) -> None:
        moved = self.say("move report.pdf to my desktop")
        self.assertTrue(moved.ok, moved.message)
        self.assertTrue((self.home / "Desktop" / "report.pdf").exists())
        undone = self.say("undo that", [{"role": "user", "text": "move report.pdf to my desktop"},
                                        {"role": "assistant", "text": moved.message}])
        self.assertTrue(undone.ok, undone.message)
        self.assertTrue((self.home / "Downloads" / "report.pdf").exists())

    def test_undo_that_after_something_else_leaves_the_files_alone(self) -> None:
        self.say("move report.pdf to my desktop")
        self.say("undo that", [{"role": "assistant", "text": "Reminder set for 5pm."}])
        self.assertTrue((self.home / "Desktop" / "report.pdf").exists())

    def test_every_form_is_developer_only(self) -> None:
        with acting_as(PERSONAL):
            for command in ("file move a.txt to desktop", "file copy a.txt to desktop", "file rename a.txt to b.txt",
                            "file delete a.txt", "undo file"):
                with self.subTest(command):
                    self.assertIsNotNone(refused_command(command))
            self.assertFalse(self.say("move report.pdf to my desktop").ok)
        self.assertTrue((self.home / "Downloads" / "report.pdf").exists())


class FileOpsRoutingTests(unittest.TestCase):
    ROUTED = {
        "move report.pdf to my desktop": "file move report.pdf to my desktop",
        "copy budget 2026.xlsx to D:\\backup": "file copy budget 2026.xlsx to D:\\backup",
        'move "old notes" to documents': 'file move "old notes" to documents',
        "rename notes.txt as ideas": "file rename notes.txt to ideas",
        "delete old-notes.txt": "file delete old-notes.txt",
        "undo the last move": "undo file move",
    }
    STAY = ("move the meeting to friday", "delete my last note", "copy that to my clipboard",
            "how do I move files to another drive", "rename my wifi network", "remove google.com from my bookmarks",
            "delete it", "move it to my desktop", "undo")

    def test_file_changes_are_routed(self) -> None:
        planner = HeuristicPlannerProvider()
        for text, command in self.ROUTED.items():
            with self.subTest(text):
                self.assertEqual(planner.plan(text, "", {}).command, command)

    def test_undo_that_is_the_file_only_after_a_file_change(self) -> None:
        planner = HeuristicPlannerProvider()
        after_move = [{"role": "assistant", "text": "Moved report.pdf to C:\\Users\\x\\Desktop\\report.pdf."}]
        after_reminder = [{"role": "assistant", "text": "Reminder set for 5pm."}]
        self.assertEqual(planner.plan("undo that", "", {}, after_move).command, "undo file")
        self.assertNotEqual(planner.plan("undo that", "", {}, after_reminder).command, "undo file")

    def test_near_misses_stay_away_from_files(self) -> None:
        planner = HeuristicPlannerProvider()
        for text in self.STAY:
            with self.subTest(text):
                command = planner.plan(text, "", {}).command or ""
                self.assertFalse(command.startswith(("file ", "undo file")), f"{text} -> {command}")


class ReviewOf287Tests(FileOpsTests):
    """The review of #287, each finding reproduced first."""

    def test_a_drive_without_a_recycle_bin_is_refused_before_anything(self) -> None:
        calls: list = []
        kernel32 = type("K", (), {"GetDriveTypeW": lambda self, root: 2})()   # DRIVE_REMOVABLE
        shell32 = type("S", (), {"SHFileOperationW": lambda self, op: calls.append(op) or 0})()
        with self.assertRaisesRegex(OSError, "no Recycle Bin"):
            fileops.recycle_windows(Path("E:/photos"), kernel32, shell32)
        with self.assertRaisesRegex(OSError, "network share"):
            fileops.recycle_windows(Path("//server/share/x.txt"), kernel32, shell32)
        self.assertEqual(calls, [], "the delete was sent")

    def test_a_folder_that_does_not_exist_is_not_a_new_name(self) -> None:
        missing = self.home / "backup"
        result = self.ops.move("report.pdf", str(missing), copy=True)
        self.assertFalse(result.ok)
        self.assertIn("There's no folder", result.message)
        self.assertFalse(missing.exists())

    def test_the_folders_above_and_inside_home_are_protected(self) -> None:
        for target in (str(self.home.parent), str(self.home / "Desktop")):
            with self.subTest(target):
                self.assertFalse(self.ops.delete(target).ok)
        self.assertEqual(self.recycled, [])

    def test_a_home_spelled_differently_from_its_resolved_path_is_still_protected(self) -> None:
        # CI's Windows temp dir is an 8.3 name (RUNNER~1) that resolve() expands; ".." stands in for it.
        spelled = self.home / ".." / self.home.name
        ops = FileOps(ApprovalGate(ask=lambda request: True), self.home.parent / "journal2.json",
                      home=spelled, recycler=self.recycled.append)
        for target in (str(self.home.parent), str(self.home / "Desktop")):
            with self.subTest(target):
                self.assertFalse(ops.delete(target).ok)
        self.assertEqual(self.recycled, [])

    def replace(self, path: Path) -> None:
        stranger = self.home / "stranger.pdf"
        stranger.write_text("someone else's", encoding="utf-8")
        path.unlink()
        stranger.rename(path)

    def test_undo_leaves_a_file_put_where_the_moved_one_was(self) -> None:
        self.assertTrue(self.ops.move("report.pdf", "my desktop").ok)
        moved = self.home / "Desktop" / "report.pdf"
        self.replace(moved)
        result = self.ops.undo()
        self.assertFalse(result.ok)
        self.assertIn("isn't the one I moved", result.message)
        self.assertEqual(moved.read_text(encoding="utf-8"), "someone else's")
        self.assertFalse(self.report.exists())

    def test_undo_leaves_a_file_put_where_the_copy_was(self) -> None:
        self.assertTrue(self.ops.move("report.pdf", "my desktop", copy=True).ok)
        copied = self.home / "Desktop" / "report.pdf"
        self.replace(copied)
        self.assertFalse(self.ops.undo().ok)
        self.assertEqual(self.recycled, [])
        self.assertTrue(copied.exists())

    def copied_drafts(self) -> Path:
        drafts = self.home / "Downloads" / "drafts"
        drafts.mkdir()
        (drafts / "outline.txt").write_text("outline", encoding="utf-8")
        self.assertTrue(self.ops.move("drafts", "my desktop", copy=True).ok)
        return self.home / "Desktop" / "drafts"

    def test_undo_leaves_a_copied_folder_with_new_work_in_it(self) -> None:
        copy = self.copied_drafts()
        (copy / "new-work.txt").write_text("mine", encoding="utf-8")
        self.assertFalse(self.ops.undo().ok)
        self.assertEqual(self.recycled, [])

    @staticmethod
    def edit_keeping_size_and_time(path: Path, text: str) -> None:
        before = path.stat()
        path.write_text(text, encoding="utf-8")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    def test_undo_leaves_a_copied_folder_whose_file_was_edited_in_place(self) -> None:
        copy = self.copied_drafts()
        self.edit_keeping_size_and_time(copy / "outline.txt", "revised")   # same length as "outline"
        self.assertFalse(self.ops.undo().ok)
        self.assertEqual(self.recycled, [])

    def test_undo_leaves_a_copied_file_edited_in_place(self) -> None:
        self.assertTrue(self.ops.move("report.pdf", "my desktop", copy=True).ok)
        copy = self.home / "Desktop" / "report.pdf"
        self.edit_keeping_size_and_time(copy, "PDF")
        self.assertFalse(self.ops.undo().ok)
        self.assertEqual(self.recycled, [])

    def test_an_unchanged_copied_folder_is_still_undone(self) -> None:
        copy = self.copied_drafts()
        self.assertTrue(self.ops.undo().ok)
        self.assertEqual(self.recycled, [copy])

    def test_my_and_the_are_not_part_of_a_name(self) -> None:
        self.assertEqual(self.ops.find("my report.pdf")[0], self.report)

    def test_a_dotted_name_keeps_its_extension(self) -> None:
        self.assertTrue(self.ops.rename("report.pdf", "Q3 v2.1").ok)
        self.assertTrue((self.home / "Downloads" / "Q3 v2.1.pdf").exists())

    def test_something_arriving_during_the_card_is_not_overwritten(self) -> None:
        arrived = self.home / "Desktop" / "report.pdf"

        def ask(request) -> bool:
            arrived.write_text("theirs", encoding="utf-8")
            return True

        ops = fileops.FileOps(fileops.ApprovalGate(ask=ask), self.home / "j2.json", home=self.home)
        self.assertFalse(ops.move("report.pdf", "desktop", copy=True).ok)
        self.assertEqual(arrived.read_text(encoding="utf-8"), "theirs")

    def test_a_delete_does_not_block_undoing_what_came_before(self) -> None:
        self.ops.move("report.pdf", "desktop")
        (self.home / "Documents" / "old.txt").write_text("x", encoding="utf-8")
        self.ops.delete("old.txt")
        self.assertIn("Recycle Bin", self.ops.undo().message)
        self.assertTrue(self.ops.undo().ok, "the move under the delete could never be undone")
        self.assertTrue(self.report.exists())

    def test_undoing_a_named_kind_skips_the_others(self) -> None:
        self.ops.move("report.pdf", "desktop")
        self.ops.move("report.pdf", "documents", copy=True)
        self.assertTrue(self.ops.undo("move").ok)
        self.assertTrue(self.report.exists())
        self.assertTrue((self.home / "Documents" / "report.pdf").exists(), "undoing a move removed the copy")


class ReviewOf287CommandTests(FileOpsCommandTests):
    def test_a_name_with_to_in_it_is_one_name(self) -> None:
        (self.home / "Downloads" / "back to school.docx").write_text("x", encoding="utf-8")
        self.assertEqual(self.orch._file_change_parts("file move back to school.docx to desktop"),
                         ("move", "back to school.docx", "desktop"))
        self.assertEqual(self.orch._file_change_parts('file move "back to school.docx" to desktop'),
                         ("move", "back to school.docx", "desktop"))

    def test_an_invented_target_is_refused(self) -> None:
        self.assertIn("file delete", self.orch._TARGET_COMMANDS)


class ReviewOf287UndoTests(FileOpsCommandTests):
    def test_the_model_router_cannot_undo_a_file_after_something_else(self) -> None:
        from laptop_agent.planner.core import PlanDecision
        fake = type("P", (), {"plan": lambda self, *a, **k: PlanDecision(action="command", command="undo file",
                                                                          confidence=0.9, explanation="model")})()
        self.orch.planner = type("Planner", (), {"plan": fake.plan, "provider": None})()
        after_reminder = [{"role": "assistant", "text": "Reminder set for 5pm."}]
        after_move = [{"role": "assistant", "text": "Moved report.pdf to C:/Users/me/Desktop/report.pdf."}]
        routed = self.orch._route("undo that please", {}, after_reminder)
        self.assertFalse(routed.is_command, routed)
        self.assertTrue(self.orch._route("undo that please", {}, after_move).is_command)


class ReviewOf287RoutingTests(FileOpsRoutingTests):
    STAY = FileOpsRoutingTests.STAY + ("delete the alarm at 6.30", "remove my alarm at 7.45", "delete google.com",
                                "remove v1.2", "delete chapter 3.1", "undo the last change", "revert the last change")


if __name__ == "__main__":
    unittest.main()
