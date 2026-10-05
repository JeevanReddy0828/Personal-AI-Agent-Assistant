from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AgentsFileTests(unittest.TestCase):
    """AGENTS.md is loaded whole into every session of both agents - Codex reads it with a
    32 KiB combined cap, Claude Code through the import in CLAUDE.md - so it must stay small,
    and its map must send each reader to a section that exists."""

    def setUp(self) -> None:
        self.agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")

    def test_it_stays_inside_its_budget(self) -> None:
        self.assertLess(len(self.agents.encode("utf-8")), 8 * 1024)

    def test_claude_code_loads_it(self) -> None:
        self.assertRegex(self.claude, r"(?m)@AGENTS\.md\b")

    def test_every_section_it_names_is_in_claude_md(self) -> None:
        rows = [line for line in self.agents.splitlines() if line.startswith("| ") and "---" not in line][1:]
        named = [name.rstrip("…").rstrip(".") for row in rows for name in re.findall(r'"([^"]+)"', row.split("|")[2])]
        self.assertGreater(len(named), 10)
        missing = [name for name in named if name not in self.claude]
        self.assertEqual(missing, [], "AGENTS.md points at CLAUDE.md sections that are not there")


if __name__ == "__main__":
    unittest.main()
