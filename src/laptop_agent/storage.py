"""Atomic local storage with recovery copies and serialized read/modify/write operations."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()
_held = threading.local()
_warnings: dict[str, str] = {}


@contextmanager
def file_lock(path: Path):
    key = str(path.resolve())
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, "paths", set())
        if key in held:
            yield
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with Path(key + ".lock").open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX)
            _held.paths = held | {key}
            try:
                yield
            finally:
                _held.paths = held
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)


_REFRESH = {
    "SchedulerStore": {"add", "remove", "set_enabled", "list_jobs", "due_jobs", "claim_due_jobs", "mark_ran"},
    "MemoryStore": {"set_profile_value", "add_note", "get_profile", "dump"},
    "JobTracker": {"add", "import_leads", "update", "set_resume", "set_resume_profile", "set_tailoring", "set_tailored_pdf", "clear_leads", "remove", "list", "get", "get_resume", "stats"},
    "TaskTracker": {"record_run", "latest", "all_runs"},
    "AutopilotTracker": {"record_run", "latest", "all_runs"},
    "WorkflowTracker": {"record_run", "latest", "all_runs"},
    "AgentRunTracker": {"record_run", "latest", "all_runs"},
}


def synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        path = getattr(self, "path", None) or getattr(self, "storage_path", None)
        if path is None:
            return method(self, *args, **kwargs)
        with file_lock(Path(path)):
            # Refresh only the outer operation; nested get() calls must see pending edits.
            active = getattr(_held, "operations", set())
            key = str(Path(path).resolve())
            outer = key not in active
            _held.operations = active | {key}
            try:
                if outer and method.__name__ in _REFRESH.get(type(self).__name__, set()):
                    loader = getattr(self, "_load", None) or getattr(self, "load", None)
                    loader()
                return method(self, *args, **kwargs)
            finally:
                _held.operations = active
    return wrapped


def _replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    with file_lock(path):
        if path.exists():
            previous = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                try:
                    json.loads(previous)
                except ValueError:
                    _preserve_corrupt(path)
                else:
                    _replace_text(Path(str(path) + ".bak"), previous)
            else:
                _replace_text(Path(str(path) + ".bak"), previous)
        _replace_text(path, text)


def _preserve_corrupt(path: Path) -> None:
    raw = path.read_bytes()
    backup = Path(str(path) + ".corrupt-" + hashlib.sha256(raw).hexdigest()[:12])
    if not backup.exists():
        backup.write_bytes(raw)
    with _registry_lock:
        _warnings[str(path)] = f"Recovered {path.name}; original bytes preserved in {backup.name}. Review the recovery copy."


def read_json(path: Path, default):
    path = Path(path)
    with file_lock(path):
        if not path.exists():
            return deepcopy(default)
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(result, type(default)):
                raise ValueError("Invalid storage root")
            return result
        except (ValueError, UnicodeError):
            _preserve_corrupt(path)
            try:
                result = json.loads(Path(str(path) + ".bak").read_text(encoding="utf-8"))
                if isinstance(result, type(default)):
                    return result
            except (OSError, ValueError):
                pass
            return deepcopy(default)


def positive_int(value, default=1):
    try:
        return max(1, int(value))
    except (ValueError, TypeError, OverflowError):
        return default


def storage_warnings() -> list[str]:
    with _registry_lock:
        return list(_warnings.values())
