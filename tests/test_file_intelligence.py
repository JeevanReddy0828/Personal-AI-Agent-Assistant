from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.files import FileTool


def auto_approve_tool() -> FileTool:
    return FileTool(ApprovalGate(lambda request: True))


def deny_tool() -> FileTool:
    return FileTool(ApprovalGate(lambda request: False))


SAMPLE_PROSE = (
    "The laptop agent reads local files safely. The agent can summarize documents without any cloud service. "
    "Summarization runs fully offline using simple word frequency scoring. "
    "An approval gate protects every action that writes or moves files. "
    "Approval gates keep the agent safe and predictable for the user."
)


class SummarizeTests(unittest.TestCase):
    def test_summarizes_text_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "doc.txt"
            path.write_text(SAMPLE_PROSE, encoding="utf-8")
            result = auto_approve_tool().summarize(str(path), sentences=2)
            self.assertTrue(result.ok)
            self.assertEqual(result.data["summary_sentences"], 2)
            self.assertTrue(result.data["summary"])
            self.assertIn("agent", result.data["keywords"])

    def test_short_text_returns_all_sentences(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "short.txt"
            path.write_text("One sentence only.", encoding="utf-8")
            result = auto_approve_tool().summarize(str(path), sentences=5)
            self.assertTrue(result.ok)
            self.assertEqual(result.data["summary_sentences"], 1)

    def test_missing_file_fails(self) -> None:
        result = auto_approve_tool().summarize("does-not-exist.txt")
        self.assertFalse(result.ok)


class FileInfoTests(unittest.TestCase):
    def test_reports_text_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "note.md"
            path.write_text("alpha beta\ngamma\n", encoding="utf-8")
            result = auto_approve_tool().file_info(str(path))
            self.assertTrue(result.ok)
            self.assertEqual(result.data["word_count"], 3)
            self.assertEqual(result.data["category"], "documents")


class ExtractTablesTests(unittest.TestCase):
    def test_extracts_csv(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "data.csv"
            path.write_text("name,role\nAda,Engineer\nGrace,Admiral\n", encoding="utf-8")
            result = auto_approve_tool().extract_tables(str(path))
            self.assertTrue(result.ok)
            table = result.data["tables"][0]
            self.assertEqual(table["header"], ["name", "role"])
            self.assertEqual(table["row_count"], 2)

    def test_extracts_markdown_table(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "tables.md"
            path.write_text(
                "Intro text\n\n| Name | Role |\n| --- | --- |\n| Ada | Engineer |\n| Grace | Admiral |\n",
                encoding="utf-8",
            )
            result = auto_approve_tool().extract_tables(str(path))
            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["tables"]), 1)
            self.assertEqual(result.data["tables"][0]["header"], ["Name", "Role"])
            self.assertEqual(result.data["tables"][0]["row_count"], 2)

    def test_unsupported_type_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "image.png"
            path.write_bytes(b"\x89PNG")
            result = auto_approve_tool().extract_tables(str(path))
            self.assertFalse(result.ok)


class ConvertTests(unittest.TestCase):
    def test_converts_with_approval(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            src = Path(raw) / "in.md"
            dst = Path(raw) / "out.txt"
            src.write_text("# Title\n\nBody text.", encoding="utf-8")
            result = auto_approve_tool().convert(str(src), str(dst))
            self.assertTrue(result.ok)
            self.assertTrue(dst.exists())
            self.assertEqual(dst.read_text(encoding="utf-8"), "# Title\n\nBody text.")

    def test_denied_conversion_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            src = Path(raw) / "in.txt"
            dst = Path(raw) / "out.txt"
            src.write_text("data", encoding="utf-8")
            with self.assertRaises(Exception):
                deny_tool().convert(str(src), str(dst))
            self.assertFalse(dst.exists())

    def test_unsupported_target_fails(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            src = Path(raw) / "in.txt"
            dst = Path(raw) / "out.pdf"
            src.write_text("data", encoding="utf-8")
            result = auto_approve_tool().convert(str(src), str(dst))
            self.assertFalse(result.ok)


class OrganizeTests(unittest.TestCase):
    def _seed(self, root: Path) -> None:
        (root / "report.pdf").write_text("x", encoding="utf-8")
        (root / "song.mp3").write_text("x", encoding="utf-8")
        (root / "script.py").write_text("x", encoding="utf-8")

    def test_preview_does_not_move(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._seed(root)
            result = auto_approve_tool().organize(str(root), apply=False)
            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["planned"]), 3)
            self.assertTrue((root / "report.pdf").exists())
            self.assertFalse((root / "documents").exists())

    def test_apply_moves_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._seed(root)
            result = auto_approve_tool().organize(str(root), apply=True)
            self.assertTrue(result.ok)
            self.assertEqual(len(result.data["moved"]), 3)
            self.assertTrue((root / "documents" / "report.pdf").exists())
            self.assertTrue((root / "audio" / "song.mp3").exists())
            self.assertTrue((root / "code" / "script.py").exists())
            self.assertFalse((root / "report.pdf").exists())


if __name__ == "__main__":
    unittest.main()
