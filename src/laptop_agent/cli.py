from __future__ import annotations

import asyncio
import json
import sys

from laptop_agent.app import build_orchestrator
from laptop_agent.safety import ApprovalDenied


async def repl() -> None:
    orchestrator = build_orchestrator()
    print("Laptop Agent MVP. Type 'help' for commands, 'exit' to quit.")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.lower() in {"exit", "quit"}:
            return
        try:
            result = await orchestrator.handle(text)
        except ApprovalDenied as exc:
            print(f"Denied: {exc}")
            continue
        print(result.message)
        if result.data:
            print(json.dumps(_json_safe(result.data), indent=2, default=str))


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_safe(value.__dict__)
    return value


def main() -> None:
    # Windows terminals default to cp1252, which can't encode characters that appear
    # in tool messages (arrows, curly quotes, em dashes). Print as UTF-8 so the CLI
    # never crashes with a UnicodeEncodeError mid-answer.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    asyncio.run(repl())


if __name__ == "__main__":
    main()
