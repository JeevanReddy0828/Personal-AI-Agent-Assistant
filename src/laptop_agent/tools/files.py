from __future__ import annotations

import csv
import mimetypes
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.failures import record_failure
from laptop_agent.terms import collapse_acronyms
from laptop_agent.tools.base import ToolResult

NL = chr(10)


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


# Counting a tree is cheap; counting an unbounded one is not. Past this many entries the
# scan reports that it stopped rather than pretending the number is the total.
_SCAN_WALK_CEILING = 50_000




def _readable_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GB"


def _describe_matches(matches: list[dict[str, object]], query: str, base: Path, capped: bool = False) -> str:
    """Show what was found. "Found 50 matches." left the matches in `data`, where the
    chat page never renders them - the user saw a number and nothing else."""
    if not matches:
        return f"No matches for {query!r} under {base}."
    shown = matches[:12]
    lines = [f"**{len(matches)}{'+' if capped else ''} match(es) for `{query}`**", ""]
    for entry in shown:
        try:
            where = Path(str(entry["path"])).relative_to(base)
        except (ValueError, TypeError):
            where = Path(str(entry.get("path", ""))).name
        lines.append(f"- `{where}:{entry.get('line')}` — {str(entry.get('text', '')).strip()[:120]}")
    if len(matches) > len(shown):
        lines.append(f"- …and {len(matches) - len(shown)} more")
    return NL.join(lines)


