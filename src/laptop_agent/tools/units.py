"""Unit conversions, computed rather than recalled.

"convert 5 miles to km", "how many ounces in a pound", "100 fahrenheit to celsius" all
reached a chat model. A value with exactly one right answer is parsed, never inferred -
the same reason the calculator and the reminder parser exist.

US customary volumes (cup, pint, quart, gallon, fluid ounce) are the US ones: a British
pint is 20% bigger, and the answer says which it used.
"""

from __future__ import annotations

import re

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.calculator import _NUMBER_WORD, _words_to_number

# unit -> (dimension, how many base units one of it is). Bases: metre, gram, litre,
# second, byte, metre-per-second. Temperature is handled on its own.
_FACTORS: dict[str, tuple[str, float]] = {
    "millimetre": ("length", 0.001), "centimetre": ("length", 0.01), "metre": ("length", 1.0),
    "kilometre": ("length", 1000.0), "inch": ("length", 0.0254), "foot": ("length", 0.3048),
    "yard": ("length", 0.9144), "mile": ("length", 1609.344), "nautical mile": ("length", 1852.0),
    "milligram": ("mass", 0.001), "gram": ("mass", 1.0), "kilogram": ("mass", 1000.0),
    "ounce": ("mass", 28.349523125), "pound": ("mass", 453.59237), "stone": ("mass", 6350.29318),
    "tonne": ("mass", 1_000_000.0), "ton": ("mass", 907_184.74),
    "millilitre": ("volume", 0.001), "litre": ("volume", 1.0), "teaspoon": ("volume", 0.00492892159375),
    "tablespoon": ("volume", 0.01478676478125), "fluid ounce": ("volume", 0.0295735295625),
    "cup": ("volume", 0.2365882365), "pint": ("volume", 0.473176473), "quart": ("volume", 0.946352946),
    "gallon": ("volume", 3.785411784),
    "second": ("time", 1.0), "minute": ("time", 60.0), "hour": ("time", 3600.0), "day": ("time", 86400.0),
    "week": ("time", 604800.0), "year": ("time", 31_557_600.0),
    "byte": ("data", 1.0), "kilobyte": ("data", 1e3), "megabyte": ("data", 1e6), "gigabyte": ("data", 1e9),
    "terabyte": ("data", 1e12), "kibibyte": ("data", 1024.0), "mebibyte": ("data", 1024.0 ** 2),
    "gibibyte": ("data", 1024.0 ** 3),
    "metre per second": ("speed", 1.0), "kilometre per hour": ("speed", 1000 / 3600),
    "mile per hour": ("speed", 1609.344 / 3600), "knot": ("speed", 1852 / 3600),
    "celsius": ("temperature", 1.0), "fahrenheit": ("temperature", 1.0), "kelvin": ("temperature", 1.0),
}

