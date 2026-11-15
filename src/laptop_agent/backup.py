"""Portable snapshots of what this app remembers, and a restore that cannot overwrite.

Memory, reminders, schedules, lists, the knowledge index, generated documents and
pictures all live in one folder of JSON files. Until now the only copy was the one on
the laptop: a dead disk or a new machine lost everything, and the app's own advice for
a damaged store was to move the file aside. This writes one verified ``.zip`` of what
is worth keeping and restores it into a folder that does not exist yet.

An allow-list, never a deny-list. The same folder holds credentials - ``accounts.json``,
``sessions.json``, ``email_tokens.json``, ``jobright_session.json`` - and a list of names
to skip would carry the next secret somebody adds. A backup that quietly included
password hashes would also be a backup nobody should store off the machine, which is
exactly what people do with backups. So ``SNAPSHOT_FILES`` names what goes in, the
manifest records every file with its SHA-256, and :func:`verify_snapshot` refuses an
archive whose bytes no longer match it.

Restore is additive on purpose: into a target that is absent or empty, never over a
live store. The one time ``--overwrite`` would be used is the one time the path is
wrong. Chat transcripts are not here either - the page keeps those in its own browser
storage, not in this folder.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from laptop_agent import __version__
from laptop_agent.failures import record_failure
from laptop_agent.tools.base import reserve_new_path

FORMAT = "jarvis-backup"
VERSION = 1
MANIFEST = "manifest.json"
CHUNK = 1024 * 1024

# What is worth keeping: what the user told the app, asked it to remember, or had it
# write. file_journal.json and watches.json are decisions ("undo that", a standing
# watch), so they travel with the data they describe.
SNAPSHOT_FILES = (
    "memory.json",
    "reminders.json",
    "scheduler.json",
    "tasks.json",
    "workflows.json",
    "jobs.json",
    "knowledge.json",
    "autopilot.json",
    "agent_runs.json",
    "file_journal.json",
    "watches.json",
)
SNAPSHOT_FOLDERS = ("documents", "images", "recordings", "resumes")

# Refused inside an archive even if a hand-written manifest lists them, and named in
# every manifest so the file says what it left behind.
NEVER_SNAPSHOT = ("accounts.json", "sessions.json", "email_tokens.json", "jobright_session.json")

# A corrupt or hostile archive must not be able to fill the disk or the memory of a
# machine whose whole point is running on modest hardware.
MAX_FILES = 10_000
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class SnapshotError(RuntimeError):
    """A snapshot that cannot be written, trusted or restored. Safe to show."""


@dataclass(frozen=True)
class Snapshot:
    path: Path
    created_at: str
    files: tuple[str, ...]
    # Credentials the folder held and this archive left out, so the reply can say so.
    excluded: tuple[str, ...]


def _members(data_dir: Path) -> list[tuple[Path, str]]:
    """Every file to keep, with the name it carries inside the archive."""
    members: list[tuple[Path, str]] = []
    for name in SNAPSHOT_FILES:
        path = data_dir / name
        if path.is_file():
            members.append((path, name))
    for folder in SNAPSHOT_FOLDERS:
        root = data_dir / folder
        if not root.is_dir():
            continue
        # A symlink is skipped, never followed: the target may sit outside the folder
        # and copying it would put something in the archive nobody chose to back up.
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                members.append((path, path.relative_to(data_dir).as_posix()))
    return members


def create_snapshot(data_dir: Path, dest_dir: Path | None = None,
                    now: datetime | None = None) -> Snapshot:
    """Write one archive of the durable data. The destination defaults to ``<data>/backups``."""
    data_dir = Path(data_dir)
    members = _members(data_dir)
    if not members:
        raise SnapshotError(f"There is nothing to back up yet in {data_dir}: no memory, reminders or files.")
    total = sum(path.stat().st_size for path, _ in members)
    if len(members) > MAX_FILES or total > MAX_TOTAL_BYTES:
        raise SnapshotError(f"That folder holds {len(members)} files and {total // (1024 * 1024)} MB, "
                            "which is more than one backup should take. Move large files out and try again.")

    created = (now or datetime.now()).astimezone()
    destination = Path(dest_dir) if dest_dir is not None else data_dir / "backups"
    destination.mkdir(parents=True, exist_ok=True)
    # Claimed exclusively, so two backups in the same second cannot become one file.
    target = reserve_new_path(destination, f"jarvis-backup-{created:%Y%m%d-%H%M%S}", ".zip")

    handle, staged = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=destination)
    os.close(handle)
    staged_path = Path(staged)
    written: list[dict[str, object]] = []
    try:
        with zipfile.ZipFile(staged_path, "w", zipfile.ZIP_DEFLATED) as archive:
            # Hashed as it is written, from the same bytes: a store replaced between a
            # separate hash and the write would otherwise carry a manifest that no
            # longer describes the archive, and verify would refuse a good backup.
            for path, name in members:
                digest, size = hashlib.sha256(), 0
                with path.open("rb") as source, archive.open(name, "w") as entry:
                    for chunk in iter(lambda: source.read(CHUNK), b""):
                        digest.update(chunk)
                        size += len(chunk)
                        entry.write(chunk)
                written.append({"path": name, "bytes": size, "sha256": digest.hexdigest()})
            manifest = {
                "format": FORMAT,
                "version": VERSION,
                "app_version": __version__,
                "created_at": created.isoformat(timespec="seconds"),
                "files": written,
                "excluded": [name for name in NEVER_SNAPSHOT if (data_dir / name).exists()],
            }
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2, sort_keys=True))
        # "rb+": Windows refuses fsync on a read-only handle (Errno 9), and the archive
        # must be on disk before os.replace publishes it.
        with staged_path.open("rb+") as staged_file:
            os.fsync(staged_file.fileno())
        os.replace(staged_path, target)
    except OSError as exc:
        raise SnapshotError(f"Could not write the backup: {exc}") from exc
    finally:
        staged_path.unlink(missing_ok=True)

    return Snapshot(path=target, created_at=manifest["created_at"],
                    files=tuple(str(entry["path"]) for entry in written),
                    excluded=tuple(manifest["excluded"]))


def _unsafe_name(name: str) -> bool:
    """A name that could land outside the folder it is restored into."""
    if not name or "\\" in name or name.startswith("/"):
        return True
    parts = PurePosixPath(name).parts
    return any(part in {"..", "."} or ":" in part for part in parts)


def _manifest_of(archive: zipfile.ZipFile) -> dict:
    try:
        raw = archive.read(MANIFEST)
    except KeyError as exc:
        raise SnapshotError("That file is not one of my backups: it has no manifest.") from exc
    try:
        manifest = json.loads(raw)
    except ValueError as exc:
        raise SnapshotError("That backup's manifest cannot be read.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise SnapshotError("That file is not one of my backups.")
    if manifest.get("version") != VERSION:
        raise SnapshotError(f"That backup was written by a different version (format {manifest.get('version')!r}).")
    if not isinstance(manifest.get("files"), list) or not isinstance(manifest.get("created_at"), str):
        raise SnapshotError("That backup's manifest does not describe any files.")
    return manifest


def verify_snapshot(path: Path) -> Snapshot:
    """Re-read an archive and check every byte against its manifest before trusting it."""
    path = Path(path)
    if not path.is_file():
        raise SnapshotError(f"There is no backup at {path}.")
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest_of(archive)
            listed: dict[str, dict] = {}
            for entry in manifest["files"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    raise SnapshotError("That backup's manifest lists a file it cannot describe.")
                listed[entry["path"]] = entry
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if any(_unsafe_name(name) for name in names):
                raise SnapshotError("That backup holds a file path that would land outside the folder I restore into.")
            if set(names) != set(listed) | {MANIFEST}:
                raise SnapshotError("That backup's contents do not match its manifest, so it was changed after it was written.")
            if len(listed) > MAX_FILES:
                raise SnapshotError("That backup holds more files than a backup should.")
            total = 0
            for name, entry in listed.items():
                total += archive.getinfo(name).file_size
                if total > MAX_TOTAL_BYTES:
                    raise SnapshotError("That backup unpacks to more data than a backup should.")
                digest, size = hashlib.sha256(), 0
                # Streamed, so verifying a large document or recording costs one chunk of
                # memory rather than its whole size twice.
                with archive.open(name) as member:
                    for chunk in iter(lambda: member.read(CHUNK), b""):
                        digest.update(chunk)
                        size += len(chunk)
                if size != entry.get("bytes"):
                    raise SnapshotError(f"That backup's {name} is not the size it was written with.")
                if digest.hexdigest() != entry.get("sha256"):
                    raise SnapshotError(f"That backup's {name} is damaged: its contents changed after it was written.")
            for bad in NEVER_SNAPSHOT:
                # Only the top-level name is the credential: `documents/accounts.json` is
                # the user's own file, and refusing it would refuse a legitimate backup.
                if bad in listed:
                    # A backup holding a credential is one nobody should keep; refuse it
                    # rather than restore it and call it safe.
                    raise SnapshotError(f"That backup contains {bad}, which is a credential and never belongs in one.")
    except zipfile.BadZipFile as exc:
        raise SnapshotError(f"That file is not a readable backup: {exc}.") from exc
    # Manifest order, not sorted: it is the order the files were taken in.
    return Snapshot(path=path, created_at=manifest["created_at"],
                    files=tuple(entry["path"] for entry in manifest["files"]),
                    excluded=tuple(str(name) for name in manifest.get("excluded") or ()))


def restore_snapshot(path: Path, target_dir: Path) -> Path:
    """Unpack a verified backup into a folder that does not exist, or is empty. Returns it."""
    verify_snapshot(path)
    target = Path(target_dir)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise SnapshotError(f"{target} already holds files. Restore into a new or empty folder, "
                            "then point LAPTOP_AGENT_DATA_DIR at it.")
    staging = target.with_name(f"{target.name}.partial-{secrets.token_hex(4)}")
    try:
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if name == MANIFEST or name.endswith("/"):
                    continue
                destination = staging / PurePosixPath(name)
                if staging.resolve() not in destination.resolve().parents:
                    raise SnapshotError(f"That backup's {name} would land outside the folder I restore into.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rmdir()          # checked empty above; absent is the common case
        os.replace(staging, target)
    except OSError as exc:
        raise SnapshotError(f"Could not restore into {target}: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
            if staging.exists():
                # Windows can hold files open; the reply must not claim a clean refusal.
                record_failure("backup/cleanup", f"could not remove {staging}")
    return target


def main(argv: list[str] | None = None, out=sys.stdout) -> int:
    """``python -m laptop_agent.backup create|verify|restore``."""
    parser = argparse.ArgumentParser(prog="python -m laptop_agent.backup",
                                     description="Back up, check and restore J.A.R.V.I.S's own data.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="write one archive of memory, reminders, lists and files")
    create.add_argument("--dest", help="folder for the archive (default: <data dir>/backups)")
    commands.add_parser("verify", help="check an archive against its manifest").add_argument("path")
    restore = commands.add_parser("restore", help="unpack an archive into a new or empty folder")
    restore.add_argument("path")
    restore.add_argument("target")
    args = parser.parse_args(argv)

    from laptop_agent.config import load_config

    try:
        if args.command == "create":
            result = create_snapshot(load_config().data_dir, Path(args.dest) if args.dest else None)
            print(f"Backed up {len(result.files)} file(s), {result.path.stat().st_size // 1024} KB, "
                  f"to {result.path}.", file=out)
            if result.excluded:
                print("Left out, because they are credentials: " + ", ".join(result.excluded), file=out)
        elif args.command == "verify":
            result = verify_snapshot(Path(args.path))
            print(f"{result.path} is intact: {len(result.files)} file(s), written {result.created_at}.", file=out)
        else:
            print(f"Restored into {restore_snapshot(Path(args.path), Path(args.target))}.", file=out)
    except SnapshotError as exc:
        print(str(exc), file=out)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
