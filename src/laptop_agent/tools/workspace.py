"""`workspace <request>` - a folder, and the files asked for in it.

"create a workspace folder and fill files with python basics" was answered by the document tool,
the nearest thing the model router could find: one Markdown file in the app's own data folder.
Nothing could create a folder or write several files. Here the folder's name and place are read
from the words themselves, the model only plans the files (as JSON), every path it returns is
checked, and one approval card lists the folder and every file before anything is written.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Callable

from laptop_agent.failures import record_failure
from laptop_agent.planner.core import CutOff
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

Writer = Callable[[str], str]

MAX_FILES = 12
MAX_DEPTH = 4
# What the writer can return: 6,000 tokens is about 24,000 characters, so a bigger budget only
# promised what a cut-off reply could never deliver (review of #282).
MAX_FILE_CHARS = 12_000
MAX_TOTAL_CHARS = 20_000
PREVIEW_CHARS = 3_000

_HOME_PLACES = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "home": ""}
_NAMED = re.compile(r"\b(?:called|named|with\s+the\s+name)\s+"
                    r"[\"']?(?P<name>[\w][\w .-]{0,39}?)[\"']?"
                    r"(?=\s+(?:on|in|at|under)\b|\s*[,.!?]|\s*$)", re.IGNORECASE)
_KIND = re.compile(r"\b(?:a|an|my|the)\s+(?:new\s+)?(?P<name>[a-z][\w-]{0,30}(?:\s+[a-z][\w-]{0,30})?)\s+(?:folder|directory)\b",
                   re.IGNORECASE)
# Words that describe a folder rather than name it: "an empty folder" is not called "empty".
_NOT_NAMES = {"new", "empty", "blank", "fresh", "project", "folder", "directory", "workspace"}
_PLACE = re.compile(r"\b(?:on|in|at|under|inside)\s+(?:my\s+|the\s+)?(?P<home>desktop|documents|downloads|home)"
                    r"(?:\s+folder)?\b", re.IGNORECASE)
# A path: quoted (spaces allowed) or bare up to whitespace, with sentence punctuation left off.
_PATH = re.compile(r"\b(?:in|at|under|inside)\s+(?:\"(?P<quoted>[^\"]+)\"|(?P<path>[A-Za-z]:[\\/][^\s,]*|~[\\/][^\s,]*|/[^\s,]+))",
                   re.IGNORECASE)
# What the folder should hold, when anything: "and fill files with python basics", "with notes on rust".
_CONTENTS = re.compile(r"\b(?:with(?!\s+the\s+name\b)|and\s+(?:fill|put|add|write|create)|fill(?:ed)?\s+(?:it|with)"
                       r"|containing|full\s+of|for\s+(?:learning|practising|practicing|studying))\b", re.IGNORECASE)
# One plain name per part: no drive, no "..", no leading dot, nothing a shell or Windows treats specially.
_PART = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_. -]{0,62}[A-Za-z0-9_])?")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}
# Files Windows acts on by being opened or merely shown in Explorer - a .url or .scf can send the
# user's login hash to a server named in it - or that run on a double-click. The model's plan comes
# from a prompt that can carry web text, so none of these is ever written (review of #282).
_ACTING = {"url", "lnk", "scf", "library-ms", "searchconnector-ms", "desktop", "hta", "vbs", "vbe", "wsf", "wsh",
           "bat", "cmd", "ps1", "psm1", "reg", "exe", "dll", "msi", "com", "scr", "pif", "jar", "cpl", "inf", "appref-ms"}

_PROMPT = """You are setting up a folder of files for this request:

{request}

Reply with ONLY a JSON object in exactly this shape:
{{"files": [{{"path": "relative/path.ext", "content": "the whole file"}}]}}

Rules:
- At most {max_files} files, at most {max_file:,} characters each and {max_total:,} in all - fewer, shorter files
  beat a reply that is cut off.
