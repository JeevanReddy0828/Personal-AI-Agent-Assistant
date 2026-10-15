"""`learn from chat` - the lasting facts the user said in this conversation, saved once approved.

A fact reached memory only when said as "remember …": a whole conversation about a new job, a
dentist or a favourite editor left nothing behind. Here the model reads the user's own messages
(never the assistant's replies, which carry web pages and tool output that could plant a "fact"),
proposes key/value facts as JSON, and one approval card lists them before any is stored. What
looks like a secret is dropped before the card.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

Writer = Callable[[str], str]
Save = Callable[[str, str], None]

MAX_FACTS = 10
MAX_TURNS = 40
_KEY = re.compile(r"[a-z][a-z0-9_]{1,39}")
# A long run of digits (a card or account number), a key, a password: never stored as a fact.
_SECRET = re.compile(r"\d[\d -]{10,}\d|\b(?:nvapi|sk|ghp|xox[bp])[-_][\w-]{8,}|password|passcode"
                     r"|(?<![a-z])(?:pin|cvv|ssn)(?![a-z])", re.IGNORECASE)

_PROMPT = """Below are messages a user wrote to their assistant. List the lasting facts about the user
they state about themselves: preferences, people in their life, their work, places, plans with a date.
Leave out anything passing (what they asked for once), anything uncertain, and any secret.

Reply with ONLY a JSON object: {{"facts": [{{"key": "snake_case_name", "value": "the fact, short"}}]}}
At most {max_facts} facts. An empty list is a fine answer.

Their messages:
{messages}
"""


def proposed(reply: str, known: dict[str, object]) -> list[tuple[str, str]]:
    """The facts a model's reply proposes, checked: a usable key, a short value, nothing secret, and
    nothing already stored with the same value."""
    text = (reply or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        facts = json.loads(text[start:end + 1]).get("facts")
    except (ValueError, AttributeError):
        return []
    found: list[tuple[str, str]] = []
    for fact in facts if isinstance(facts, list) else []:
        if not isinstance(fact, dict):
            continue
        key = re.sub(r"[\s-]+", "_", str(fact.get("key", "")).strip().lower())
        value = " ".join(str(fact.get("value", "")).split())
        if not _KEY.fullmatch(key) or not value or len(value) > 200:
            continue
        if _SECRET.search(key) or _SECRET.search(value):
            continue
        if str(known.get(key, "")).strip().lower() == value.lower() or any(key == seen for seen, _ in found):
            continue
        found.append((key, value))
        if len(found) == MAX_FACTS:
            break
    return found


class LearnTool:
    def __init__(self, writer: Writer | None, approval_gate: ApprovalGate, save: Save) -> None:
        self._writer = writer
        self._gate = approval_gate
        self._save = save

    def learn(self, history: list[dict], known: dict[str, object]) -> ToolResult:
        said = [str(turn.get("text") or "").strip() for turn in history or []
                if turn.get("role") == "user" and str(turn.get("text") or "").strip()][-MAX_TURNS:]
        if not said:
            return ToolResult.failure("There's nothing in this chat yet to learn from.")
        if self._writer is None:
            return ToolResult.failure("Learning from a chat needs a language model, and none is configured.")
        messages = "\n".join(f"- {line[:500]}" for line in said)
        facts = proposed(self._writer(_PROMPT.format(max_facts=MAX_FACTS, messages=messages)), known)
        if not facts:
            return ToolResult.success("I didn't find anything new about you worth keeping in this chat.", facts=[])
        listing = "\n".join(f"- {key.replace('_', ' ')}: {value}" for key, value in facts)
        self._gate.require(ApprovalRequest(
            action=f"Remember {len(facts)} thing(s) about you from this chat",
            risk=RiskLevel.HIGH,
            reason="These go into your memory and shape later answers; \"forget <name>\" removes one.",
            preview=listing,
            everyday=True,
        ))
        for key, value in facts:
            self._save(key, value)
        return ToolResult.success(f"Remembered {len(facts)} thing(s):\n{listing}",
                                  facts=[{"key": key, "value": value} for key, value in facts])
