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
        # Since 2026-10-06 the detail lives in docs/design/; CLAUDE.md is the core.
        self.design = "\n".join(p.read_text(encoding="utf-8") for p in sorted((ROOT / "docs" / "design").glob("*.md")))

    def test_it_stays_inside_its_budget(self) -> None:
        self.assertLess(len(self.agents.encode("utf-8")), 8 * 1024)

    def test_claude_code_loads_it(self) -> None:
        self.assertRegex(self.claude, r"(?m)@AGENTS\.md\b")

    def test_every_section_it_names_is_in_claude_md(self) -> None:
        # Name kept so its history reads on; it now checks the design notes the map points at.
        rows = [line for line in self.agents.splitlines() if line.startswith("| ") and "---" not in line][1:]
        named = [name.rstrip("…").rstrip(".") for row in rows for name in re.findall(r'"([^"]+)"', row.split("|")[2])]
        self.assertGreater(len(named), 8)
        missing = [name for name in named if name not in self.design and name not in self.claude]
        self.assertEqual(missing, [], "AGENTS.md points at sections that are not there")

    def test_every_file_it_names_exists(self) -> None:
        rows = [line for line in self.agents.splitlines() if line.startswith("| ") and "---" not in line][1:]
        files = {name for row in rows for name in re.findall(r"`([\w./-]+\.md)`", row.split("|")[2])}
        self.assertGreater(len(files), 8)
        missing = [name for name in sorted(files)
                   if not (ROOT / "docs" / "design" / name).is_file() and not (ROOT / name).is_file()]
        self.assertEqual(missing, [], "AGENTS.md names design files that do not exist")

    def test_claude_md_stays_a_core(self) -> None:
        # It was 127 KB, re-read whole by every session; the detail moved to docs/design/.
        self.assertLess(len(self.claude.encode("utf-8")), 24 * 1024)


if __name__ == "__main__":
    unittest.main()
