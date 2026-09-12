"""Keep the generated-artifact folders from growing forever.

One day of testing left 7.1MB of generated images, 856KB of documents, and a temporary
upload directory that nothing ever removed. None of it is wrong, but none of it is ever
cleaned either, and a local-first app that quietly eats a user's disk is a bug they only
notice when it is large.

Deliberately conservative. It only touches directories this app writes into, only files
whose extension it produces, and it keeps whatever is recent or small - so a picture from
this morning survives, and nothing outside these folders is ever considered.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from laptop_agent.failures import record_failure


@dataclass(frozen=True)
class Policy:
    """What to keep in one folder. A file survives if it is inside `keep_newest` OR
    younger than `max_age_days` - so a quiet week does not wipe the folder, and a busy
    day does not fill the disk."""

    folder: str
    suffixes: tuple[str, ...]
    keep_newest: int
    max_age_days: float


POLICIES = (
    Policy("images", (".png", ".jpg", ".jpeg", ".webp"), keep_newest=60, max_age_days=30),
    Policy("documents", (".pdf", ".docx", ".md"), keep_newest=40, max_age_days=30),
    Policy("downloads", (), keep_newest=10_000, max_age_days=3650),  # the user asked for these
    Policy("resumes", (".pdf", ".html"), keep_newest=30, max_age_days=90),
)


def sweep(data_dir: Path, policies=POLICIES, now: float | None = None) -> dict[str, object]:
    """Delete what no policy keeps. Returns what happened, per folder."""
    moment = now if now is not None else time.time()
    removed: dict[str, int] = {}
    freed = 0
    for policy in policies:
        directory = Path(data_dir) / policy.folder
        if not directory.is_dir():
            continue
        try:
            entries = [p for p in directory.iterdir() if p.is_file()]
        except OSError as exc:
            record_failure("retention/list", exc, folder=policy.folder)
            continue
        if policy.suffixes:
            entries = [p for p in entries if p.suffix.lower() in policy.suffixes]
        entries.sort(key=lambda p: _mtime(p), reverse=True)
        cutoff = moment - policy.max_age_days * 86400
        count = 0
        for index, path in enumerate(entries):
            if index < policy.keep_newest or _mtime(path) >= cutoff:
                continue
            try:
                size = path.stat().st_size
                path.unlink()
            except OSError as exc:
                record_failure("retention/unlink", exc, path=path.name)
                continue
            count += 1
            freed += size
        if count:
            removed[policy.folder] = count
    return {"removed": removed, "freed_bytes": freed}


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def sweep_uploads(upload_dir: Path, max_age_hours: float = 24.0, now: float | None = None) -> int:
    """Remove stale upload scratch directories.

    Every upload creates its own mkdtemp folder and nothing ever removed them, so the
    temp directory accumulated one per attachment for the life of the machine.
    """
    moment = now if now is not None else time.time()
    directory = Path(upload_dir)
    if not directory.is_dir():
        return 0
    cutoff = moment - max_age_hours * 3600
    removed = 0
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        record_failure("retention/uploads", exc, path=str(directory))
        return 0
    for child in children:
        # Only the scratch folders this app makes, never a stray file someone put here.
        if not child.is_dir() or not child.name.startswith("upload_"):
            continue
        if _mtime(child) >= cutoff:
            continue
        try:
            shutil.rmtree(child, ignore_errors=False)
            removed += 1
        except OSError as exc:
            record_failure("retention/rmtree", exc, path=child.name)
    return removed
