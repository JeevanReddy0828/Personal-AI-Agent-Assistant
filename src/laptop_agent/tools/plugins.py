"""`plugins`, `plugin <name> <request>` - commands the owner adds as Python files, when switched on.

A new capability meant a change to the orchestrator, the router and the access lists. With
`LAPTOP_AGENT_PLUGINS=1`, each `<data dir>/plugins/*.py` defining COMMAND, DESCRIPTION, RISK and
`run(argument) -> str` is one more command. A plugin is the owner's own code and runs in this
process, so: the switch is off unless set; loading the folder runs every file, so it asks first
(HIGH, once); every run asks at HIGH or above - a file's RISK can raise its card to CRITICAL, never
lower it, since "low" skipped the gate and "medium" ran with no card in the web app; the agent
loop cannot call one; and a plugin that fails to load or raises - `sys.exit` included - is
recorded, never fatal. Files load as the package `jarvis_plugins`, so a helper `_name.py` is
imported with `from . import _name`.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sys
import threading
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from laptop_agent.cancellation import OperationCancelled
from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

ENABLE = "LAPTOP_AGENT_PLUGINS"
PACKAGE = "jarvis_plugins"
_NAME = re.compile(r"[a-z][a-z0-9-]{1,30}")
_RISKS = {"low": RiskLevel.HIGH, "medium": RiskLevel.HIGH, "high": RiskLevel.HIGH, "critical": RiskLevel.CRITICAL}
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


def _risk(declared: object) -> RiskLevel | None:
    """The card a plugin runs behind: its RISK, raised to HIGH. The project's own RiskLevel works
    too (str() of it is "RiskLevel.HIGH" on 3.11, which was refused)."""
    value = declared.value if isinstance(declared, RiskLevel) else str(declared or "").strip().lower()
    return _RISKS.get(value)


def _package(folder: Path) -> None:
    """A fresh package for each load: a helper cached from an earlier folder (another data
    directory) or an earlier version of the file would otherwise answer `from . import _name`."""
    for name in [name for name in sys.modules if name.startswith(PACKAGE + ".")]:
        sys.modules.pop(name, None)
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(folder)]
    sys.modules[PACKAGE] = package


def load(folder: Path) -> list[Plugin]:
    """Every plugin file in `folder`, each loaded or with the reason it was not."""
    found: list[Plugin] = []
    if not folder.is_dir():
        return found
    _package(folder)
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        qualified = f"{PACKAGE}.{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(qualified, path)
            module = importlib.util.module_from_spec(spec)
            # Registered before running, as importlib's own recipe does: a dataclass or pickle in the
            # file looks itself up in sys.modules, and failed without it.
            sys.modules[qualified] = module
            spec.loader.exec_module(module)  # the owner's own code, opted into with LAPTOP_AGENT_PLUGINS=1
            name = str(getattr(module, "COMMAND", "")).strip().lower()
            risk = _risk(getattr(module, "RISK", ""))
            run = getattr(module, "run", None)
            problem = ("COMMAND must be 2-31 lowercase letters, digits or hyphens" if not _NAME.fullmatch(name)
                       else "RISK must be low, medium, high or critical" if risk is None
                       else "run(argument) is missing" if not callable(run)
                       else "run must be a plain function, not async" if inspect.iscoroutinefunction(run) else "")
        except OperationCancelled:
            raise
        except BaseException as exc:  # sys.exit in a file is a broken plugin, not the app quitting
            sys.modules.pop(qualified, None)
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
        self._plugins: list[Plugin] | None = None
        self._lock = threading.Lock()   # two first uses at once ran every file twice

    @property
    def on(self) -> bool:
        return self._on

    def _loaded(self) -> list[Plugin]:
        with self._lock:
            if self._plugins is None:
                files = sorted(path.name for path in self._folder.glob("*.py")) if self._folder.is_dir() else []
                if files:
                    self._gate.require(ApprovalRequest(
                        action=f"Load your plugins from {self._folder}", risk=RiskLevel.HIGH,
                        reason="Loading a plugin runs its file's code in this app.", preview="\n".join(files)))
                self._plugins = load(self._folder)
            return self._plugins

    def names(self) -> set[str]:
        """The names a sentence starting "plugin" may use. Before the folder is loaded (which asks
        first, since it runs the files) they are read off each file's `COMMAND = "..."` line, so a
        typed "plugin shout hi" is a command from the start; nothing is run to find them."""
        if not self._on:
            return set()
        if self._plugins is not None:
            return {plugin.name for plugin in self._plugins if plugin.run}
        found = set()
        for path in sorted(self._folder.glob("*.py")) if self._folder.is_dir() else []:
            try:
                written = re.search(r"^COMMAND\s*=\s*[\"']([^\"']+)[\"']", path.read_text(encoding="utf-8"), re.M)
            except (OSError, UnicodeDecodeError) as exc:
                record_failure("plugins/names", exc, file=path.name)
                continue
            if written:
                found.add(written.group(1).strip().lower())
        return found

    def listing(self) -> ToolResult:
        if not self._on:
            return ToolResult.failure(f"Plugins are off. Set {ENABLE}=1 and put plugin files in {self._folder}, then restart.")
        plugins = self._loaded()
        if not plugins:
            return ToolResult.success(f"No plugins in {self._folder}.", plugins=[])
        lines = [f"- **{p.name}** ({p.risk.value}): {p.description}" if p.run else f"- {p.file}: not loaded - {p.error}"
                 for p in plugins]
        return ToolResult.success("Plugins:\n" + "\n".join(lines),
                                  plugins=[{"name": p.name, "file": p.file, "ok": bool(p.run), "error": p.error}
                                           for p in plugins])

    def run(self, name: str, argument: str) -> ToolResult:
        if not self._on:
            return ToolResult.failure(f"Plugins are off. Set {ENABLE}=1 to use them.")
        plugin = next((p for p in self._loaded() if p.name == name.strip().lower() and p.run), None)
        if plugin is None:
            return ToolResult.failure(f"There's no plugin called {name!r}. Say \"plugins\" to see them.")
        self._gate.require(ApprovalRequest(action=f"Run plugin {plugin.name}: {argument[:120]}", risk=plugin.risk,
                                           reason=f"Your plugin {plugin.file} runs its own code.", preview=argument))
        try:
            reply = plugin.run(argument)
        except OperationCancelled:
            raise
        except BaseException as exc:   # sys.exit or KeyboardInterrupt in a plugin ends that plugin, not the app
            record_failure("plugins/run", exc, plugin=plugin.name)
            return ToolResult.failure(f"Plugin {plugin.name} failed: {type(exc).__name__}: {exc}")
        text = str(reply if reply is not None else "Done.")
        if len(text) > MAX_REPLY:
            text = text[:MAX_REPLY] + f"\n\n… (cut: the plugin's reply was {len(text):,} characters)"
        return ToolResult.success(text, plugin=plugin.name)
