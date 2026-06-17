from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.file_processor import FileProcessor
from laptop_agent.tools.files import FileTool
from laptop_agent.tools.transcribe import TranscribeTool


def make_processor(ocr_text: str = "stub ocr text") -> FileProcessor:
    files = FileTool(ApprovalGate(lambda request: True))
    transcribe = TranscribeTool(ocr_backend=lambda path: ocr_text)
    return FileProcessor(files, transcribe)


SAMPLE_PROSE = (
    "The laptop agent reads local files safely. The agent summarizes documents offline. "
    "An approval gate protects writes. Approval gates keep the agent predictable."
)

SAMPLE_CSV = "name,age,score\nAda,37,9.5\nGrace,45,8.0\nAlan,41,7.5\n"


class SpreadsheetStatsTests(unittest.TestCase):
    def test_numeric_and_text_columns(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "people.csv"
            path.write_text(SAMPLE_CSV, encoding="utf-8")
            result = FileTool(ApprovalGate(lambda r: True)).analyze_spreadsheet(str(path))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["row_count"], 3)
            self.assertEqual(result.data["column_count"], 3)
            columns = {column["name"]: column for column in result.data["columns"]}
            self.assertEqual(columns["name"]["type"], "text")
            self.assertEqual(columns["name"]["unique"], 3)
            self.assertEqual(columns["age"]["type"], "number")
            self.assertEqual(columns["age"]["min"], 37)
            self.assertEqual(columns["age"]["max"], 45)
            self.assertEqual(columns["age"]["mean"], 41)
            self.assertEqual(columns["score"]["sum"], 25.0)

    def test_rejects_non_spreadsheet(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "doc.txt"
            path.write_text("hello", encoding="utf-8")
            result = FileTool(ApprovalGate(lambda r: True)).analyze_spreadsheet(str(path))
            self.assertFalse(result.ok)


class FileProcessorRoutingTests(unittest.TestCase):
    def test_inspect_reports_category(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "data.csv"
            path.write_text(SAMPLE_CSV, encoding="utf-8")
            result = make_processor().inspect(str(path))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["category"], "spreadsheets")
            self.assertEqual(result.data["default_operation"], "analyze")
            self.assertIn("analyze", result.data["available_operations"])

    def test_csv_routes_to_analyze(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "data.csv"
            path.write_text(SAMPLE_CSV, encoding="utf-8")
            result = make_processor().process(str(path))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["operation"], "analyze")
            self.assertEqual(result.data["category"], "spreadsheets")
            self.assertIn("columns", result.data)

    def test_document_routes_to_summarize(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "doc.txt"
            path.write_text(SAMPLE_PROSE, encoding="utf-8")
            result = make_processor().process(str(path))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["operation"], "summarize")
            self.assertTrue(result.data["summary"])

    def test_intent_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "data.csv"
            path.write_text(SAMPLE_CSV, encoding="utf-8")
            result = make_processor().process(str(path), intent="tables")
            self.assertTrue(result.ok)
            self.assertEqual(result.data["operation"], "tables")
            self.assertIn("tables", result.data)

    def test_image_routes_to_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "shot.png"
            path.write_bytes(b"\x89PNG\r\n")  # content irrelevant; OCR backend is stubbed
            result = make_processor(ocr_text="invoice total 42").process(str(path))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["operation"], "ocr")
            self.assertEqual(result.data["category"], "images")
            self.assertIn("42", result.data["text"])

    def test_missing_file(self) -> None:
        result = make_processor().process("does-not-exist.csv")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