- Paths are relative, use "/" between folders, at most {max_depth} levels, and never start with "/" or contain "..".
- Names use plain English letters, digits, "_", "-", "." and spaces, and never start with a dot (no .gitignore).
- Only source code and text files: no shortcuts, scripts that run on a double-click, or system files.
- Every file is complete and correct; code runs as written and is commented for a learner where that helps.
"""


def _head(request: str) -> str:
    """The words before what the folder should hold: a place or a name in the contents ("notes on
    home loans", "a script that cleans files in my downloads") is not where the folder goes."""
    contents = _CONTENTS.search(request)
    return request[:contents.start()] if contents else request


def folder_and_place(request: str, home: Path) -> tuple[str, Path]:
    """The folder's name and the place it goes, read from the words before the contents: "a folder
    called notes on my desktop" -> ("notes", ~/Desktop). Defaults: "workspace", in ~/Documents."""
    head = _head(request)
    named = _NAMED.search(head)             # "notes on a book called dune" names no folder
    if named is None:
        kind = _KIND.search(head)
        name = kind.group("name").strip() if kind else ""
        name = "workspace" if not name or name.lower() in _NOT_NAMES | set(_HOME_PLACES) else name
    else:
        name = named.group("name").strip()  # said outright: kept, even "downloads"
    name = re.sub(r"[^\w .-]+", "", name).strip(" .") or "workspace"
    path = _PATH.search(head)
    if path:
        written = (path.group("quoted") or path.group("path")).rstrip("?.!,;:")
        return name, Path(written).expanduser()
    place = _PLACE.search(head)
    sub = _HOME_PLACES.get(place.group("home").lower(), "Documents") if place else "Documents"
    return name, (home / sub) if sub else home


def safe_relative(path: object) -> str | None:
    """A model-given path as a plain relative path, or None when it is anything else."""
    if not isinstance(path, str):
        return None                         # a JSON null became a file called "None"
    cleaned = path.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        return None
    parts = cleaned.split("/")
    if len(parts) > MAX_DEPTH:
        return None
    for part in parts:
        if part in {"", ".", ".."} or not _PART.fullmatch(part) or part.split(".")[0].strip().lower() in _RESERVED:
            return None
    name = parts[-1].lower()
    if name == "desktop.ini" or name.rsplit(".", 1)[-1] in _ACTING:
        return None
    return "/".join(part.strip() for part in parts)


def _files(reply: str) -> list | None:
    """The "files" list in a reply. Read with a JSON decoder from each "{", so a README in the plan
    holding its own ``` fence, or prose after the object, no longer loses the plan."""
    text = reply or ""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            data, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(data, dict) and "files" in data:
            return data["files"] if isinstance(data["files"], list) else None
    return None


def file_plan(reply: str) -> list[tuple[str, str]] | None:
    """The files a model's JSON reply asks for, checked; None when it is not a usable plan. One unsafe
    path refuses the whole plan: a model that writes "../" is not to be trusted with the rest."""
    files = _files(reply)
    if not files or len(files) > MAX_FILES:
        record_failure("workspace/plan", ValueError("no usable files list"), files=len(files or []))
        return None
    plan: list[tuple[str, str]] = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
            return None
        relative = safe_relative(entry.get("path"))
        if relative is None or len(entry["content"]) > MAX_FILE_CHARS:
            record_failure("workspace/plan", ValueError("a path or a file refused"), path=str(entry.get("path"))[:80])
            return None
        if relative.lower() in {seen.lower() for seen, _ in plan}:
            return None
        plan.append((relative, entry["content"]))
    if sum(len(content) for _, content in plan) > MAX_TOTAL_CHARS:
        return None
    # A path that is a file and also a folder above another ("src" and "src/app.py") fails half way
    # through writing, after the card was approved. Checked on the whole plan, so either order is
    # caught, and without case, as Windows compares names (Codex's review).
    names = {path.lower() for path, _ in plan}
    folders = {"/".join(path.lower().split("/")[:depth]) for path, _ in plan for depth in range(1, path.count("/") + 1)}
    if names & folders:
        return None
    return plan


def _claim(place: Path, name: str) -> Path:
    """The folder, created now: "<name>", else "<name>-2", "-3"... Claimed by creating it, so a folder
    that appeared while the card was open is never written into (the old check-then-create raced)."""
    for n in range(1, 100):
        folder = place / (name if n == 1 else f"{name}-{n}")
        try:
            folder.mkdir(exist_ok=False)
            return folder
        except FileExistsError:
            continue
    raise FileExistsError(f"Too many folders named {name} in {place}.")


def _preview(folder: str, plan: list[tuple[str, str]]) -> str:
    """The folder and every file with the start of its contents: names alone hid what was written."""
    lines = [f"Folder: {folder}"]
    budget = PREVIEW_CHARS
    for path, content in plan:
        lines.append(f"- {path} ({len(content):,} characters)")
        shown = content[:max(0, min(300, budget))]
        budget -= len(shown)
        if shown:
            lines.append("    " + shown.replace("\n", "\n    ") + ("…" if len(shown) < len(content) else ""))
    if not plan:
        lines.append("(empty)")
    return "\n".join(lines)


class WorkspaceTool:
    def __init__(self, writer: Writer | None, approval_gate: ApprovalGate, home: Path | None = None) -> None:
        self._writer = writer
        self._gate = approval_gate
        self._home = home or Path.home()

    def create(self, request: str) -> ToolResult:
        request = (request or "").strip()
        if not request:
            return ToolResult.failure("What folder should I make? Try \"create a folder called notes on my desktop\".")
        name, place = folder_and_place(request, self._home)
        standard = place in {self._home / sub for sub in _HOME_PLACES.values() if sub} and self._home.is_dir()
        if not place.is_dir() and not standard:
            # Never made: "in C:\My Projects" without quotes read as C:\My, which was created.
            return ToolResult.failure(f"There's no folder {place}. Put a path with spaces in quotes, "
                                      "or make the folder first.")
        plan: list[tuple[str, str]] = []
        if _CONTENTS.search(request):
            if self._writer is None:
                return ToolResult.failure("Filling a folder needs a language model, and none is configured.")
            try:
                reply = self._writer(_PROMPT.format(request=request, max_files=MAX_FILES, max_file=MAX_FILE_CHARS,
                                                    max_total=MAX_TOTAL_CHARS, max_depth=MAX_DEPTH))
            except Exception as exc:  # a model outage must not raise out of a tool
                record_failure("workspace/writer", exc)
                return ToolResult.failure(f"The model could not plan those files: {exc}")
            if not str(reply or "").strip():
                return ToolResult.failure("No language model answered just now, so nothing was written. Try again "
                                          "in a moment.")
            if isinstance(reply, CutOff):
                return ToolResult.failure("The model's plan ran past its length limit, so nothing was written. "
                                          "Ask for fewer or shorter files.")
            planned = file_plan(reply)
            if planned is None:
                return ToolResult.failure("The model did not return a usable list of files (a name it may not use, "
                                          "a file type I don't write, or a file too long), so nothing was written. "
                                          "Try again, or say what the files should be.")
            plan = planned
        self._gate.require(
            ApprovalRequest(
                action=f"Create folder {place / name}" + (f" with {len(plan)} file(s)" if plan else ""),
                risk=RiskLevel.HIGH,
                reason="This creates a folder and writes files to disk.",
                preview=_preview(f"{place / name} (or {name}-2, -3... if that name is taken)", plan),
            )
        )
        try:
            if standard:
                place.mkdir(exist_ok=True)  # Desktop or Documents under an existing home, never its parents
            folder = _claim(place, name)
        except OSError as exc:
            record_failure("workspace/claim", exc)
            return ToolResult.failure(f"I couldn't make the folder in {place}: {exc}")
        try:
            for relative, content in plan:
                target = folder / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            # Never half a folder: everything in it was written by this call, so it all goes.
            record_failure("workspace/write", exc, folder=str(folder))
            shutil.rmtree(folder, ignore_errors=True)
            if folder.exists():
                return ToolResult.failure(f"Writing the files failed ({type(exc).__name__}), and I couldn't remove "
                                          f"the folder again: what was written is still at {folder}.")
            return ToolResult.failure(f"Writing the files failed ({type(exc).__name__}), so I removed the folder "
                                      "again; nothing was left half-written.")
        files = "\n".join(f"- `{path}`" for path, _ in plan)
        return ToolResult.success(
            f"Created **{folder.name}** at {folder}" + (f" with {len(plan)} file(s):\n{files}" if plan else ", empty."),
            folder=str(folder),
            files=[path for path, _ in plan],
        )
