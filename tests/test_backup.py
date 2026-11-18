from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from laptop_agent import backup
from laptop_agent.access import acting_as
from laptop_agent.accounts import Principal
from laptop_agent.backup import MANIFEST, SnapshotError, create_snapshot, restore_snapshot, verify_snapshot
from laptop_agent.safety import ApprovalRequest, RiskLevel
from test_everyday_requests import Everyday

STAMP = datetime(2026, 11, 14, 10, 30, tzinfo=timezone(timedelta(hours=-5)))


class SnapshotTestCase(unittest.TestCase):
    """A data folder shaped like the real one, credentials and noise included."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "memory.json").write_text(json.dumps({"profile": {"name": "Jeevan"}}), encoding="utf-8")
        (self.data / "reminders.json").write_text(json.dumps([{"id": "r1", "message": "call mum"}]), encoding="utf-8")
        (self.data / "documents").mkdir()
        (self.data / "documents" / "plan.md").write_text("# plan\n", encoding="utf-8")
        # Never in a snapshot: password hashes, sessions and mail tokens.
        (self.data / "accounts.json").write_text('{"accounts": []}', encoding="utf-8")
        (self.data / "sessions.json").write_text("{}", encoding="utf-8")
        (self.data / "email_tokens.json").write_text("{}", encoding="utf-8")
        (self.data / "jobright_session.json").write_text("{}", encoding="utf-8")
        # Neither: log noise, lock files and a user download.
        (self.data / "audit.jsonl").write_text("{}\n", encoding="utf-8")
        (self.data / "knowledge.json.lock").write_text("0", encoding="utf-8")
        (self.data / "downloads").mkdir()
        (self.data / "downloads" / "movie.mp4").write_bytes(b"x")

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_zip(self, path: Path, entries: dict[str, bytes], manifest: dict | None = None) -> Path:
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
            if manifest is not None:
                archive.writestr(MANIFEST, json.dumps(manifest))
        return path

    def _manifest(self, entries: dict[str, bytes], excluded: list[str] | None = None) -> dict:
        return {
            "format": backup.FORMAT,
            "version": backup.VERSION,
            "app_version": "test",
            "created_at": "2026-11-14T10:30:00-05:00",
            "files": [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in entries.items()],
            "excluded": excluded or [],
        }


class WhatGoesIn(SnapshotTestCase):
    def test_a_snapshot_keeps_what_the_app_remembers(self) -> None:
        result = create_snapshot(self.data, now=STAMP)
        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
        self.assertIn("memory.json", names)
        self.assertIn("reminders.json", names)
        self.assertIn("documents/plan.md", names)

    def test_a_snapshot_never_carries_a_credential(self) -> None:
        result = create_snapshot(self.data, now=STAMP)
        with zipfile.ZipFile(result.path) as archive:
            names = set(archive.namelist())
        for secret in backup.NEVER_SNAPSHOT:
            self.assertNotIn(secret, names)
        self.assertNotIn("audit.jsonl", names)
        self.assertNotIn("knowledge.json.lock", names)
        self.assertNotIn("downloads/movie.mp4", names)

    def test_the_manifest_names_what_was_left_out(self) -> None:
        result = create_snapshot(self.data, now=STAMP)
        self.assertIn("accounts.json", result.excluded)
        self.assertIn("email_tokens.json", result.excluded)

    def test_the_manifest_hash_matches_the_bytes_in_the_archive(self) -> None:
        result = create_snapshot(self.data, now=STAMP)
        with zipfile.ZipFile(result.path) as archive:
            manifest = json.loads(archive.read(MANIFEST))
            recorded = {entry["path"]: entry for entry in manifest["files"]}
            data = archive.read("memory.json")
        self.assertEqual(recorded["memory.json"]["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(recorded["memory.json"]["bytes"], len(data))

    def test_two_snapshots_in_the_same_second_do_not_become_one_file(self) -> None:
        first = create_snapshot(self.data, now=STAMP)
        second = create_snapshot(self.data, now=STAMP)
        self.assertNotEqual(first.path, second.path)
        self.assertTrue(first.path.is_file() and second.path.is_file())

    def test_a_folder_with_nothing_in_it_says_so(self) -> None:
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(SnapshotError):
            create_snapshot(empty, now=STAMP)

    @unittest.skipIf(os.name == "nt", "creating a symlink needs a privilege on Windows")
    def test_a_symlink_out_of_the_folder_is_not_followed(self) -> None:
        outside = self.root / "secret.txt"
        outside.write_text("not mine to copy", encoding="utf-8")
        (self.data / "images").mkdir()
        (self.data / "images" / "link.txt").symlink_to(outside)
        result = create_snapshot(self.data, now=STAMP)
        with zipfile.ZipFile(result.path) as archive:
            self.assertNotIn("images/link.txt", archive.namelist())


class Retention(SnapshotTestCase):
    """The backups folder keeps the newest few archives instead of growing forever."""

    def _snapshots(self) -> list[str]:
        folder = self.data / "backups"
        return sorted(path.name for path in folder.iterdir())

    def test_only_the_newest_archives_are_kept(self) -> None:
        written = [create_snapshot(self.data, now=STAMP + timedelta(minutes=i))
                   for i in range(backup.KEEP_ARCHIVES + 2)]
        kept = self._snapshots()
        self.assertEqual(len(kept), backup.KEEP_ARCHIVES)
        self.assertEqual(kept[-1], written[-1].path.name)
        for gone in written[:2]:
            self.assertFalse(gone.path.exists())
        # What survives is still a backup the module accepts, not just a file left behind.
        self.assertTrue(verify_snapshot(written[-1].path).files)

    def test_how_many_to_keep_can_be_chosen(self) -> None:
        for i in range(4):
            create_snapshot(self.data, now=STAMP + timedelta(minutes=i), keep=2)
        self.assertEqual(len(self._snapshots()), 2)

    def test_a_file_that_is_not_a_backup_is_never_deleted(self) -> None:
        folder = self.data / "backups"
        folder.mkdir()
        mine = folder / "notes.txt"
        mine.write_text("a copy I keep here myself", encoding="utf-8")
        whole = folder / "jarvis-backup-copy-of-dads.zip"
        whole.write_bytes(b"not mine")
        for i in range(backup.KEEP_ARCHIVES + 2):
            create_snapshot(self.data, now=STAMP + timedelta(minutes=i))
        self.assertTrue(mine.is_file())
        self.assertTrue(whole.is_file())

    def test_a_folder_that_cannot_be_listed_is_recorded_and_the_backup_stands(self) -> None:
        # Windows hands out a permission error when a scan holds the folder, so listing it
        # is an operation that can fail on its own - after the archive is already good.
        real_iterdir = Path.iterdir

        def unreadable(path: Path):
            if path.name == "backups":
                raise PermissionError(13, "Access is denied")
            return real_iterdir(path)

        with patch.object(Path, "iterdir", unreadable), \
                patch.object(backup, "record_failure") as recorded:
            result = create_snapshot(self.data, now=STAMP)
        self.assertTrue(verify_snapshot(result.path).files)
        self.assertEqual(recorded.call_args.args[0], "backup/retention")
        self.assertIn("could not list", recorded.call_args.args[1])

    def test_an_archive_that_cannot_be_deleted_is_recorded_and_the_backup_stands(self) -> None:
        oldest = create_snapshot(self.data, now=STAMP)
        for i in range(1, backup.KEEP_ARCHIVES):
            create_snapshot(self.data, now=STAMP + timedelta(minutes=i))
        real_unlink = Path.unlink

        def held_open(path: Path, *args, **kwargs):
            if path.name == oldest.path.name:
                raise PermissionError(32, "The process cannot access the file: in use")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", held_open), \
                patch.object(backup, "record_failure") as recorded:
            newest = create_snapshot(self.data, now=STAMP + timedelta(minutes=backup.KEEP_ARCHIVES))
        self.assertTrue(verify_snapshot(newest.path).files)
        self.assertTrue(oldest.path.is_file(), "a file another process holds is still there")
        self.assertEqual(recorded.call_args.args[0], "backup/retention")
        self.assertIn(oldest.path.name, recorded.call_args.args[1])


class Verification(SnapshotTestCase):
    def test_a_fresh_snapshot_verifies(self) -> None:
        result = create_snapshot(self.data, now=STAMP)
        checked = verify_snapshot(result.path)
        self.assertEqual(checked.files, result.files)
        self.assertEqual(checked.created_at, result.created_at)
        self.assertEqual(checked.excluded, result.excluded)

    def test_a_member_edited_after_it_was_written_is_refused(self) -> None:
        original = create_snapshot(self.data, now=STAMP)
        with zipfile.ZipFile(original.path) as archive:
            manifest = json.loads(archive.read(MANIFEST))
            members = {name: archive.read(name) for name in archive.namelist() if name != MANIFEST}
        # Same length, one byte different: only the hash can tell, which is the case a
        # size check alone would wave through.
        changed = bytearray(members["memory.json"])
        changed[-2] ^= 0x01
        members["memory.json"] = bytes(changed)
        tampered = self._write_zip(self.root / "tampered.zip", members, manifest)
        with self.assertRaises(SnapshotError) as caught:
            verify_snapshot(tampered)
        self.assertIn("changed after it was written", str(caught.exception))

    def test_a_member_missing_from_the_archive_is_refused(self) -> None:
        original = create_snapshot(self.data, now=STAMP)
        with zipfile.ZipFile(original.path) as archive:
            manifest = json.loads(archive.read(MANIFEST))
            members = {name: archive.read(name) for name in archive.namelist() if name != MANIFEST}
        members.pop("reminders.json")
        broken = self._write_zip(self.root / "incomplete.zip", members, manifest)
        with self.assertRaises(SnapshotError):
            verify_snapshot(broken)

    def test_a_hand_written_backup_carrying_a_credential_is_refused(self) -> None:
        entries = {"accounts.json": b'{"accounts": [{"password_hash": "scrypt$..."}]}'}
        archive = self._write_zip(self.root / "credential.zip", entries, self._manifest(entries))
        with self.assertRaises(SnapshotError) as caught:
            verify_snapshot(archive)
        self.assertIn("credential", str(caught.exception))

    def test_a_users_own_file_named_like_a_credential_is_not_refused(self) -> None:
        # The credential is the top-level name; a document that happens to share it is
        # the user's own file, and refusing it would refuse a legitimate backup.
        (self.data / "documents" / "accounts.json").write_text('{"mine": true}', encoding="utf-8")
        result = create_snapshot(self.data, now=STAMP)
        self.assertIn("documents/accounts.json", verify_snapshot(result.path).files)

    def test_a_path_that_would_land_outside_the_folder_is_refused(self) -> None:
        entries = {"../escape.txt": b"nope"}
        archive = self._write_zip(self.root / "escape.zip", entries, self._manifest(entries))
        with self.assertRaises(SnapshotError):
            verify_snapshot(archive)

    def test_a_zip_without_a_manifest_is_refused(self) -> None:
        archive = self._write_zip(self.root / "other.zip", {"notes.txt": b"hello"})
        with self.assertRaises(SnapshotError) as caught:
            verify_snapshot(archive)
        self.assertIn("manifest", str(caught.exception))

    def test_a_file_that_is_not_a_zip_is_refused(self) -> None:
        broken = self.root / "broken.zip"
        broken.write_bytes(b"not a zip at all")
        with self.assertRaises(SnapshotError):
            verify_snapshot(broken)

    def test_a_backup_from_another_format_version_is_refused(self) -> None:
        entries = {"memory.json": b"{}"}
        manifest = self._manifest(entries)
        manifest["version"] = backup.VERSION + 1
        archive = self._write_zip(self.root / "future.zip", entries, manifest)
        with self.assertRaises(SnapshotError) as caught:
            verify_snapshot(archive)
        self.assertIn("different version", str(caught.exception))

    def test_a_missing_file_says_where_it_looked(self) -> None:
        with self.assertRaises(SnapshotError) as caught:
            verify_snapshot(self.root / "nowhere.zip")
        self.assertIn("nowhere.zip", str(caught.exception))


class Restoring(SnapshotTestCase):
    def test_restore_writes_every_file_into_an_empty_folder(self) -> None:
        result = create_snapshot(self.data, now=STAMP)
        target = self.root / "restored"
        restored = restore_snapshot(result.path, target)
        self.assertEqual(restored, target)
        self.assertEqual((target / "memory.json").read_bytes(), (self.data / "memory.json").read_bytes())
        self.assertEqual((target / "documents" / "plan.md").read_bytes(),
                         (self.data / "documents" / "plan.md").read_bytes())
        self.assertFalse((target / "accounts.json").exists())

    def test_restore_refuses_a_folder_that_holds_files(self) -> None:
        result = create_snapshot(self.data, now=STAMP)
        target = self.root / "occupied"
        target.mkdir()
        (target / "keep.txt").write_text("live data", encoding="utf-8")
        with self.assertRaises(SnapshotError) as caught:
            restore_snapshot(result.path, target)
        self.assertIn("already holds files", str(caught.exception))
        self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "live data")

    def test_a_refused_archive_leaves_no_partial_folder(self) -> None:
        entries = {"../escape.txt": b"nope"}
        archive = self._write_zip(self.root / "escape.zip", entries, self._manifest(entries))
        target = self.root / "never"
        with self.assertRaises(SnapshotError):
            restore_snapshot(archive, target)
        self.assertFalse(target.exists())
        self.assertEqual([item.name for item in self.root.glob("never.partial-*")], [])


class ThroughTheApp(unittest.TestCase):
    """`backup` typed in the app: developer-only, asked at HIGH, and it really writes one."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.app = Everyday(self.data, llm="none")

    def test_a_personal_account_is_refused_the_command(self) -> None:
        with acting_as(Principal("a1", "friend", "personal")):
            refused, _ran = self.app.say("backup")
        self.assertFalse(refused.ok)
        self.assertIn("needs a developer account", refused.message)
        self.assertEqual(self.app.approvals, [])

    def test_a_developer_is_asked_and_nothing_is_written_when_refused(self) -> None:
        (self.data / "memory.json").write_text("{}", encoding="utf-8")
        result, _ran = self.app.say("backup")
        self.assertIsNone(result, "the harness denies HIGH, so the command must not run")
        self.assertEqual([risk for risk, _ in self.app.approvals], ["high"])
        self.assertEqual(list((self.data / "backups").glob("*.zip")), [])

    def test_the_card_says_only_the_newest_archives_are_kept(self) -> None:
        (self.data / "memory.json").write_text("{}", encoding="utf-8")
        gate = self.app.orchestrator.context.web.approval_gate
        seen: list[ApprovalRequest] = []
        gate._ask = lambda request: (seen.append(request), True)[1]
        self.app.say("backup")
        self.assertEqual(seen[0].risk, RiskLevel.HIGH)
        card = f"{seen[0].action} {seen[0].reason}".lower()
        self.assertIn(f"newest {backup.KEEP_ARCHIVES} archives are kept", card)
        self.assertIn("deletes anything older", card)

    def test_the_reply_says_only_the_newest_archives_are_kept(self) -> None:
        (self.data / "memory.json").write_text("{}", encoding="utf-8")
        gate = self.app.orchestrator.context.web.approval_gate
        gate._ask = lambda request: True
        result, _ran = self.app.say("backup")
        self.assertTrue(result.ok, result.message)
        self.assertIn(f"newest {backup.KEEP_ARCHIVES} archives are kept there", result.message)

    def test_an_approved_backup_writes_an_archive_the_module_can_verify(self) -> None:
        (self.data / "memory.json").write_text('{"profile": {"name": "Jeevan"}}', encoding="utf-8")
        gate = self.app.orchestrator.context.web.approval_gate
        gate._ask = lambda request: True
        result, ran = self.app.say("backup")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(ran, "backup")
        self.assertIn("Backed up", result.message)
        archives = list((self.data / "backups").glob("*.zip"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(verify_snapshot(archives[0]).files, ("memory.json",))


class CommandLine(SnapshotTestCase):
    def test_the_cli_writes_a_backup_for_the_configured_data_folder(self) -> None:
        import io

        out = io.StringIO()
        with patch.dict(os.environ, {"LAPTOP_AGENT_DATA_DIR": str(self.data)}, clear=False):
            code = backup.main(["create"], out=out)
        self.assertEqual(code, 0)
        self.assertIn("Backed up", out.getvalue())
        self.assertIn("credentials", out.getvalue())
        self.assertIn(f"newest {backup.KEEP_ARCHIVES} archives are kept", out.getvalue())
        self.assertEqual(len(list((self.data / "backups").glob("*.zip"))), 1)

    def test_the_cli_can_write_the_archive_somewhere_else(self) -> None:
        import io

        dest = self.root / "usb"
        out = io.StringIO()
        with patch.dict(os.environ, {"LAPTOP_AGENT_DATA_DIR": str(self.data)}, clear=False):
            code = backup.main(["create", "--dest", str(dest)], out=out)
        self.assertEqual(code, 0)
        self.assertEqual(len(list(dest.glob("*.zip"))), 1)
        self.assertFalse((self.data / "backups").exists(), "--dest writes only where it was told")

    def test_the_cli_reports_a_refusal_without_a_traceback(self) -> None:
        import io

        out = io.StringIO()
        code = backup.main(["verify", str(self.root / "missing.zip")], out=out)
        self.assertEqual(code, 1)
        self.assertIn("missing.zip", out.getvalue())


if __name__ == "__main__":
    unittest.main()
