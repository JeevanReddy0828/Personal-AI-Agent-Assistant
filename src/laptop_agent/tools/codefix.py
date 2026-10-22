"""`code run <file.py>`, `code fix <file.py>` - run a Python script, and fix it from its own error.

"fix my script hello.py" reached the chat model, which can only guess at an error it never saw.
Here the script is run (asked first: it is code), and when it fails the model gets the file and
the error, returns the whole corrected file, and the change is shown as a diff on an approval
card before it is written. The old version is kept in `<data dir>/code_backups/` first. The
script runs with list arguments, never through a shell, in its own folder, with a time limit.
"""
from __future__ import annotations

import difflib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.failures import record_failure
from laptop_agent.tools.base import ToolResult

Writer = Callable[[str], str]
Runner = Callable[[Path], tuple[int, str]]
TIMEOUT = 30
MAX_SOURCE = 60_000
MAX_SHOWN = 4000

_PROMPT = """This Python script fails. Fix it so it runs and still does what it was written to do.
Change only what the error needs. Reply with the WHOLE corrected file in one ```python block and
nothing else.

File: {name}
```python
{source}
```

It printed:
```
{error}
```
"""


def run_python(path: Path) -> tuple[int, str]:
    """(exit code, output) of `python <path>` in the script's folder, cut off after TIMEOUT."""
    try:
        done = subprocess.run([sys.executable, str(path)], cwd=str(path.parent), capture_output=True, text=True,
                              timeout=TIMEOUT, stdin=subprocess.DEVNULL,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        return -1, (str(exc.stdout or "") + f"\n(stopped after {TIMEOUT} seconds)").strip()
    return done.returncode, (done.stdout + done.stderr).strip()


def corrected(reply: str) -> str | None:
    """The file inside the model's ```python block, or None."""
    block = re.search(r"```(?:python|py)?\s*\n(.*?)```", reply or "", re.DOTALL)
    code = block.group(1) if block else ""
    return code if code.strip() else None


class CodeFixTool:
    def __init__(self, writer: Writer | None, approval_gate: ApprovalGate, backups: Path,
                 runner: Runner = run_python) -> None:
        self._writer = writer
        self._gate = approval_gate
        self._backups = backups
        self._run = runner

    def _script(self, words: str) -> tuple[Path | None, str]:
        path = Path(words.strip().strip("\"'")).expanduser()
        if not path.is_absolute():
            for place in (Path.home() / "Desktop", Path.home() / "Documents", Path.home() / "Downloads", Path.cwd()):
                if (place / path).is_file():
                    path = place / path
                    break
        if path.suffix.lower() != ".py":
            return None, "I can run and fix Python scripts (.py) so far."
        if not path.is_file():
            return None, f"There's no script at {path}. Give me its full path."
        return path.resolve(), ""

    def _execute(self, path: Path) -> tuple[int, str]:
        self._gate.require(ApprovalRequest(action=f"Run the Python script {path}", risk=RiskLevel.HIGH,
                                           reason="Running a script runs whatever code it holds.", preview=str(path)))
        return self._run(path)

    def run(self, words: str) -> ToolResult:
        path, why = self._script(words)
        if path is None:
            return ToolResult.failure(why)
        code, output = self._execute(path)
        shown = output[-MAX_SHOWN:] or "(no output)"
        if code == 0:
            return ToolResult.success(f"{path.name} ran without errors.\n\n```\n{shown}\n```", exit_code=code)
        return ToolResult.failure(f"{path.name} failed (exit {code}):\n\n```\n{shown}\n```\n\nSay \"fix {path.name}\" "
                                  "and I'll propose a fix.", exit_code=code)

    def fix(self, words: str) -> ToolResult:
        path, why = self._script(words)
        if path is None:
            return ToolResult.failure(why)
        if self._writer is None:
            return ToolResult.failure("Fixing a script needs a language model, and none is configured.")
        original = path.read_bytes()
        identity = (path.stat().st_dev, path.stat().st_ino)
        source = original.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        if len(source) > MAX_SOURCE:
            return ToolResult.failure(f"{path.name} is over {MAX_SOURCE:,} characters; that's more than I fix in one go.")
        code, output = self._execute(path)
        if code == 0:
            return ToolResult.success(f"{path.name} already runs without errors, so there's nothing to fix.", exit_code=0)
        fixed = corrected(self._writer(_PROMPT.format(name=path.name, source=source, error=output[-MAX_SHOWN:])))
        if fixed is None or fixed.strip() == source.strip():
            return ToolResult.failure(f"The model didn't return a changed file, so {path.name} is as it was.\n\n"
                                      f"The error:\n```\n{output[-MAX_SHOWN:]}\n```")
        diff = "".join(difflib.unified_diff(source.splitlines(True), fixed.splitlines(True),
                                            f"{path.name} (now)", f"{path.name} (fixed)"))
        self._gate.require(ApprovalRequest(action=f"Write the fix to {path}", risk=RiskLevel.HIGH,
                                           reason="The old version is kept in code_backups first.", preview=diff[:6000]))
        def unchanged() -> bool:
            try:
                current = path.stat()
                return (current.st_dev, current.st_ino) == identity and path.read_bytes() == original
            except OSError as exc:
                record_failure("codefix/check-source", exc, path=str(path))
                return False

        if not unchanged():
            return ToolResult.failure(f"{path.name} changed while I was preparing its fix, so I left the newer file alone.")
        self._backups.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f"{path.stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-",
                                    suffix=path.suffix, dir=self._backups)
        backup = Path(name)
        with os.fdopen(fd, "wb") as output:
            output.write(original)
        if not unchanged():
            return ToolResult.failure(f"{path.name} changed while I was backing it up, so I left the newer file alone.")
        path.write_text(fixed, encoding="utf-8")
        return ToolResult.success(f"Fixed {path.name}; the old version is at {backup}. Say \"run {path.name}\" to check it.\n\n"
                                  f"```diff\n{diff[:MAX_SHOWN]}\n```", backup=str(backup), path=str(path))
