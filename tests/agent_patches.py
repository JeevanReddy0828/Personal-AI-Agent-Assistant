"""Patch an object in every module of `laptop_agent.agents` that holds it.

The orchestrator is being split into several modules under `agents/`. A test that freezes the
clock with `patch("laptop_agent.agents.orchestrator.datetime", ...)` freezes only the code still
in that one file: a method moved out reads the real clock, and the test passes on most days and
fails on some. Patching by object follows the code wherever it lives.
"""

from __future__ import annotations

import contextlib
import importlib
import pkgutil
from collections.abc import Iterator
from unittest.mock import patch

import laptop_agent.agents as agents


@contextlib.contextmanager
def patch_agents(original: object, new: object) -> Iterator[list[str]]:
    """Replace `original` with `new` wherever an `agents` module has it as a global.

    Yields the patched `module.name` pairs. Patching nothing raises, so a test cannot quietly
    stop freezing the clock when the last module using it is renamed.
    """
    modules = [importlib.import_module(f"{agents.__name__}.{info.name}")
               for info in pkgutil.iter_modules(agents.__path__)]
    targets = [(module, name) for module in modules
               for name, value in list(vars(module).items()) if value is original]
    if not targets:
        raise AssertionError(f"no module in {agents.__name__} holds {original!r}")
    with contextlib.ExitStack() as stack:
        for module, name in targets:
            stack.enter_context(patch.object(module, name, new))
        yield [f"{module.__name__}.{name}" for module, name in targets]
