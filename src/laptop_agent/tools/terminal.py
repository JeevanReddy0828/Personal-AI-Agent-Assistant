from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult


CommandRunner = Callable[[str, Path, int], subprocess.CompletedProcess[str]]


class TerminalTool:
    """Approval-gated local terminal command execution.

    This is intentionally explicit and synchronous: commands are previewed for
    approval, run with a timeout, and return captured stdout/stderr. It does not
    run hidden background services or stream interactive sessions.
    """

    def __init__(self, approval_gate: ApprovalGate, runner: CommandRunner | None = None) -> None:
        self.approval_gate = approval_gate
        self._runner = runner or self._run_subprocess

    def run(self, command: str, cwd: str | None = None, timeout: int = 30, max_output_chars: int = 12000) -> ToolResult:
        cleaned = command.strip()
        if not cleaned:
            return ToolResult.failure("Use: run command <command>")
        if len(cleaned) > 4000:
            return ToolResult.failure("Command is too long to run safely.")
        safe_timeout = max(1, min(timeout, 120))
        working_dir = Path(cwd or os.getcwd()).expanduser().resolve()
        if not working_dir.exists() or not working_dir.is_dir():
            return ToolResult.failure(f"Working directory does not exist: {working_dir}")

        self.approval_gate.require(
            ApprovalRequest(
                action=f"Run terminal command: {cleaned}",
                risk=RiskLevel.CRITICAL,
                reason="Terminal commands can read, modify, delete, or transmit local data.",
                preview=f"Working directory: {working_dir}\nTimeout: {safe_timeout}s\nCommand:\n{cleaned}",
            )
        )

        try:
            completed = self._runner(cleaned, working_dir, safe_timeout)
        except subprocess.TimeoutExpired as exc:
            return ToolResult.failure(
                f"Command timed out after {safe_timeout}s.",
                command=cleaned,
                cwd=str(working_dir),
                timeout=safe_timeout,
                stdout=self._clip(exc.stdout, max_output_chars),
                stderr=self._clip(exc.stderr, max_output_chars),
            )
        except OSError as exc:
            return ToolResult.failure(
                f"Command failed to start: {exc}",
                command=cleaned,
                cwd=str(working_dir),
            )

        ok = completed.returncode == 0
        stdout = self._clip(completed.stdout, max_output_chars)
        stderr = self._clip(completed.stderr, max_output_chars)
        message = "Command completed." if ok else f"Command exited with code {completed.returncode}."
        return ToolResult(
            ok=ok,
            message=message,
            data={
                "command": cleaned,
                "cwd": str(working_dir),
                "returncode": completed.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": len(str(completed.stdout or "")) > len(stdout) or len(str(completed.stderr or "")) > len(stderr),
            },
        )

    @staticmethod
    def _run_subprocess(command: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    @staticmethod
    def _clip(value: object, max_chars: int) -> str:
        text = "" if value is None else str(value)
        return text[:max(0, max_chars)]
