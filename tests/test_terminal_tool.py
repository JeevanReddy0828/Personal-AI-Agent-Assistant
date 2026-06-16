from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalGate
from laptop_agent.tools.terminal import TerminalTool


class TerminalToolTests(unittest.TestCase):
    def test_runs_after_approval(self) -> None:
        calls = []

        def runner(command: str, cwd: Path, timeout: int):
            calls.append((command, cwd, timeout))
            return subprocess.CompletedProcess(command, 0, stdout="hello\n", stderr="")

        with tempfile.TemporaryDirectory() as raw:
            tool = TerminalTool(ApprovalGate(lambda request: True), runner=runner)
            result = tool.run("echo hello", cwd=raw)

        self.assertTrue(result.ok)
        self.assertEqual(result.data["stdout"], "hello\n")
        self.assertEqual(calls[0][0], "echo hello")

    def test_denied_approval_does_not_run(self) -> None:
        calls = []
        tool = TerminalTool(
            ApprovalGate(lambda request: False),
            runner=lambda command, cwd, timeout: calls.append(command) or subprocess.CompletedProcess(command, 0),
        )

        with self.assertRaises(Exception):
            tool.run("echo nope")

        self.assertEqual(calls, [])

    def test_timeout_returns_failure(self) -> None:
        def runner(command: str, cwd: Path, timeout: int):
            raise subprocess.TimeoutExpired(command, timeout, output="partial", stderr="slow")

        tool = TerminalTool(ApprovalGate(lambda request: True), runner=runner)
        result = tool.run("slow", timeout=1)

        self.assertFalse(result.ok)
        self.assertIn("timed out", result.message)
        self.assertEqual(result.data["stdout"], "partial")

    def test_missing_cwd_fails_before_approval(self) -> None:
        approvals = []
        tool = TerminalTool(ApprovalGate(lambda request: approvals.append(request) or True))
        result = tool.run("echo hi", cwd="Z:/missing/path")

        self.assertFalse(result.ok)
        self.assertEqual(approvals, [])


if __name__ == "__main__":
    unittest.main()
