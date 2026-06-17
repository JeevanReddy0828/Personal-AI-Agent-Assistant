from __future__ import annotations

import unittest

from laptop_agent.webui import _compose_command


class ComposeCommandTests(unittest.TestCase):
    def test_no_attachments_returns_command(self) -> None:
        self.assertEqual(_compose_command("hello there", []), "hello there")

    def test_bare_single_upload_routes_to_processor(self) -> None:
        self.assertEqual(_compose_command("", ["/tmp/report.csv"]), "process file /tmp/report.csv")

    def test_bare_multi_upload_uses_parallel_processor(self) -> None:
        result = _compose_command("", ["/tmp/a.csv", "/tmp/b.pdf"])
        self.assertEqual(result, "multi process file /tmp/a.csv ;; process file /tmp/b.pdf")

    def test_typed_message_keeps_command_and_appends_paths(self) -> None:
        result = _compose_command("what is the total revenue?", ["/tmp/sales.csv"])
        self.assertTrue(result.startswith("what is the total revenue?"))
        self.assertIn("/tmp/sales.csv", result)
        self.assertIn("attached file(s)", result)

    def test_ignores_blank_and_non_list_attachments(self) -> None:
        self.assertEqual(_compose_command("hi", [""]), "hi")
        self.assertEqual(_compose_command("hi", None), "hi")


if __name__ == "__main__":
    unittest.main()
