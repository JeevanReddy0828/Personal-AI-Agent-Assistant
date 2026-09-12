from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from laptop_agent.retention import Policy, sweep, sweep_uploads


class ArtifactRetentionTests(unittest.TestCase):
    """One day of use left 7.1MB of generated images and a temp directory with one
    folder per attachment, forever. A local-first app that quietly eats a user's disk
    is a bug they only notice once it is large."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        self.images = self.data / "images"
        self.images.mkdir()

    def make(self, name: str, age_days: float = 0.0, size: int = 100) -> Path:
        path = self.images / name
        path.write_bytes(b"x" * size)
        stamp = time.time() - age_days * 86400
        import os

        os.utime(path, (stamp, stamp))
        return path

    def policy(self, keep_newest=3, max_age_days=7) -> tuple[Policy, ...]:
        return (Policy("images", (".png",), keep_newest=keep_newest, max_age_days=max_age_days),)

    def test_recent_files_are_kept_however_many_there_are(self) -> None:
        for index in range(10):
            self.make(f"new{index}.png", age_days=0.5)
        sweep(self.data, self.policy())
        self.assertEqual(len(list(self.images.glob("*.png"))), 10)

    def test_the_newest_are_kept_however_old_they_are(self) -> None:
        for index in range(6):
            self.make(f"old{index}.png", age_days=400)
        outcome = sweep(self.data, self.policy(keep_newest=3))
        self.assertEqual(len(list(self.images.glob("*.png"))), 3)
        self.assertEqual(outcome["removed"]["images"], 3)

    def test_a_file_type_we_do_not_write_is_never_touched(self) -> None:
        keeper = self.images / "notes.txt"
        keeper.write_text("mine", encoding="utf-8")
        import os

        stale = time.time() - 400 * 86400
        os.utime(keeper, (stale, stale))
        for index in range(6):
            self.make(f"old{index}.png", age_days=400)
        sweep(self.data, self.policy(keep_newest=0))
        self.assertTrue(keeper.exists(), "a file the app does not generate must survive")

    def test_a_missing_folder_is_not_an_error(self) -> None:
        self.assertEqual(sweep(self.data / "nowhere", self.policy())["removed"], {})

    def test_it_reports_what_it_freed(self) -> None:
        for index in range(5):
            self.make(f"old{index}.png", age_days=400, size=1000)
        outcome = sweep(self.data, self.policy(keep_newest=1))
        self.assertEqual(outcome["freed_bytes"], 4000)


class UploadSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.uploads = Path(self._tmp.name)

    def folder(self, name: str, age_hours: float) -> Path:
        path = self.uploads / name
        path.mkdir()
        (path / "file.bin").write_bytes(b"x")
        import os

        stamp = time.time() - age_hours * 3600
        os.utime(path, (stamp, stamp))
        return path

    def test_stale_upload_folders_are_removed(self) -> None:
        old = self.folder("upload_old", age_hours=48)
        fresh = self.folder("upload_fresh", age_hours=1)
        self.assertEqual(sweep_uploads(self.uploads, max_age_hours=24), 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_only_our_own_scratch_folders_are_considered(self) -> None:
        stranger = self.folder("someone_elses_data", age_hours=500)
        sweep_uploads(self.uploads, max_age_hours=1)
        self.assertTrue(stranger.exists(), "only folders this app creates may be removed")

    def test_a_loose_file_is_left_alone(self) -> None:
        loose = self.uploads / "upload_notes.txt"
        loose.write_text("x", encoding="utf-8")
        sweep_uploads(self.uploads, max_age_hours=0)
        self.assertTrue(loose.exists())


class AuditRotationTests(unittest.TestCase):
    """The audit log was append-only with no rotation, so it grew for the life of the
    install — every approval and risky action, forever."""

    def test_the_log_rotates_once_it_is_large(self) -> None:
        from laptop_agent.audit import AuditLogger

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            logger = AuditLogger(path)
            logger.MAX_BYTES = 2_000
            for index in range(200):
                logger.record("approval", action=f"thing {index}", padding="y" * 40)
            self.assertTrue(path.exists())
            self.assertTrue(path.with_suffix(".jsonl.1").exists(), "no previous generation kept")
            self.assertLess(path.stat().st_size, 20_000)

    def test_events_are_still_readable_after_rotation(self) -> None:
        from laptop_agent.audit import AuditLogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = AuditLogger(Path(tmp) / "audit.jsonl")
            logger.MAX_BYTES = 1_000
            for index in range(120):
                logger.record("approval", action=f"thing {index}", padding="y" * 40)
            recent = logger.tail(5)
            self.assertEqual(len(recent), 5)
            self.assertIn("thing 119", str(recent[-1]))


class CommandLengthTests(unittest.TestCase):
    """A 300,000-character message was accepted and spent 41.5s in the advisor."""

    def test_an_enormous_message_is_refused_with_a_way_forward(self) -> None:
        import asyncio

        from laptop_agent.agents.orchestrator import MAX_COMMAND_CHARS

        from test_orchestrator import OrchestratorTests

        with tempfile.TemporaryDirectory() as raw:
            o = OrchestratorTests.build(OrchestratorTests(), Path(raw))
            result = asyncio.run(o.handle("x" * (MAX_COMMAND_CHARS + 1)))
            self.assertFalse(result.ok)
            self.assertIn("summarize file", result.message)
            self.assertEqual(result.data["limit"], MAX_COMMAND_CHARS)

    def test_a_normal_message_is_untouched(self) -> None:
        import asyncio

        from test_orchestrator import OrchestratorTests

        with tempfile.TemporaryDirectory() as raw:
            o = OrchestratorTests.build(OrchestratorTests(), Path(raw))
            self.assertTrue(asyncio.run(o.handle("memory")).ok)


if __name__ == "__main__":
    unittest.main()
