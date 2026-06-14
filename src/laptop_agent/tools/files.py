from __future__ import annotations

import csv
import mimetypes
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
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

CONVERT_TARGET_EXTENSIONS = {".txt", ".md", ".markdown"}

CATEGORY_BY_EXTENSION = {
    "documents": {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".tex", ".rtf", ".odt"},
    "spreadsheets": {".csv", ".tsv", ".xlsx", ".xls", ".ods"},
    "images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".tiff", ".heic"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "archives": {".zip", ".tar", ".gz", ".tgz", ".7z", ".rar"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".json", ".yaml", ".yml", ".sh", ".ps1", ".java", ".c", ".cpp"},
}

STOPWORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had", "her", "was",
    "one", "our", "out", "day", "get", "has", "him", "his", "how", "man", "new", "now", "old",
    "see", "two", "way", "who", "boy", "did", "its", "let", "put", "say", "she", "too", "use",
    "that", "this", "with", "have", "from", "they", "will", "would", "there", "their", "what",
    "about", "which", "when", "make", "like", "time", "just", "into", "than", "them", "then",
    "your", "some", "could", "other", "been", "were", "also", "more", "very", "such", "only",
    "over", "most", "after", "where", "these", "those", "being", "while", "should", "shall",
}


@dataclass(frozen=True)
class FileSummary:
    path: str
    size_bytes: int
    mime_type: str