class FileTool:
    def __init__(self, approval_gate: ApprovalGate | None = None) -> None:
        self.approval_gate = approval_gate or ApprovalGate()

    def scan(self, root: str, limit: int = 200) -> ToolResult:
        base = Path(root).expanduser().resolve()
        if not base.exists():
            return ToolResult.failure(f"Path does not exist: {base}")
        if base.is_file():
            return ToolResult.success("Scanned one file.", files=[self._summarize(base).__dict__])

        # The cap used to be invisible: "Scanned 200 files." for a tree of thousands read
        # as the total, to the user and to the autonomous agent alike. Walk the whole tree
        # to count and tally by type, and return only `limit` summaries — so a question
        # like "how many python files are in src" is answerable from a fact rather than by
        # counting the handful of names that survived truncation. The agent answered that
        # one 27, then 6, against a true 65.
        files: list[dict[str, object]] = []
        by_extension: dict[str, int] = {}
        total = 0
        walked_all = True
        for path in base.rglob("*"):
            if total >= _SCAN_WALK_CEILING:
                walked_all = False
                break
            try:
                if not path.is_file():
                    continue
            except OSError as exc:  # a vanished or unreadable entry must not end the scan
                record_failure("files/scan", exc, path=str(path)[:120])
                continue
            total += 1
            suffix = path.suffix.lower() or "(no extension)"
            by_extension[suffix] = by_extension.get(suffix, 0) + 1
            if len(files) < limit:
                files.append(self._summarize(path).__dict__)
        shown = len(files)
        if not walked_all:
            message = f"Scanned {shown} files; stopped counting at {total} (the tree is very large)."
        elif shown < total:
            message = f"Scanned {total} files, listing the first {shown}."
        else:
            message = f"Scanned {total} files."
        return ToolResult.success(
            message,
            files=files,
            root=str(base),
            total_files=total,
            listed=shown,
            complete=walked_all and shown == total,
            by_extension=dict(sorted(by_extension.items(), key=lambda item: -item[1])),
        )

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
        files = (base,) if base.is_file() else (p for p in base.rglob("*") if p.is_file())
        lowered = query.lower()
        for path in files:
            if path.suffix.lower() not in TEXT_EXTENSIONS:
                continue
            try:
                handle = path.open("r", encoding="utf-8", errors="replace")
            except OSError:
                continue
            with handle:
                for number, line in enumerate(handle, start=1):
                    if lowered in line.lower():
                        matches.append({"path": str(path), "line": number, "text": line.strip()[:300]})
                        if len(matches) >= limit:
                            return ToolResult.success(
                                _describe_matches(matches, query, base, capped=True), matches=matches
                            )
        return ToolResult.success(_describe_matches(matches, query, base), matches=matches)

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

    def answer_question(self, path: str, question: str, sentences: int = 5) -> ToolResult:
        target = Path(path).expanduser().resolve()
        text, error, _meta = self._load_text(target)
        if error is not None:
            return error
        return self.answer_text(text, question, source=str(target), sentences=sentences)

    def answer_text(self, text: str, question: str, source: str | None = None, sentences: int = 5) -> ToolResult:
        cleaned_question = question.strip()
        if not cleaned_question:
            return ToolResult.failure("Ask a question to answer from the text.")
        sentence_list = self._split_sentences(text)
        if not sentence_list:
            return ToolResult.failure(
                f"No readable prose to answer from{f' in: {source}' if source else '.'}",
                source=source,
            )
        query_terms = set(self._content_words(cleaned_question))
        if not query_terms:
            return self.summarize_text(text, source=source, sentences=sentences)

        ranked: list[tuple[float, int, str]] = []
        for index, sentence in enumerate(sentence_list):
            words = self._content_words(sentence)
            if not words:
                continue
            overlap = sum(1 for word in words if word in query_terms)
            if overlap <= 0:
                continue
            score = overlap / (len(words) ** 0.35)
            ranked.append((score, index, sentence))
        if not ranked:
            return ToolResult.failure(
                f"I could not find text relevant to: {cleaned_question}",
                question=cleaned_question,
                source=source,
            )

        wanted = max(1, min(sentences, 10))
        selected = sorted(sorted(ranked, key=lambda item: (-item[0], item[1]))[:wanted], key=lambda item: item[1])
        excerpts = [
            {
                "sentence": sentence,
                "score": round(score, 4),
                "position": index,
            }
            for score, index, sentence in selected
        ]
        answer = " ".join(str(item["sentence"]) for item in excerpts)
        return ToolResult.success(
            f"Answered from {source or 'text'} using {len(excerpts)} excerpt(s).",
            question=cleaned_question,
            source=source,
            answer=answer,
            excerpts=excerpts,
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
        described = [
            f"**{target.name}**",
            f"- {_readable_size(stat.st_size)} · {info['mime_type']} · {info['category']}",
            f"- `{target}`",
        ]
        if "line_count" in info:
            described.append(
                f"- {info['line_count']:,} lines · {info['word_count']:,} words · "
                f"{info['char_count']:,} characters"
            )
        if info.get("read_error"):
            described.append(f"- could not read it: {info['read_error']}")
        return ToolResult.success(NL.join(described), **info)

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

    def analyze_spreadsheet(self, path: str, max_rows: int = 10000) -> ToolResult:
        """Compute per-column statistics for a CSV/TSV file (stdlib only).

        Numeric columns get count/min/max/mean/sum; everything else gets a value
        count and a few sample values. Read-only and local, so no approval gate.
        """
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"File does not exist: {target}")
        suffix = target.suffix.lower()
        if suffix not in {".csv", ".tsv"}:
            return ToolResult.failure(
                f"Spreadsheet analysis supports .csv and .tsv files, not {suffix or 'unknown'}.",
                hint="Convert .xlsx to .csv first, or use 'file info' for metadata.",
            )

        delimiter = "\t" if suffix == ".tsv" else ","
        try:
            with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                reader = csv.reader(handle, delimiter=delimiter)
                rows = [row for _, row in zip(range(max_rows + 1), reader)]
        except OSError as exc:
            return ToolResult.failure(f"Could not read spreadsheet: {exc}")
        if not rows:
            return ToolResult.failure(f"Spreadsheet is empty: {target.name}")

        header = rows[0]
        body = rows[1:]
        truncated = len(body) >= max_rows
        columns = [self._column_stats(header[index] or f"column_{index + 1}", index, body) for index in range(len(header))]
        return ToolResult.success(
            f"Analyzed {len(body)} row(s) across {len(header)} column(s) in {target.name}.",
            path=str(target),
            row_count=len(body),
            column_count=len(header),
            truncated=truncated,
            columns=columns,
        )

    @staticmethod
    def _column_stats(name: str, index: int, body: list[list[str]]) -> dict[str, object]:
        values = [row[index].strip() for row in body if index < len(row)]
        non_empty = [value for value in values if value]
        numbers: list[float] = []
        for value in non_empty:
            try:
                numbers.append(float(value.replace(",", "")))
            except ValueError:
                continue
        is_numeric = bool(non_empty) and len(numbers) == len(non_empty)
        stats: dict[str, object] = {
            "name": name,
            "type": "number" if is_numeric else ("empty" if not non_empty else "text"),
            "count": len(non_empty),
            "empty": len(values) - len(non_empty),
        }
        if is_numeric and numbers:
            total = sum(numbers)
            stats.update(
                min=round(min(numbers), 4),
                max=round(max(numbers), 4),
                mean=round(total / len(numbers), 4),
                sum=round(total, 4),
            )
        else:
            uniques = list(dict.fromkeys(non_empty))
            stats["unique"] = len(uniques)
            stats["samples"] = uniques[:3]
        return stats

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

    def write_text(self, destination: str, text: str, description: str = "text file") -> ToolResult:
        dst = Path(destination).expanduser().resolve()
        if not dst.name:
            return ToolResult.failure("Destination file path is required.")
        overwrite = dst.exists()
        self.approval_gate.require(
            ApprovalRequest(
                action=f"Write {description}: {dst}",
                risk=RiskLevel.HIGH,
                reason="This writes a file to disk and can overwrite an existing file.",
                preview=(
                    f"Destination: {dst}\n"
                    f"Overwrite existing: {'yes' if overwrite else 'no'}\n"
                    f"Characters: {len(text)}"
                ),
            )
        )
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8")
        return ToolResult.success(
            f"Wrote {description} to {dst}.",
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
        # Prefer pdfplumber: it preserves fi/fl ligatures and (with a tight x_tolerance)
        # word spacing, which pypdf mangles ("filtering"->"ltering", "JEEVAN"->"JEEV AN").
        # Fall back to pypdf when pdfplumber isn't installed.
        try:
            import pdfplumber  # type: ignore

            with pdfplumber.open(str(target)) as pdf:
                text = "\n".join(page.extract_text(x_tolerance=1) or "" for page in pdf.pages)
                return text, None, {"kind": "PDF", "pages": len(pdf.pages)}
        except ImportError:
            pass
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            return "", ToolResult.failure("PDF support requires: pip install pdfplumber (or pypdf)"), {}
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
        # Its own pattern, not terms.words(): apostrophes belong inside a word here
        # ("can't", "user's"), and version numbers do not belong in a frequency count.
        words = re.findall(r"[A-Za-z']+", collapse_acronyms(text).lower())
        return [word for word in words if len(word) > 2 and word not in STOPWORDS]

    @classmethod
    def _rank_sentences(cls, sentences: list[str], wanted: int, min_words: int = 4) -> list[int]:
        if len(sentences) <= wanted:
            return list(range(len(sentences)))
        frequencies = Counter(cls._content_words(" ".join(sentences)))

        # Prefer substantial sentences so fragments (common in scraped web text)
        # like "loop." cannot dominate purely by repeating a frequent word.
        candidates = [
            index for index, sentence in enumerate(sentences) if len(cls._content_words(sentence)) >= min_words
        ]
        pool = candidates if len(candidates) >= wanted else list(range(len(sentences)))

        scored: list[tuple[float, int]] = []
        for index in pool:
            words = cls._content_words(sentences[index])
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
