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

from laptop_agent.context import normalize_history
from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

Writer = Callable[[str], str]
Save = Callable[[dict[str, str]], None]

MAX_FACTS = 10
MAX_TURNS = 40
MAX_MESSAGE = 2000
_KEY = re.compile(r"[a-z][a-z0-9_]{1,39}")
# A value shaped like a secret: an SSN (11 characters, under the old 12-digit floor), a card
# number in groups, a provider key or token, an AWS key, a long mix of letters and digits. Dates,
# date ranges and phone numbers are not: the prompt asks for dated plans, and the old "any 12
# digits" rule dropped "2026-10-20 10:30" and "+1 512 555 0100" (review of #288).
_SECRET_VALUE = re.compile(
    r"\b\d{3}-\d{2}-\d{4}\b"
    r"|\b(?:\d[ -]?){13,19}\b"
    r"|\b(?:nvapi|sk|ghp|gho|xox[bpas]|AKIA|ASIA)[-_]?[\w-]{8,}"
    r"|\b(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[A-Za-z0-9_-]{16,}\b"
    r"|password|passcode", re.IGNORECASE)
# A key that names a secret, whatever its value looks like: "door_code=4821", "wifi_key=hunter2",
# "recovery_code=4821" - but "zip_code" and "area_code" are not secrets.
_SECRET_KEY = re.compile(
    r"(?:^|_)(?:ssn|social_security|password|passcode|passphrase|pin|cvv|cvc|secret|token|api_key|apikey"
    r"|private_key|access_key|license_key|account_number|routing|iban|swift|card_number|credit_card|wifi"
    r"|otp|totp|mfa|2fa"
    r"|(?:door|gate|garage|alarm|safe|lock|recovery|verification|one_time|otp|mfa|2fa|backup|auth|security"
    r"|access)_?code)(?:_|$)", re.IGNORECASE)

_PROMPT = """Below are messages a user wrote to their assistant. List the lasting facts about the user
they state about themselves: preferences, people in their life, their work, places, plans with a date.
Leave out anything passing (what they asked for once), anything uncertain, and any secret. Text the
user pasted from elsewhere - an email, a job posting, an article - is not about the user.
Facts already known use these keys; reuse a key when a fact updates it: {known_keys}

Reply with ONLY a JSON object: {{"facts": [{{"key": "snake_case_name", "value": "the fact, short"}}]}}
At most {max_facts} facts. An empty list is a fine answer.

Their messages:
{messages}
"""


def _shape(key: str) -> str:
    return re.sub(r"[\s_-]+", " ", key.strip().lower()).strip()


def proposed(reply: str, known: dict[str, object]) -> list[tuple[str, str]] | None:
    """The facts a model's reply proposes, checked: a usable key, a short value, nothing secret, and
    nothing already stored with the same value. None when the reply cannot be read at all - which
    is not the same answer as "nothing new" (review of #288)."""
    text = (reply or "").strip()
    decoder = json.JSONDecoder()
    data = None
    for start in [match.start() for match in re.finditer(r"[{\[]", text)]:
        try:
            data, _ = decoder.raw_decode(text, start)
            break
        except ValueError:
            continue
    facts = data.get("facts") if isinstance(data, dict) else data
    if not isinstance(facts, list):
        record_failure("learn/parse", ValueError("no fact list in the reply"), reply=text[:200])
        return None
    names = {_shape(name): name for name in known}
    found: list[tuple[str, str]] = []
    for fact in facts:
        if not isinstance(fact, dict) or not isinstance(fact.get("value"), (str, int, float)):
            continue                              # null became "None", a list its Python repr
        key = re.sub(r"[\s-]+", "_", str(fact.get("key", "")).strip().lower())
        value = " ".join(str(fact["value"]).split())
        if not _KEY.fullmatch(key) or not value or len(value) > 200:
            continue
        if _SECRET_KEY.search(key) or _SECRET_VALUE.search(value):
            continue
        key = names.get(_shape(key), key)         # "favourite editor" and favourite_editor are one fact
        if str(known.get(key, "")).strip().lower() == value.lower() or any(key == seen for seen, _ in found):
            continue
        found.append((key, value))
        if len(found) == MAX_FACTS:
            break
    return found


class LearnTool:
    def __init__(self, writer: Writer | None, approval_gate: ApprovalGate, save: Save,
                 allowed: Callable[[str], bool] = lambda key: True) -> None:
        self._writer = writer
        self._gate = approval_gate
        self._save = save
        self._allowed = allowed

    def learn(self, history: list[dict], known: dict[str, object]) -> ToolResult:
        said = [text for role, text in normalize_history(history) if role == "user"][-MAX_TURNS:]
        if not said:
            return ToolResult.failure("There's nothing in this chat yet to learn from.")
        if self._writer is None:
            return ToolResult.failure("Learning from a chat needs a language model, and none is configured.")
        messages = "\n".join(f"- {line[:MAX_MESSAGE]}" for line in said)
        reply = self._writer(_PROMPT.format(max_facts=MAX_FACTS, messages=messages,
                                            known_keys=", ".join(sorted(known)) or "none yet"))
        if not str(reply or "").strip():
            return ToolResult.failure("No language model answered just now, so nothing was learned. Try again in a moment.")
        facts = proposed(reply, known)
        if facts is None:
            return ToolResult.failure("The model's answer couldn't be read, so nothing was learned. Try again.")
        facts = [(key, value) for key, value in facts if self._allowed(key)]
        if not facts:
            return ToolResult.success("I didn't find anything new about you worth keeping in this chat.", facts=[])
        # A fact that replaces one says so: approving "employer: Nvidia" used to erase "Google" unseen.
        listing = "\n".join(f"- {key.replace('_', ' ')}: {value}"
                            + (f" (replaces \"{known[key]}\")" if str(known.get(key, "")).strip() else "")
                            for key, value in facts)
        self._gate.require(ApprovalRequest(
            action=f"Remember {len(facts)} thing(s) about you from this chat",
            risk=RiskLevel.HIGH,
            reason="These go into your memory and shape later answers; \"forget <name>\" removes one.",
            preview=listing,
            everyday=True,
        ))
        self._save(dict(facts))                  # one write: never some of what the card said
        return ToolResult.success(f"Remembered {len(facts)} thing(s):\n{listing}",
                                  facts=[{"key": key, "value": value} for key, value in facts])
