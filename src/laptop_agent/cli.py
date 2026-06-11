from __future__ import annotations

import asyncio
import json

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
    asyncio.run(repl())


if __name__ == "__main__":
    main()
