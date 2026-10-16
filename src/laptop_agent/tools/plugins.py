"""`plugins`, `plugin <name> <request>` - commands the owner adds as Python files, when switched on.

A new capability meant a change to the orchestrator, the router and the access lists. With
`LAPTOP_AGENT_PLUGINS=1`, each `<data dir>/plugins/*.py` defining COMMAND, DESCRIPTION, RISK and
`run(argument) -> str` is one more command. A plugin is the owner's own code and runs in this
process, so the switch is off unless set, the folder is read once, the first time a plugin is asked
for (a file written there later is not picked up until the next start), every run goes through the approval gate at the
risk the plugin declares, and a plugin that fails to load or raises is recorded, never fatal.
"""
from __future__ import annotations

import importlib.util
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

ENABLE = "LAPTOP_AGENT_PLUGINS"
_NAME = re.compile(r"[a-z][a-z0-9-]{1,30}")
_RISKS = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH, "critical": RiskLevel.CRITICAL}
MAX_REPLY = 20_000


@dataclass(frozen=True)
class Plugin:
    name: str
    description: str
    risk: RiskLevel
    run: Callable[[str], object] | None
    file: str
    error: str = ""


def enabled() -> bool:
    return os.environ.get(ENABLE, "").strip() == "1"


def load(folder: Path) -> list[Plugin]:
    """Every plugin file in `folder`, each loaded or with the reason it was not."""
    found: list[Plugin] = []
    for path in sorted(folder.glob("*.py")) if folder.is_dir() else []:
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"jarvis_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # the owner's own code, opted into with LAPTOP_AGENT_PLUGINS=1
            name = str(getattr(module, "COMMAND", "")).strip().lower()
            risk = _RISKS.get(str(getattr(module, "RISK", "")).strip().lower())
            run = getattr(module, "run", None)
            problem = ("COMMAND must be 2-31 lowercase letters, digits or hyphens" if not _NAME.fullmatch(name)
                       else "RISK must be low, medium, high or critical" if risk is None
                       else "run(argument) is missing" if not callable(run) else "")
        except Exception as exc:  # a broken plugin is reported, and the others still load
            record_failure("plugins/load", exc, file=path.name)
            found.append(Plugin(path.stem, "", RiskLevel.CRITICAL, None, path.name, f"{type(exc).__name__}: {exc}"))
            continue
        if problem:
            found.append(Plugin(name or path.stem, "", RiskLevel.CRITICAL, None, path.name, problem))
            continue
        if any(plugin.name == name and plugin.run for plugin in found):
            found.append(Plugin(name, "", risk, None, path.name, f"another file already uses the name {name}"))
            continue
        found.append(Plugin(name, str(getattr(module, "DESCRIPTION", "")).strip()[:200], risk, run, path.name))
    return found


class PluginTool:
    def __init__(self, approval_gate: ApprovalGate, folder: Path, on: bool | None = None) -> None:
        self._gate = approval_gate
        self._folder = folder
        self._on = enabled() if on is None else on
        self._plugins = load(folder) if self._on else []

    def listing(self) -> ToolResult:
        if not self._on:
            return ToolResult.failure(f"Plugins are off. Set {ENABLE}=1 and put plugin files in {self._folder}, then restart.")
        if not self._plugins:
            return ToolResult.success(f"No plugins in {self._folder}.", plugins=[])
        lines = [f"- **{p.name}** ({p.risk.value}): {p.description}" if p.run else f"- {p.file}: not loaded - {p.error}"
                 for p in self._plugins]
        return ToolResult.success("Plugins:\n" + "\n".join(lines),
                                  plugins=[{"name": p.name, "file": p.file, "ok": bool(p.run), "error": p.error}
                                           for p in self._plugins])

    def run(self, name: str, argument: str) -> ToolResult:
        if not self._on:
            return ToolResult.failure(f"Plugins are off. Set {ENABLE}=1 to use them.")
        plugin = next((p for p in self._plugins if p.name == name.strip().lower() and p.run), None)
        if plugin is None:
            return ToolResult.failure(f"There's no plugin called {name!r}. Say \"plugins\" to see them.")
        self._gate.require(ApprovalRequest(action=f"Run plugin {plugin.name}: {argument[:120]}", risk=plugin.risk,
                                           reason=f"Your plugin {plugin.file} runs its own code.", preview=argument))
        try:
            reply = plugin.run(argument)
        except Exception as exc:
            record_failure("plugins/run", exc, plugin=plugin.name)
            return ToolResult.failure(f"Plugin {plugin.name} failed: {type(exc).__name__}: {exc}")
        return ToolResult.success(str(reply if reply is not None else "Done.")[:MAX_REPLY], plugin=plugin.name)