# How each is written or said, longest first when matching.
_SPELLINGS: dict[str, tuple[str, ...]] = {
    "millimetre": ("millimeters", "millimetres", "millimeter", "millimetre", "mm"),
    "centimetre": ("centimeters", "centimetres", "centimeter", "centimetre", "cm"),
    "kilometre": ("kilometers", "kilometres", "kilometer", "kilometre", "km", "kms"),
    "metre": ("meters", "metres", "meter", "metre", "m"),
    "inch": ("inches", "inch", "in"),
    "foot": ("feet", "foot", "ft"),
    "yard": ("yards", "yard", "yd", "yds"),
    "nautical mile": ("nautical miles", "nautical mile", "nmi"),
    "mile": ("miles", "mile", "mi"),
    "milligram": ("milligrams", "milligram", "mg"),
    "kilogram": ("kilograms", "kilogram", "kilos", "kilo", "kg", "kgs"),
    "gram": ("grams", "gram", "g"),
    "ounce": ("ounces", "ounce", "oz"),
    "pound": ("pounds", "pound", "lbs", "lb"),
    "stone": ("stones", "stone", "st"),
    "tonne": ("tonnes", "tonne", "metric tons", "metric ton"),
    "ton": ("tons", "ton"),
    "millilitre": ("milliliters", "millilitres", "milliliter", "millilitre", "ml"),
    "litre": ("liters", "litres", "liter", "litre", "l"),
    "teaspoon": ("teaspoons", "teaspoon", "tsp"),
    "tablespoon": ("tablespoons", "tablespoon", "tbsp"),
    "fluid ounce": ("fluid ounces", "fluid ounce", "fl oz", "floz"),
    "cup": ("cups", "cup"),
    "pint": ("pints", "pint", "pt"),
    "quart": ("quarts", "quart", "qt"),
    "gallon": ("gallons", "gallon", "gal"),
    "second": ("seconds", "second", "secs", "sec", "s"),
    "minute": ("minutes", "minute", "mins", "min"),
    "hour": ("hours", "hour", "hrs", "hr", "h"),
    "day": ("days", "day"),
    "week": ("weeks", "week"),
    "year": ("years", "year", "yrs", "yr"),
    "byte": ("bytes", "byte", "b"),
    "kilobyte": ("kilobytes", "kilobyte", "kb"),
    "megabyte": ("megabytes", "megabyte", "mb"),
    "gigabyte": ("gigabytes", "gigabyte", "gb"),
    "terabyte": ("terabytes", "terabyte", "tb"),
    "kibibyte": ("kibibytes", "kibibyte", "kib"),
    "mebibyte": ("mebibytes", "mebibyte", "mib"),
    "gibibyte": ("gibibytes", "gibibyte", "gib"),
    "metre per second": ("meters per second", "metres per second", "m/s", "mps"),
    "kilometre per hour": ("kilometers per hour", "kilometres per hour", "km/h", "kmh", "kph"),
    "mile per hour": ("miles per hour", "mph"),
    "knot": ("knots", "knot", "kn", "kt"),
    "celsius": ("degrees celsius", "degree celsius", "celsius", "centigrade", "°c", "c"),
    "fahrenheit": ("degrees fahrenheit", "degree fahrenheit", "fahrenheit", "°f", "f"),
    "kelvin": ("kelvins", "kelvin", "k"),
}
_LOOKUP = {spelling: unit for unit, spellings in _SPELLINGS.items() for spelling in spellings}
_UNIT = "(?:" + "|".join(re.escape(s) for s in sorted(_LOOKUP, key=len, reverse=True)) + ")"
_NUMBER = r"-?\d+(?:[.,]\d+)?"

# "convert 5 miles to km", "5 miles in km", "what's 100 f in c", "30 degrees celsius to f"
_FORWARD = re.compile(
    rf"^\s*(?:(?:please\s+)?convert|what(?:'s|s|\s+is)|how\s+much\s+is|how\s+(?:tall|heavy)\s+is"
    rf"|how\s+many\s+\w+(?:\s+\w+)?\s+(?:is|are))?\s*"
    rf"(?P<n>{_NUMBER})\s*(?:degrees?\s+)?(?P<from>{_UNIT})\s+(?:to|in|into|as|in\s+terms\s+of)\s+"
    rf"(?:degrees?\s+)?(?P<to>{_UNIT})\s*[?.!]*\s*$",
    re.IGNORECASE,
)
# "how many ounces in a pound", "how many cm are in 6 feet", "how many cups in 2 liters"
_HOW_MANY = re.compile(
    rf"^\s*how\s+many\s+(?P<to>{_UNIT})\s+(?:are\s+)?(?:there\s+)?(?:in|is|are|per|make\s+up)\s+"
    rf"(?:an?\s+|one\s+)?(?P<n>{_NUMBER})?\s*(?P<from>{_UNIT})\s*[?.!]*\s*$",
    re.IGNORECASE,
)


# Amounts said as fractions: "a quarter cup", "half a cup", "three quarters of a cup", "one and
# a half cups", "a cup and a half", "3/4 cup". Rewritten only where an amount stands - just
# before a unit - so a "half" anywhere else in the sentence is left alone. Number words are
# digits by the time this runs ("three quarters" arrives as "3 quarters").
_BEFORE_UNIT = rf"(?=\s*{_UNIT}\b)"
_FRACTION_AMOUNTS = (
    (r"\b(\d+)\s+and\s+(?:a\s+)?half\s+", lambda m: f"{int(m.group(1)) + 0.5:g} "),
    (r"\b(\d+)\s*/\s*(\d+)\s+", lambda m: f"{int(m.group(1)) / int(m.group(2)):.10g} " if int(m.group(2)) else m.group(0)),
    (r"\b(?:3\s+(?:quarters|fourths))(?:\s+of\s+an?)?\s+", lambda m: "0.75 "),
    (r"\b(?:2\s+thirds)(?:\s+of\s+an?)?\s+", lambda m: f"{2 / 3:.10g} "),
    (r"\b(?:(?:a|an|1)\s+)?half(?:\s+(?:of\s+)?an?)?\s+", lambda m: "0.5 "),
    (r"\b(?:(?:a|an|1)\s+)?(?:quarter|fourth)(?:\s+(?:of\s+)?an?)?\s+", lambda m: "0.25 "),
    (r"\b(?:(?:a|an|1)\s+)?third(?:\s+(?:of\s+)?an?)?\s+", lambda m: f"{1 / 3:.10g} "),
)


