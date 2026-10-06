"""Every folder explains itself, and a README cannot silently fall behind its folder.

Each folder carries a README.md with its purpose, usage, contents and connections. Without a
check, the first module added without a line in its folder's README starts the drift; so
every code file must be named there, directly or by a backtick glob such as `test_webui*.py`.
Data and images are left out on purpose: running an evaluation writes a results file beside
it, and that must not fail the suite.
"""

from __future__ import annotations

import fnmatch
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("src", "src/laptop_agent", "tests", "tests/data", "docs", "packaging", ".github/workflows")
CODE = {".py", ".js", ".css", ".html", ".ps1"}


def _folders() -> list[Path]:
    found: list[Path] = []
    for name in FOLDERS:
        base = ROOT / name
        if not base.is_dir():
            continue
        found.append(base)
        if name in ("src/laptop_agent", "docs"):
            found += [p for p in sorted(base.rglob("*")) if p.is_dir() and "__pycache__" not in p.parts]
    return found


class FolderReadmeTests(unittest.TestCase):
    def test_every_folder_has_a_readme(self) -> None:
        missing = [str(folder.relative_to(ROOT)) for folder in _folders() if not (folder / "README.md").is_file()]
        self.assertEqual(missing, [], "add a README.md (purpose, usage, contents, connections)")

    def test_every_code_file_is_named_in_its_folder_readme(self) -> None:
        unnamed: list[str] = []
        for folder in _folders():
            readme = folder / "README.md"
            if not readme.is_file():
                continue
            text = readme.read_text(encoding="utf-8")
            globs = re.findall(r"`([^`\s]*\*[^`\s]*)`", text)
            for path in sorted(folder.iterdir()):
                if not path.is_file() or path.suffix not in CODE:
                    continue
                if path.name not in text and not any(fnmatch.fnmatch(path.name, g) for g in globs):
                    unnamed.append(str(path.relative_to(ROOT)))
        self.assertEqual(unnamed, [], "name each file in its folder's README.md (one line on what it does)")


if __name__ == "__main__":
    unittest.main()
