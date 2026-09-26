"""Coin flips, dice and random numbers, drawn rather than described.

"flip a coin" reached a chat model, which cannot draw anything at random - it produces
the most likely continuation, which is the same answer every time it is asked the same
way. `secrets` rather than `random` so no seed ties one answer to the next.
"""

from __future__ import annotations

import re
import secrets

from laptop_agent.tools.base import ToolResult

_POLITE = r"^\s*(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+)?"
_COIN = re.compile(_POLITE + r"(?:(?:flip|toss)\s+(?:a|one)\s+coin|heads\s+or\s+tails)(?:\s+for\s+me)?\s*[?.!]*$",
                   re.IGNORECASE)
# Any length of number is matched and then refused by size: a pattern that stopped at two
# digits sent "roll 99999999999999999999 dice" to a chat model to invent a roll.
_DICE = re.compile(
    _POLITE + r"roll\s+(?:(?:a|an|one)|(?P<count>\d+|two|three|four|five|six))\s+"
    r"(?:(?P<sides>\d+)[\s-]*sided\s+)?(?:dice|die|d(?P<d>\d+))(?:\s+for\s+me)?\s*[?.!]*$",
    re.IGNORECASE,
)
_PICK = re.compile(
    _POLITE + r"(?:pick|choose|give\s+me|generate|think\s+of)\s+(?:me\s+)?a\s+(?:random\s+)?number"
    r"(?:\s+(?:between|from)\s+(?P<low>-?\d+)\s+(?:and|to|-)\s+(?P<high>-?\d+))?\s*[?.!]*$",
    re.IGNORECASE,
)
_COUNT_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def is_chance_request(text: str) -> bool:
    return any(pattern.match(text or "") for pattern in (_COIN, _DICE, _PICK))


def draw(text: str) -> ToolResult | None:
    """The draw a request asks for, or None when it is not one."""
    if _COIN.match(text or ""):
        return ToolResult.success(f"**{secrets.choice(('Heads', 'Tails'))}**.", kind="coin")
    dice = _DICE.match(text or "")
    if dice:
        raw = (dice.group("count") or "1").lower()
        sides_raw = dice.group("sides") or dice.group("d") or "6"
        count = _COUNT_WORDS.get(raw) or (int(raw) if len(raw) <= 4 else 0)
        sides = int(sides_raw) if len(sides_raw) <= 5 else 0
        if not 1 <= count <= 20 or not 2 <= sides <= 1000:
            return ToolResult.failure("I can roll 1 to 20 dice with 2 to 1000 sides each.")
        rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
        if count == 1:
            article = "an" if str(rolls[0]).startswith("8") or rolls[0] in (11, 18) else "a"
            return ToolResult.success(f"You rolled {article} **{rolls[0]}**.", kind="dice", rolls=rolls)
        return ToolResult.success(f"You rolled {', '.join(map(str, rolls))} — **{sum(rolls)}** in total.",
                                  kind="dice", rolls=rolls)
    pick = _PICK.match(text or "")
    if pick:
        if any(len(bound.lstrip("-")) > 18 for bound in (pick.group("low") or "", pick.group("high") or "")):
            return ToolResult.failure("Give me a range with smaller numbers than that.")
        low, high = (int(pick.group("low")), int(pick.group("high"))) if pick.group("low") else (1, 100)
        low, high = min(low, high), max(low, high)
        number = low + secrets.randbelow(high - low + 1)
        return ToolResult.success(f"**{number}** (between {low} and {high}).", kind="number", value=number)
    return None