def _fraction_amounts(text: str) -> str:
    text = re.sub(rf"\ban?\s+({_UNIT})\s+and\s+a\s+half\b", r"1.5 \1", text, flags=re.IGNORECASE)
    for pattern, value in _FRACTION_AMOUNTS:
        text = re.sub(pattern + _BEFORE_UNIT, value, text, flags=re.IGNORECASE)
    return text


def _unit(word: str) -> str | None:
    return _LOOKUP.get(word.lower().strip())


# An amount said in two units - "5 feet 10 inches", "5'10\"", "2 pounds 4 ounces", "2 hours 30
# minutes" - reached a chat model, which is the wrong tool for a height. It is read as one amount
# in the smaller unit, and shown as it was said. The second unit may go unsaid: "6 foot 2".
_PAIRS = {"foot": "inch", "pound": "ounce", "stone": "pound", "hour": "minute", "minute": "second"}
_AMOUNT_ENDS = r"(?=\s+(?:to|in|into|as)\s|\s*[?.!]*\s*$)"
_COMPOUND = re.compile(
    rf"\b(?P<a>\d+)\s*(?P<big>{_UNIT})\s+(?:and\s+)?(?P<b>\d+(?:\.\d+)?)(?:\s*(?P<small>{_UNIT}))?{_AMOUNT_ENDS}",
    re.IGNORECASE,
)
_FEET_MARKS = re.compile(r"\b(\d+)\s*['’]\s*(\d+(?:\.\d+)?)\s*(?:[\"”]|'')?(?=\s|$|[?.!])")
# "to feet and inches": the answer in both units, the way a height or a baby's weight is said.
_TO_PAIR = re.compile(rf"\s+(?:to|in|into|as)\s+(?P<big>{_UNIT})\s+(?:and|&)\s+(?P<small>{_UNIT})\s*[?.!]*\s*$",
                      re.IGNORECASE)


def _compound(text: str) -> tuple[str, str, tuple[str, str] | None]:
    """The text with a two-unit amount made one, what that amount was said as, and the pair of
    units the answer should be given in, when one was asked for."""
    text = _FEET_MARKS.sub(r"\1 ft \2 in", text)
    said = ""
    match = _COMPOUND.search(text)
    big = _unit(match.group("big")) if match else None
    small = (_unit(match.group("small")) if match.group("small") else _PAIRS.get(big or "")) if match else None
    if match and big in _PAIRS and _PAIRS[big] == small:
        whole, part = int(match.group("a")), float(match.group("b"))
        total = whole * _FACTORS[big][1] / _FACTORS[small][1] + part
        said = f"{whole} {_named(big, whole)} {_shown(part)} {_named(small, part)}"
        text = text[:match.start()] + f"{total:.10g} {_SPELLINGS[small][-1]}" + text[match.end():]
    pair = None
    wanted = _TO_PAIR.search(text)
    if wanted:
        big, small = _unit(wanted.group("big")), _unit(wanted.group("small"))
        if big in _PAIRS and _PAIRS[big] == small:
            pair = (big, small)
            text = text[:wanted.start()] + f" to {_SPELLINGS[small][-1]}"
    return text, said, pair


def parse(text: str) -> tuple[float, str, str] | None:
    """(amount, from-unit, to-unit) for a conversion request, or None."""
    return _parse(text)[0]


def _parse(text: str) -> tuple[tuple[float, str, str] | None, str, tuple[str, str] | None]:
    cleaned = _fraction_amounts(_NUMBER_WORD.sub(lambda m: _words_to_number(m.group(0)), text or ""))
    cleaned, said, pair = _compound(cleaned)
    return _parse_one(cleaned), said, pair