class FileTool:
    def __init__(self, approval_gate: ApprovalGate | None = None) -> None:
        self.approval_gate = approval_gate or ApprovalGate()

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
        text, error, meta = self._load_text(target)
        if error is not None:
            return error
        return ToolResult.success(
            f"Read {meta.get('kind', 'text')}: {target}",
            text=text[:max_chars],
            truncated=len(text) > max_chars,
            **{key: value for key, value in meta.items() if key != "kind"},
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

    def extract_document_text(self, path: str) -> ToolResult:
        target = Path(path).expanduser().resolve()
        text, error, meta = self._load_text(target)
        if error is not None:
            return error
        return ToolResult.success(
            f"Extracted text from {target.name}.",
            path=str(target),
            text=text,
            char_count=len(text),
            **{key: value for key, value in meta.items() if key != "kind"},
        )

    def summarize(self, path: str, sentences: int = 5) -> ToolResult:
        target = Path(path).expanduser().resolve()
        text, error, _meta = self._load_text(target)
        if error is not None:
            return error
        return self.summarize_text(text, source=str(target), sentences=sentences)

    def summarize_text(self, text: str, source: str | None = None, sentences: int = 5) -> ToolResult:
        sentence_list = self._split_sentences(text)
        if not sentence_list:
            return ToolResult.failure(
                f"No readable prose to summarize{f' in: {source}' if source else '.'}",
                source=source,
            )

        wanted = max(1, min(sentences, 15))
        selected = self._rank_sentences(sentence_list, wanted)
        summary = " ".join(sentence_list[index] for index in selected)
        words = self._content_words(text)
        keywords = [word for word, _ in Counter(words).most_common(8)]
        label = source or "text"
        return ToolResult.success(
            f"Summarized {label} into {len(selected)} sentence(s).",
            path=source,
            summary=summary,
            sentence_count=len(sentence_list),
            summary_sentences=len(selected),
            word_count=len(re.findall(r"\S+", text)),
            keywords=keywords,
        )

    def file_info(self, path: str) -> ToolResult:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"File does not exist: {target}")
        stat = target.stat()
        mime_type, _ = mimetypes.guess_type(target.name)
        info: dict[str, object] = {
            "path": str(target),
            "name": target.name,
            "suffix": target.suffix.lower(),
            "size_bytes": stat.st_size,
            "mime_type": mime_type or "application/octet-stream",
            "category": self._category_for(target.suffix.lower()),
        }
        if target.suffix.lower() in TEXT_EXTENSIONS:
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
                info["line_count"] = content.count("\n") + 1 if content else 0
                info["word_count"] = len(re.findall(r"\S+", content))
                info["char_count"] = len(content)
            except OSError as exc:
                info["read_error"] = str(exc)
        return ToolResult.success(f"File info for {target.name}.", **info)

    def extract_tables(self, path: str, max_rows: int = 100) -> ToolResult:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"File does not exist: {target}")
        suffix = target.suffix.lower()
        if suffix in {".csv", ".tsv"}:
            tables = self._extract_delimited_tables(target, "\t" if suffix == ".tsv" else ",", max_rows)
        elif suffix in {".md", ".markdown"}:
            tables = self._extract_markdown_tables(target, max_rows)
        else:
            return ToolResult.failure(
                f"Table extraction supports .csv, .tsv, and .md files, not {suffix or 'unknown'}.",
            )
        total_rows = sum(len(table["rows"]) for table in tables)
        return ToolResult.success(
            f"Extracted {len(tables)} table(s) with {total_rows} row(s).",
            path=str(target),
            tables=tables,
        )

    def convert(self, source: str, destination: str) -> ToolResult:
        src = Path(source).expanduser().resolve()
        dst = Path(destination).expanduser().resolve()
        text, error, _meta = self._load_text(src)
        if error is not None:
            return error
        if dst.suffix.lower() not in CONVERT_TARGET_EXTENSIONS:
            return ToolResult.failure(
                f"Unsupported conversion target: {dst.suffix or 'unknown'}",
                supported=sorted(CONVERT_TARGET_EXTENSIONS),
            )
        if dst == src:
            return ToolResult.failure("Source and destination are the same file.")

        overwrite = dst.exists()
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Write converted file: {dst}",
                risk=RiskLevel.HIGH,
                reason="Conversion writes a new file to disk and can overwrite an existing file.",
                preview=(
                    f"Source: {src}\nDestination: {dst}\n"
                    f"Overwrite existing: {'yes' if overwrite else 'no'}\nCharacters: {len(text)}"
                ),
            )
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        return ToolResult.success(
            f"Converted {src.name} to {dst}.",
            source=str(src),
            destination=str(dst),
            overwrote=overwrite,
            char_count=len(text),
        )

    def organize(self, root: str, apply: bool = False) -> ToolResult:
        base = Path(root).expanduser().resolve()
        if not base.exists() or not base.is_dir():
            return ToolResult.failure(f"Folder does not exist: {base}")

        planned: list[dict[str, object]] = []
        for path in sorted(base.iterdir()):
            if not path.is_file():
                continue
            category = self._category_for(path.suffix.lower())
            destination = base / category / path.name
            planned.append(
                {
                    "from": str(path),
                    "to": str(destination),
                    "category": category,
                    "collision": destination.exists(),
                }
            )

        counts: dict[str, int] = {}
        for item in planned:
            counts[str(item["category"])] = counts.get(str(item["category"]), 0) + 1

        if not apply:
            return ToolResult.success(
                f"Planned organization for {len(planned)} file(s). Nothing was moved.",
                root=str(base),
                planned=planned,
                category_counts=counts,
                next_steps=["Review the plan", "Run 'organize folder <path> apply' to move files after approval"],
            )

        if not planned:
            return ToolResult.success("No files to organize.", root=str(base), moved=[], skipped=[])

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Organize folder by moving {len(planned)} file(s): {base}",
                risk=RiskLevel.HIGH,
                reason="Organizing moves files into category subfolders. Moves change where your files live.",
                preview="\n".join(f"{category}: {count} file(s)" for category, count in sorted(counts.items())),
            )
        )

        moved: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        for item in planned:
            source_path = Path(str(item["from"]))
            destination_path = Path(str(item["to"]))
            if destination_path.exists():
                skipped.append({**item, "reason": "destination already exists"})
                continue
            try:
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source_path), str(destination_path))
                moved.append(item)
            except OSError as exc:
                skipped.append({**item, "reason": str(exc)})
        return ToolResult.success(
            f"Organized folder: moved {len(moved)} file(s), skipped {len(skipped)}.",
            root=str(base),
            moved=moved,
            skipped=skipped,
            category_counts=counts,
        )

    def _load_text(self, target: Path) -> tuple[str, ToolResult | None, dict[str, object]]:
        if not target.exists() or not target.is_file():
            return "", ToolResult.failure(f"File does not exist: {target}"), {}

        suffix = target.suffix.lower()
        if suffix in TEXT_EXTENSIONS:
            text = target.read_text(encoding="utf-8", errors="replace")
            return text, None, {"kind": "text file"}
        if suffix == ".pdf":
            return self._load_pdf(target)
        if suffix == ".docx":
            return self._load_docx(target)
        return (
            "",
            ToolResult.failure(
                f"Unsupported text type: {suffix or 'unknown'}",
                hint="Install docs extras for PDF/DOCX, or use scan to inspect metadata.",
            ),
            {},
        )

    @staticmethod
    def _load_pdf(target: Path) -> tuple[str, ToolResult | None, dict[str, object]]:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return "", ToolResult.failure("PDF support requires: pip install pypdf"), {}
        reader = PdfReader(str(target))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text, None, {"kind": "PDF", "pages": len(reader.pages)}

    @staticmethod
    def _load_docx(target: Path) -> tuple[str, ToolResult | None, dict[str, object]]:
        try:
            from docx import Document  # type: ignore
        except ImportError:
            return "", ToolResult.failure("DOCX support requires: pip install python-docx"), {}
        document = Document(str(target))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        return text, None, {"kind": "DOCX"}

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", text).strip()
        if not cleaned:
            return []
        parts = re.split(r"(?<=[.!?])\s+", cleaned)
        return [part.strip() for part in parts if len(part.strip()) > 1]

    @staticmethod
    def _content_words(text: str) -> list[str]:
        words = re.findall(r"[A-Za-z']+", text.lower())
        return [word for word in words if len(word) > 2 and word not in STOPWORDS]

    @classmethod
    def _rank_sentences(cls, sentences: list[str], wanted: int, min_words: int = 4) -> list[int]:
        if len(sentences) <= wanted:
            return list(range(len(sentences)))
        frequencies = Counter(cls._content_words(" ".join(sentences)))

        def words_of(sentence: str) -> list[str]:
            return [word for word in re.findall(r"[A-Za-z']+", sentence.lower()) if len(word) > 2 and word not in STOPWORDS]

        # Prefer substantial sentences so fragments (common in scraped web text)
        # like "loop." cannot dominate purely by repeating a frequent word.
        candidates = [index for index, sentence in enumerate(sentences) if len(words_of(sentence)) >= min_words]
        pool = candidates if len(candidates) >= wanted else list(range(len(sentences)))

        scored: list[tuple[float, int]] = []
        for index in pool:
            words = words_of(sentences[index])
            # Dampen by sqrt(length) to reward informative sentences without
            # always picking the longest one.
            score = sum(frequencies[word] for word in words) / (len(words) ** 0.5) if words else 0.0
            scored.append((score, index))
        top = sorted(scored, key=lambda item: (-item[0], item[1]))[:wanted]
        return sorted(index for _, index in top)

    @staticmethod
    def _category_for(suffix: str) -> str:
        for category, extensions in CATEGORY_BY_EXTENSION.items():
            if suffix in extensions:
                return category
        return "other"

    @staticmethod
    def _extract_delimited_tables(target: Path, delimiter: str, max_rows: int) -> list[dict[str, object]]:
        try:
            with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                reader = csv.reader(handle, delimiter=delimiter)
                rows = [row for _, row in zip(range(max_rows + 1), reader)]
        except OSError:
            return []
        if not rows:
            return []
        header = rows[0]
        body = rows[1 : max_rows + 1]
        return [{"header": header, "rows": body, "row_count": len(body), "column_count": len(header)}]

    @staticmethod
    def _extract_markdown_tables(target: Path, max_rows: int) -> list[dict[str, object]]:
        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

        def cells(line: str) -> list[str]:
            return [cell.strip() for cell in line.strip().strip("|").split("|")]

        tables: list[dict[str, object]] = []
        index = 0
        while index < len(lines) - 1:
            line = lines[index].strip()
            separator = lines[index + 1].strip()
            is_table = "|" in line and bool(re.match(r"^\|?\s*:?-{1,}.*\|", separator)) and set(separator) <= set("|-: ")
            if is_table:
                header = cells(line)
                body: list[list[str]] = []
                cursor = index + 2
                while cursor < len(lines) and "|" in lines[cursor] and len(body) < max_rows:
                    body.append(cells(lines[cursor]))
                    cursor += 1
                tables.append({"header": header, "rows": body, "row_count": len(body), "column_count": len(header)})
                index = cursor
            else:
                index += 1
        return tables

    @staticmethod
    def _summarize(path: Path) -> FileSummary:
        mime_type, _ = mimetypes.guess_type(path.name)
        return FileSummary(path=str(path), size_bytes=path.stat().st_size, mime_type=mime_type or "application/octet-stream")
