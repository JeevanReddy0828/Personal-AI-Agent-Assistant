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
from pathlib import Path
from typing import Callable

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

Writer = Callable[[str], str]

MAX_FILES = 12
MAX_FILE_CHARS = 20_000
MAX_TOTAL_CHARS = 120_000

_HOME_PLACES = {"desktop": "Desktop", "documents": "Documents", "downloads": "Downloads", "home": ""}
_NAMED = re.compile(r"\b(?:folder|directory|workspace|project)\s+(?:called|named)\s+[\"']?(?P<name>[\w][\w .-]{0,39}?)[\"']?"
                    r"(?=\s+(?:on|in|at|under|with|and|for|that|containing|full)\b|\s*[.!?]*$)", re.IGNORECASE)
_KIND = re.compile(r"\b(?:a|an|my|the)\s+(?:new\s+)?(?P<name>[a-z][\w-]{0,30}(?:\s+[a-z][\w-]{0,30})?)\s+(?:folder|directory)\b",
                   re.IGNORECASE)
_PLACE = re.compile(r"\b(?:on|in|at|under|inside)\s+(?:my\s+|the\s+)?(?P<home>desktop|documents|downloads|home)"
                    r"(?:\s+folder)?\b", re.IGNORECASE)
_PATH = re.compile(r"\b(?:in|at|under|inside)\s+(?P<path>[A-Za-z]:[\\/][^\s,]*|~[\\/][^\s,]*|/[^\s,]+)", re.IGNORECASE)
# What the folder should hold, when anything: "and fill files with python basics", "with notes on rust".
_CONTENTS = re.compile(r"\b(?:with|and\s+(?:fill|put|add|write|create)|fill(?:ed)?\s+(?:it|with)|containing|full\s+of"
                       r"|for\s+(?:learning|practising|practicing|studying))\b", re.IGNORECASE)
# One plain name per part: no drive, no "..", no leading dot, nothing a shell or Windows treats specially.
_PART = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_. -]{0,62}[A-Za-z0-9_])?")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{n}" for n in range(1, 10)), *(f"lpt{n}" for n in range(1, 10))}

_PROMPT = """You are setting up a folder of files for this request:

{request}

Reply with ONLY a JSON object, no prose and no code fence, in exactly this shape:
{{"files": [{{"path": "relative/path.ext", "content": "the whole file"}}]}}

Rules:
- At most {max_files} files. Paths are relative, use "/" between folders, and never start with "/" or contain "..".
- Choose sensible names and extensions for what was asked (for example 01_variables.py, README.md).
- Every file is complete and correct; code runs as written and is commented for a learner where that helps.
"""


def folder_and_place(request: str, home: Path) -> tuple[str, Path]:
    """The folder's name and the place it goes, read from the words: "a folder called notes on my
    desktop" -> ("notes", ~/Desktop). Defaults: "workspace", in ~/Documents."""
    named = _NAMED.search(request) or _KIND.search(request)
    name = (named.group("name").strip() if named else "") or "workspace"
    if name.lower() in {"new", "project", "folder", "directory", *_HOME_PLACES}:
        name = "workspace"
    name = re.sub(r"[^\w .-]+", "", name).strip(" .") or "workspace"
    path = _PATH.search(request)
    if path:
        return name, Path(path.group("path")).expanduser()
    place = _PLACE.search(request)
    sub = _HOME_PLACES.get(place.group("home").lower(), "Documents") if place else "Documents"
    return name, (home / sub) if sub else home


def safe_relative(path: str) -> str | None:
    """A model-given path as a plain relative path, or None when it is anything else."""
    cleaned = path.strip().replace("\\", "/")
    if not cleaned or cleaned.startswith("/") or re.match(r"^[A-Za-z]:", cleaned):
        return None
    parts = cleaned.split("/")
    if len(parts) > 4:
        return None
    for part in parts:
        if part in {"", ".", ".."} or not _PART.fullmatch(part) or part.split(".")[0].strip().lower() in _RESERVED:
            return None
    return "/".join(part.strip() for part in parts)


def file_plan(reply: str) -> list[tuple[str, str]] | None:
    """The files a model's JSON reply asks for, checked; None when it is not a usable plan. One unsafe
    path refuses the whole plan: a model that writes "../" is not to be trusted with the rest."""
    text = (reply or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        files = json.loads(text[start:end + 1]).get("files")
    except (ValueError, AttributeError):
        return None
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        return None
    plan: list[tuple[str, str]] = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("content"), str):
            return None
        relative = safe_relative(str(entry.get("path", "")))
        if relative is None or len(entry["content"]) > MAX_FILE_CHARS:
            return None
        if relative.lower() in {seen.lower() for seen, _ in plan}:
            return None
        plan.append((relative, entry["content"]))
    if sum(len(content) for _, content in plan) > MAX_TOTAL_CHARS:
        return None
    return plan


def _fresh(folder: Path) -> Path:
    """The folder itself, or "<name>-2", "<name>-3"… when it already exists: never written into."""
    if not folder.exists():
        return folder
    for n in range(2, 100):
        candidate = folder.with_name(f"{folder.name}-{n}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Too many folders named {folder.name}.")


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
        try:
            folder = _fresh(place / name)
        except FileExistsError as exc:
            return ToolResult.failure(str(exc))
        plan: list[tuple[str, str]] = []
        if _CONTENTS.search(request):
            if self._writer is None:
                return ToolResult.failure("Filling a folder needs a language model, and none is configured.")
            try:
                reply = self._writer(_PROMPT.format(request=request, max_files=MAX_FILES))
            except Exception as exc:  # a model outage must not raise out of a tool
                return ToolResult.failure(f"The model could not plan those files: {exc}")
            planned = file_plan(reply)
            if planned is None:
                return ToolResult.failure("The model did not return a usable list of files, so nothing was written. "
                                          "Try again, or say what the files should be.")
            plan = planned
        listing = "\n".join(f"- {path} ({len(content):,} characters)" for path, content in plan)
        self._gate.require(
            ApprovalRequest(
                action=f"Create folder {folder}" + (f" with {len(plan)} file(s)" if plan else ""),
                risk=RiskLevel.HIGH,
                reason="This creates a folder and writes files to disk.",
                preview=f"Folder: {folder}\n" + (listing or "(empty)"),
            )
        )
        folder.mkdir(parents=True, exist_ok=False)
        for relative, content in plan:
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        files = "\n".join(f"- `{path}`" for path, _ in plan)
        return ToolResult.success(
            f"Created **{folder.name}** at {folder}" + (f" with {len(plan)} file(s):\n{files}" if plan else ", empty."),
            folder=str(folder),
            files=[path for path, _ in plan],
        )