def _parse_one(cleaned: str) -> tuple[float, str, str] | None:
    # The router hands over `convert <what was said>`, and "how many ounces in a pound" or
    # "what's 70 fahrenheit in celsius" only reads as a conversion from its own first word.
    cleaned = re.sub(r"^\s*(?:please\s+)?convert\s+(?=how\s+(?:many|much|tall|heavy)\b|what\b)", "", cleaned,
                     flags=re.IGNORECASE)
    for pattern in (_FORWARD, _HOW_MANY):
        match = pattern.match(cleaned)
        if not match:
            continue
        source, target = _unit(match.group("from")), _unit(match.group("to"))
        if source is None or target is None:
            continue
        # "how many ounces in a cup" means fluid ounces; it was refused as a mass against a
        # volume. The answer names the unit it used, so the reading is never hidden.
        if "ounce" in (source, target) and "volume" in (_FACTORS[source][0], _FACTORS[target][0]):
            source, target = ("fluid ounce" if unit == "ounce" else unit for unit in (source, target))
        # "convert 5k to miles" is a race, not five kelvin: a bare "k" against a length is km.
        if source == "kelvin" and match.group("from").strip().lower() == "k" and _FACTORS[target][0] == "length":
            source = "kilometre"
        if target == "kelvin" and match.group("to").strip().lower() == "k" and _FACTORS[source][0] == "length":
            target = "kilometre"
        amount = float((match.group("n") or "1").replace(",", "."))
        return amount, source, target
    return None


def convert(amount: float, source: str, target: str) -> float:
    dimension, factor = _FACTORS[source]
    other, target_factor = _FACTORS[target]
    if dimension != other:
        raise ValueError(f"{source} is a {dimension} and {target} is a {other}; they do not convert.")
    if dimension != "temperature":
        return amount * factor / target_factor
    celsius = {"celsius": amount, "fahrenheit": (amount - 32) * 5 / 9, "kelvin": amount - 273.15}[source]
    return {"celsius": celsius, "fahrenheit": celsius * 9 / 5 + 32, "kelvin": celsius + 273.15}[target]


def _shown(value: float) -> str:
    """Two decimals for everyday sizes ("37.78°C", "8.05 kilometres"), significant figures
    for tiny or huge ones."""
    if abs(value) >= 1e15 or (value and abs(value) < 0.01):
        return f"{value:.4g}"
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text if text not in {"-0", ""} else "0"


def _named(unit: str, amount: float) -> str:
    symbols = {"celsius": "°C", "fahrenheit": "°F", "kelvin": "K"}
    if unit in symbols:
        return symbols[unit]
    if abs(amount) == 1:
        return unit
    head, per, tail = unit.partition(" per ")    # "miles per hour", not "mile per hours"
    plural = {"foot": "feet", "inch": "inches"}.get(head, head + ("es" if head.endswith("s") else "s"))
    return plural + (f" per {tail}" if per else "")


class UnitTool:
    """`convert <amount> <unit> to <unit>` - local and exact, so no approval gate."""

    def convert(self, text: str) -> ToolResult:
        parsed, said, pair = _parse(text)
        if parsed is None:
            return ToolResult.failure(
                "I could not read that as a conversion. Try \"convert 5 miles to km\" or "
                "\"how many ounces in a pound\".")
        amount, source, target = parsed
        try:
            value = convert(amount, source, target)
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        left = said or f"{_shown(amount)} {_named(source, amount)}".replace(" °", "°").replace(" K", " K")
        right = f"{_shown(value)} {_named(target, value)}".replace(" °", "°")
        if pair is not None:
            big, small = pair
            ratio = round(_FACTORS[big][1] / _FACTORS[small][1])
            whole, rest = divmod(round(value, 2), ratio)
            parts = [f"{int(whole):,} {_named(big, whole)}"] if whole else []
            parts += [f"{_shown(rest)} {_named(small, rest)}"] if rest or not whole else []
            right = " ".join(parts)
        note = " (US measure)" if _FACTORS[source][0] == "volume" and {source, target} & {
            "cup", "pint", "quart", "gallon", "fluid ounce"} else ""
        return ToolResult.success(f"{left} = **{right}**{note}", value=value, source=source, target=target)


def looks_like_conversion(text: str) -> bool:
    return parse(text) is not None
