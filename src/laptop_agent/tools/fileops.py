"""`file move|copy <src> to <dst>`, `file rename <src> to <name>`, `file delete <path>`, `undo file`.

"move report.pdf to my desktop" and "delete old-notes.txt" had no tool: only reading, searching and
organising a folder were built, so the model reached for the shell. Each change here asks first
(HIGH), never overwrites, keeps out of Windows' own folders, and is written to a journal so "undo
that" can put it back: a move or rename moves back, a copy is removed. A deletion goes to the
Recycle Bin, where the user restores it; it is not erased.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.storage import atomic_write_text, file_lock, read_json
from laptop_agent.tools.base import ToolResult

JOURNAL_KEPT = 20
PLACES = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "pictures": "Pictures",
          "photos": "Pictures", "music": "Music", "videos": "Videos", "home": ""}
_DRIVE_FIXED = 3
Recycler = Callable[[Path], None]


def recycle_windows(path: Path, kernel32=None, shell32=None) -> None:
    """Send `path` to the Recycle Bin, never erase it.

    Only a fixed local drive has a Recycle Bin: on a USB stick or a network share Windows deletes
    for good, and FOF_NOCONFIRMATION used to answer its "delete permanently?" prompt with yes while
    the card promised a restore (review of #287). Those are refused before anything happens, and
    FOF_WANTNUKEWARNING still makes Windows ask if it cannot recycle for another reason (too big)."""
    kernel32 = kernel32 or ctypes.windll.kernel32
    shell32 = shell32 or ctypes.windll.shell32
    text = str(path)
    if text.startswith(("\\\\", "//")):
        raise OSError(f"{path} is on a network share, which has no Recycle Bin, so I won't delete it from here.")
    root = os.path.splitdrive(text)[0] + "\\"
    if kernel32.GetDriveTypeW(root) != _DRIVE_FIXED:
        raise OSError(f"{root} has no Recycle Bin (a removable or network drive), so deleting there would be "
                      "permanent. I won't do it from here.")

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]

    # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI | WANTNUKEWARNING
    fo_delete, flags = 3, 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x4000
    operation = SHFILEOPSTRUCTW(None, fo_delete, text + "\0", None, flags, 0, None, None)
    code = shell32.SHFileOperationW(ctypes.byref(operation))
    if code or operation.fAnyOperationsAborted:
        raise OSError(f"Windows could not move it to the Recycle Bin (code {code}).")


def system_recycler() -> Recycler | None:
    return recycle_windows if sys.platform == "win32" else None


def _identity(path: Path) -> list[int | str]:
    """Which object is at `path`, so undo never acts on something else put there since. A file's
    modified time is part of it: ext4 hands a deleted file's inode number to the next new file. A
    folder's is everything in it, so undoing a copy never recycles work added inside the copy."""
    stat = path.stat()
    if not path.is_dir():
        return [stat.st_dev, stat.st_ino, stat.st_mtime_ns]
    tree = hashlib.sha256()
    for root, folders, files in os.walk(path):
        folders.sort()
        for name in sorted(folders + files):
            entry = os.lstat(os.path.join(root, name))
            relative = os.path.relpath(os.path.join(root, name), path)
            tree.update(f"{relative}|{entry.st_size}|{entry.st_mtime_ns}\n".encode("utf-8", "surrogateescape"))
    return [stat.st_dev, stat.st_ino, tree.hexdigest()]


def _protected(path: Path, home: Path | None = None) -> bool:
    """Windows' own folders, a drive's root, the folder holding every user's profile, the home
    folder and its own standard folders (Desktop, Documents, ...) are never changed: "delete
    C:\\Users" or "delete Desktop" was one ordinary card away (review of #287)."""
    home = (home or Path.home()).resolve()
    standard = {home / folder for folder in set(PLACES.values()) if folder}
    if path.parent == path or path in {home, home.parent} or path in standard:
        return True
    roots = [os.environ.get("SystemRoot") or r"C:\Windows", os.environ.get("ProgramFiles") or r"C:\Program Files",
             os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)", os.environ.get("ProgramData") or r"C:\ProgramData"]
    text = str(path).lower()
    return any(text == root.lower() or text.startswith(root.lower().rstrip("\\") + "\\") for root in roots if root)


class FileOps:
    def __init__(self, approval_gate: ApprovalGate, journal: Path, home: Path | None = None,
                 recycler: Recycler | None = None) -> None:
        self._gate = approval_gate
        self._journal = journal
        self._home = home or Path.home()
        self._recycle = recycler

    def _places(self) -> list[Path]:
        return [self._home / "Desktop", self._home / "Documents", self._home / "Downloads", self._home]

    def _place(self, words: str) -> Path | None:
        cleaned = " ".join(words.lower().replace("folder", " ").split())
        cleaned = cleaned.removeprefix("the ").removeprefix("my ").strip()
        if cleaned in PLACES:
            return self._home / PLACES[cleaned] if PLACES[cleaned] else self._home
        return None

    def find(self, words: str) -> tuple[Path | None, str]:
        """An existing file or folder for `words`, or None and why. "my resume.pdf" is resume.pdf."""
        raw = words.strip().strip("\"'")
        if not re.match(r"[A-Za-z]:|[\\/~]", raw):
            raw = re.sub(r"^(?:(?:my|the|this|that|a|an)\s+)+", "", raw, flags=re.IGNORECASE)
        path = Path(raw).expanduser()
        if path.is_absolute():
            return (path, "") if path.exists() else (None, f"There's nothing at {path}.")
        found = [place / raw for place in self._places() if (place / raw).exists()]
        if not found:
            return None, f"I couldn't find {raw!r} on your Desktop, in Documents or Downloads. Give me its full path."
        if len(found) > 1:
            return None, f"There's more than one {raw!r}: " + ", ".join(str(item) for item in found) + ". Which one?"
        return found[0], ""

    def _destination(self, source: Path, words: str) -> Path | ToolResult:
        """Where the source lands. A folder that does not exist is refused: taken as the new name,
        "copy budget.xlsx to D:\\backup" made a file called backup with no extension."""
        place = self._place(words)
        if place is not None:
            return place / source.name
        written = words.strip().strip("\"'")
        target = Path(written).expanduser()
        if not target.is_absolute():
            target = source.parent / target
        if target.is_dir():
            return target / source.name
        if written.endswith(("\\", "/")) or (source.is_file() and source.suffix and not target.suffix):
            return ToolResult.failure(f"There's no folder {target}. Make it first, or name the file as well.")
        return target

    def _record(self, entry: dict[str, str]) -> None:
        with file_lock(self._journal):
            history = read_json(self._journal, [])
            history = (history if isinstance(history, list) else []) + [entry]
            atomic_write_text(self._journal, json.dumps(history[-JOURNAL_KEPT:], indent=2))

    def _checked(self, *paths: Path) -> ToolResult | None:
        for path in paths:
            if _protected(path.resolve(), self._home):
                return ToolResult.failure(f"I won't change {path}: it's a system folder, a drive's root or your home folder.")
        return None

    def move(self, src: str, dst: str, copy: bool = False) -> ToolResult:
        source, why = self.find(src)
        if source is None:
            return ToolResult.failure(why)
        target = self._destination(source, dst)
        if isinstance(target, ToolResult):
            return target
        if refused := self._checked(source, target):
            return refused
        if target.exists():
            return ToolResult.failure(f"{target} already exists, and I never overwrite. Pick another name or place.")
        if not target.parent.is_dir():
            return ToolResult.failure(f"There's no folder {target.parent}.")
        verb = "Copy" if copy else "Move"
        self._gate.require(ApprovalRequest(action=f"{verb} {source} to {target}", risk=RiskLevel.HIGH,
                                           reason=f"This {'writes a copy' if copy else 'moves the file'}; \"undo that\" reverses it.",
                                           preview=f"From: {source}\nTo:   {target}"))
        # Again after the card, which can wait two minutes: something may have arrived there since.
        if target.exists():
            return ToolResult.failure(f"{target} appeared while you were deciding, and I never overwrite.")
        if copy:
            (shutil.copytree if source.is_dir() else shutil.copy2)(source, target)
        else:
            shutil.move(str(source), str(target))
        self._record({"op": "copy" if copy else "move", "src": str(source), "dst": str(target), "id": _identity(target),
                      "at": datetime.now().astimezone().isoformat()})
        return ToolResult.success(f"{'Copied' if copy else 'Moved'} {source.name} to {target}.", src=str(source), dst=str(target))

    def rename(self, src: str, name: str) -> ToolResult:
        source, why = self.find(src)
        if source is None:
            return ToolResult.failure(why)
        if refused := self._checked(source):
            return refused
        new_name = name.strip().strip("\"'")
        if not new_name or any(sep in new_name for sep in "\\/:*?\"<>|"):
            return ToolResult.failure("Give a new name without a folder in it, for example \"rename notes.txt to ideas.txt\".")
        # The extension is kept unless the new name gives one: "Q3 v2.1" is not a ".1" file.
        if source.suffix and source.is_file() and not re.fullmatch(r"\.[A-Za-z][A-Za-z0-9]{0,5}", Path(new_name).suffix):
            new_name += source.suffix
        target = source.with_name(new_name)
        # Only the capitals change: on Windows the "existing" target is the file itself.
        if target.exists() and not (target.name.lower() == source.name.lower() and target.samefile(source)):
            return ToolResult.failure(f"{target.name} already exists there, and I never overwrite.")
        self._gate.require(ApprovalRequest(action=f"Rename {source} to {target.name}", risk=RiskLevel.HIGH,
                                           reason="\"undo that\" renames it back.", preview=f"{source.name} -> {target.name}"))
        source.rename(target)
        self._record({"op": "move", "src": str(source), "dst": str(target), "id": _identity(target), "at": datetime.now().astimezone().isoformat()})
        return ToolResult.success(f"Renamed {source.name} to {target.name}.", src=str(source), dst=str(target))

    def delete(self, src: str) -> ToolResult:
        if self._recycle is None:
            return ToolResult.failure("Deleting to the Recycle Bin is built for Windows only so far.")
        source, why = self.find(src)
        if source is None:
            return ToolResult.failure(why)
        if refused := self._checked(source):
            return refused
        kind = "folder and everything in it" if source.is_dir() else "file"
        self._gate.require(ApprovalRequest(action=f"Delete {source} (to the Recycle Bin)", risk=RiskLevel.HIGH,
                                           reason=f"The {kind} goes to the Recycle Bin, where you can restore it.",
                                           preview=str(source)))
        self._recycle(source)
        self._record({"op": "delete", "src": str(source), "at": datetime.now().astimezone().isoformat()})
        return ToolResult.success(f"Moved {source.name} to the Recycle Bin.", src=str(source))

    def _drop(self, entry: dict) -> None:
        with file_lock(self._journal):
            current = read_json(self._journal, [])
            if isinstance(current, list) and entry in current:
                current.remove(entry)
                atomic_write_text(self._journal, json.dumps(current, indent=2))

    def undo(self, op: str | None = None) -> ToolResult:
        """The newest change - or the newest of the kind named ("undo the last rename"), which a
        delete on top used to block for good: its entry was never removed."""
        history = read_json(self._journal, [])
        if not isinstance(history, list) or not history:
            return ToolResult.failure("There's no file change of mine to undo.")
        kinds = {"move": ("move",), "rename": ("move",), "copy": ("copy",)}.get(op or "", None)
        last = next((entry for entry in reversed(history) if kinds is None or entry.get("op") in kinds), None)
        if last is None:
            return ToolResult.failure(f"There's no {op} of mine to undo.")
        src, dst = Path(last["src"]), Path(last.get("dst") or "")
        if last["op"] == "delete":
            self._drop(last)   # said once; the next "undo" reaches the change before it
            return ToolResult.failure(f"{src.name} is in the Recycle Bin: open it and choose Restore to put it back "
                                      f"at {src.parent}.")
        if not dst.exists():
            return ToolResult.failure(f"{dst} isn't there any more, so I can't undo that.")
        if last.get("id") != _identity(dst):
            return ToolResult.failure(f"{dst} isn't the one I {'copied' if last['op'] == 'copy' else 'moved'} any more "
                                      "(it was replaced or changed since), so I left it.")
        if last["op"] == "move":
            if src.exists():
                return ToolResult.failure(f"Something is already at {src}, so moving it back would overwrite it.")
            self._gate.require(ApprovalRequest(action=f"Move {dst} back to {src}", risk=RiskLevel.HIGH,
                                               reason="Undoing the last move or rename.", preview=f"{dst} -> {src}"))
            if src.exists():
                return ToolResult.failure(f"Something arrived at {src} while you were deciding, so I left it.")
            shutil.move(str(dst), str(src))
            message = f"Put {src.name} back at {src}."
        else:
            if self._recycle is None:
                return ToolResult.failure("Removing the copy needs the Recycle Bin, built for Windows only so far.")
            self._gate.require(ApprovalRequest(action=f"Delete the copy {dst} (to the Recycle Bin)", risk=RiskLevel.HIGH,
                                               reason="Undoing the last copy; the original stays.", preview=str(dst)))
            self._recycle(dst)
            message = f"Removed the copy at {dst} (it's in the Recycle Bin)."
        self._drop(last)
        return ToolResult.success(message)
