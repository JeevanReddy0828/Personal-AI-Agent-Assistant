from __future__ import annotations

from pathlib import Path

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.files import CATEGORY_BY_EXTENSION, FileTool
from laptop_agent.tools.transcribe import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, MEDIA_EXTENSIONS, TranscribeTool


# A "universal" entry point inspired by the Mark XXXIX-OR file processor: hand it
# any path and it picks the most useful default operation for that file type,
# delegating to the existing FileTool / TranscribeTool. It does not add new
# dependencies — it just routes intent so users can say "process file X" instead
# of remembering which of a dozen file commands applies.

# The default operation chosen for each detected category.
_DEFAULT_OPERATION = {
    "spreadsheets": "analyze",
    "documents": "summarize",
    "images": "ocr",
    "audio": "transcribe",
    "video": "transcribe",
    "code": "info",
    "archives": "info",
    "other": "info",
}

# Operations a user can ask for explicitly, and which category they make sense for.
_OPERATIONS_BY_CATEGORY = {
    "spreadsheets": ("analyze", "tables", "info"),
    "documents": ("summarize", "extract", "tables", "info"),
    "images": ("ocr", "info"),
    "audio": ("transcribe", "info"),
    "video": ("transcribe", "info"),
    "code": ("info", "extract"),
    "archives": ("info",),
    "other": ("info",),
}

# Map user-spoken intent words onto canonical operation names.
_INTENT_ALIASES = {
    "summary": "summarize",
    "summarise": "summarize",
    "summarize": "summarize",
    "analyze": "analyze",
    "analyse": "analyze",
    "stats": "analyze",
    "statistics": "analyze",
    "ocr": "ocr",
    "read": "ocr",
    "transcribe": "transcribe",
    "transcript": "transcribe",
    "extract": "extract",
    "text": "extract",
    "table": "tables",
    "tables": "tables",
    "info": "info",
    "metadata": "info",
}


class FileProcessor:
    """Auto-detect a file's type and run the best default operation for it."""

    def __init__(self, files: FileTool, transcribe: TranscribeTool) -> None:
        self._files = files
        self._transcribe = transcribe

    def inspect(self, path: str) -> ToolResult:
        """Report the detected category and available operations without doing work."""
        target = Path(path.strip().strip("'\"")).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"File does not exist: {target}")
        category = self._category_for(target.suffix.lower())
        operations = _OPERATIONS_BY_CATEGORY.get(category, ("info",))
        return ToolResult.success(
            f"{target.name} looks like a {category[:-1] if category.endswith('s') else category} file.",
            path=str(target),
            category=category,
            default_operation=_DEFAULT_OPERATION.get(category, "info"),
            available_operations=list(operations),
        )

    def process(self, path: str, intent: str | None = None) -> ToolResult:
        """Run the best operation for the file (or the one named by ``intent``)."""
        cleaned = path.strip().strip("'\"")
        if not cleaned:
            return ToolResult.failure("Use: process file <path>")
        target = Path(cleaned).expanduser().resolve()
        if not target.exists() or not target.is_file():
            return ToolResult.failure(f"File does not exist: {target}")

        category = self._category_for(target.suffix.lower())
        operation = self._resolve_operation(category, intent)
        result = self._run(operation, str(target), category)
        if result.ok:
            available = _OPERATIONS_BY_CATEGORY.get(category, ("info",))
            result.data.setdefault("category", category)
            result.data["operation"] = operation
            result.data["available_operations"] = list(available)
        return result

    def _resolve_operation(self, category: str, intent: str | None) -> str:
        if intent:
            for word in intent.lower().split():
                canonical = _INTENT_ALIASES.get(word)
                if canonical and canonical in _OPERATIONS_BY_CATEGORY.get(category, ()):
                    return canonical
        return _DEFAULT_OPERATION.get(category, "info")

    def _run(self, operation: str, target: str, category: str) -> ToolResult:
        if operation == "summarize":
            return self._files.summarize(target)
        if operation == "analyze":
            return self._files.analyze_spreadsheet(target)
        if operation == "tables":
            return self._files.extract_tables(target)
        if operation == "extract":
            return self._files.extract_document_text(target)
        if operation == "ocr":
            return self._transcribe.ocr_image(target)
        if operation == "transcribe":
            return self._transcribe.transcribe_media(target)
        return self._files.file_info(target)

    @staticmethod
    def _category_for(suffix: str) -> str:
        # Transcribe's media sets are the authority for audio/video so the
        # dispatcher and the OCR/ASR tools agree on what counts as media.
        if suffix in IMAGE_EXTENSIONS:
            return "images"
        if suffix in AUDIO_EXTENSIONS:
            return "audio"
        if suffix in MEDIA_EXTENSIONS:
            return "video"
        for category, extensions in CATEGORY_BY_EXTENSION.items():
            if suffix in extensions:
                return category
        return "other"
