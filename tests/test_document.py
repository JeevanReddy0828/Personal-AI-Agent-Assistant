from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.document import DocumentTool, markdown_to_html, split_format

SAMPLE = """# Rate Limit Brief

## Summary
The gateway allows **40** requests per minute.

- Bursts are smoothed over 10s
- A 429 carries `Retry-After`

| Tier | Limit |
|---|---|
| Free | 40/min |
| Paid | 200/min |
"""


class FormatParsingTests(unittest.TestCase):
    def test_a_trailing_format_is_pulled_off_the_request(self) -> None:
        self.assertEqual(split_format("a brief on rate limits as a pdf"), ("a brief on rate limits", "pdf"))
        self.assertEqual(split_format("a brief as a word doc"), ("a brief", "docx"))
        self.assertEqual(split_format("a brief as markdown"), ("a brief", "md"))
        self.assertEqual(split_format("a brief in docx format"), ("a brief", "docx"))

    def test_no_format_keeps_the_whole_request_and_the_default(self) -> None:
        self.assertEqual(split_format("a brief on rate limits"), ("a brief on rate limits", "pdf"))

    def test_a_format_word_inside_the_topic_is_not_a_format(self) -> None:
        # "how pdf compression works" is the subject, not the output format.
        self.assertEqual(split_format("how pdf compression works"), ("how pdf compression works", "pdf"))


class MarkdownToHtmlTests(unittest.TestCase):
    def test_structure_survives(self) -> None:
        html = markdown_to_html(SAMPLE, "Rate Limit Brief")
        self.assertIn("<h1>Rate Limit Brief</h1>", html)
        self.assertIn("<h2>Summary</h2>", html)
        self.assertIn("<strong>40</strong>", html)
        self.assertIn("<li>Bursts are smoothed over 10s</li>", html)
        self.assertIn("<code>Retry-After</code>", html)
        self.assertIn("<th>Tier</th>", html)
        self.assertIn("<td>Free</td>", html)

    def test_model_output_cannot_inject_markup(self) -> None:
        html = markdown_to_html("# <script>alert(1)</script>\n\nBody <img src=x onerror=1>")
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_fenced_block_is_kept_verbatim(self) -> None:
        html = markdown_to_html("Run:\n```\nnpm install\n```\nDone.")
        self.assertIn("<pre>npm install</pre>", html)


class DocumentToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def tool(self, writer=lambda prompt: SAMPLE, **kwargs) -> DocumentTool:
        return DocumentTool(data_dir=self.data_dir, writer=writer, **kwargs)

    def test_markdown_is_written_and_linked(self) -> None:
        result = self.tool().create("a brief on rate limits as markdown")
        self.assertTrue(result.ok, result.message)
        path = Path(result.data["document"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.suffix, ".md")
        self.assertEqual(path.read_text(encoding="utf-8"), SAMPLE.strip())
        self.assertEqual(result.data["title"], "Rate Limit Brief")
        self.assertIn(f"]({result.data['url']})", result.message)

    def test_the_request_reaches_the_model_without_the_format_words(self) -> None:
        seen: list[str] = []

        def writer(prompt: str) -> str:
            seen.append(prompt)
            return SAMPLE

        self.tool(writer=writer).create("a brief on rate limits as markdown")
        self.assertIn("a brief on rate limits", seen[0])
        self.assertNotIn("as markdown", seen[0])

    def test_an_empty_request_asks_for_one(self) -> None:
        result = self.tool().create("   as a pdf")
        self.assertFalse(result.ok)
        self.assertIn("What should the document say", result.message)

    def test_an_unsupported_format_is_refused_clearly(self) -> None:
        result = self.tool().create("a brief", fmt="xlsx")
        self.assertFalse(result.ok)
        self.assertIn("pdf, docx or md", result.message)

    def test_without_a_model_it_says_so(self) -> None:
        result = DocumentTool(data_dir=self.data_dir).create("a brief as markdown")
        self.assertFalse(result.ok)
        self.assertIn("language model", result.message)

    def test_an_empty_model_reply_writes_no_file(self) -> None:
        result = self.tool(writer=lambda prompt: "   ").create("a brief as markdown")
        self.assertFalse(result.ok)
        self.assertIn("empty document", result.message)
        self.assertFalse((self.data_dir / "documents").exists())

    def test_a_model_failure_is_reported_not_raised(self) -> None:
        def broken(prompt: str) -> str:
            raise RuntimeError("model offline")

        result = self.tool(writer=broken).create("a brief as markdown")
        self.assertFalse(result.ok)
        self.assertIn("model offline", result.message)

    def test_denied_approval_stops_the_call(self) -> None:
        calls: list[str] = []

        def writer(prompt: str) -> str:
            calls.append(prompt)
            return SAMPLE

        tool = self.tool(writer=writer, approval_gate=ApprovalGate(ask=lambda request: False))
        with self.assertRaises(ApprovalDenied):
            tool.create("a brief as markdown")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
