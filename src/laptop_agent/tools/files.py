from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path

from laptop_agent.tools.base import ToolResult


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".tex",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".html",
    ".css",
}


@dataclass(frozen=True)
class FileSummary:
    path: str
    size_bytes: int
    mime_type: str


class FileTool:
    def scan(self, root: str, limit: int = 200) -> ToolResult:
        base = Path(root).expanduser().resolve()
        if not base.exists():
            return ToolResult.failure(f"Path does not exist: {base}")
        if base.is_file():
            return ToolResult.success("Scanned one file.", files=[self._summarize(base).__dict__])

        files: list[dict[str, object]] = []
        for path in base.rglob("*"):
            if path.is_file():
                files.append(self._summarize(path).__dict__)
                if len(files) >= limit:
                    break
        return ToolResult.success(f"Scanned {len(files)} files.", files=files, root=str(base))

    def read_text(self, path: str, max_chars: int = 12000) -> ToolResult:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"File does not exist: {target}")

        suffix = target.suffix.lower()
        if suffix in TEXT_EXTENSIONS:
            text = target.read_text(encoding="utf-8", errors="replace")
            return ToolResult.success(f"Read text file: {target}", text=text[:max_chars], truncated=len(text) > max_chars)

        if suffix == ".pdf":
            return self._read_pdf(target, max_chars)
        if suffix == ".docx":
            return self._read_docx(target, max_chars)

        return ToolResult.failure(
            f"Unsupported direct read type: {suffix or 'unknown'}",
            hint="Install docs extras for PDF/DOCX, or use scan to inspect metadata.",
        )

    def search_text(self, query: str, root: str, limit: int = 50) -> ToolResult:
        base = Path(root).expanduser().resolve()
        if not base.exists():
            return ToolResult.failure(f"Path does not exist: {base}")

        matches: list[dict[str, object]] = []
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        lowered = query.lower()
        for path in files:
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, start=1):
                if lowered in line.lower():
                    matches.append({"path": str(path), "line": number, "text": line.strip()[:300]})
                    if len(matches) >= limit:
                        return ToolResult.success(f"Found {len(matches)} matches.", matches=matches)
        return ToolResult.success(f"Found {len(matches)} matches.", matches=matches)

    @staticmethod
    def _summarize(path: Path) -> FileSummary:
        mime_type, _ = mimetypes.guess_type(path.name)
        return FileSummary(path=str(path), size_bytes=path.stat().st_size, mime_type=mime_type or "application/octet-stream")

    @staticmethod
    def _read_pdf(path: Path, max_chars: int) -> ToolResult:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return ToolResult.failure("PDF support requires: pip install pypdf")
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return ToolResult.success(f"Read PDF: {path}", text=text[:max_chars], pages=len(reader.pages), truncated=len(text) > max_chars)

    @staticmethod
    def _read_docx(path: Path, max_chars: int) -> ToolResult:
        try:
            from docx import Document  # type: ignore
        except ImportError:
            return ToolResult.failure("DOCX support requires: pip install python-docx")
        document = Document(str(path))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return ToolResult.success(f"Read DOCX: {path}", text=text[:max_chars], truncated=len(text) > max_chars)
