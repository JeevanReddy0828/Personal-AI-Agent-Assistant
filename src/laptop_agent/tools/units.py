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
    rf"^\s*(?:(?:please\s+)?convert|what(?:'s|s|\s+is)|how\s+much\s+is|how\s+many\s+\w+(?:\s+\w+)?\s+(?:is|are))?\s*"
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


def _unit(word: str) -> str | None:
    return _LOOKUP.get(word.lower().strip())


def parse(text: str) -> tuple[float, str, str] | None:
    """(amount, from-unit, to-unit) for a conversion request, or None."""
    cleaned = _NUMBER_WORD.sub(lambda m: _words_to_number(m.group(0)), text or "")
    # The router hands over `convert <what was said>`, and "how many ounces in a pound"
    # only reads as a conversion from its own first word.
    cleaned = re.sub(r"^\s*(?:please\s+)?convert\s+(?=how\s+many\b)", "", cleaned, flags=re.IGNORECASE)
    for pattern in (_FORWARD, _HOW_MANY):
        match = pattern.match(cleaned)
        if not match:
            continue
        source, target = _unit(match.group("from")), _unit(match.group("to"))
        if source is None or target is None:
            continue
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
        parsed = parse(text)
        if parsed is None:
            return ToolResult.failure(
                "I could not read that as a conversion. Try \"convert 5 miles to km\" or "
                "\"how many ounces in a pound\".")
        amount, source, target = parsed
        try:
            value = convert(amount, source, target)
        except ValueError as exc:
            return ToolResult.failure(str(exc))
        left = f"{_shown(amount)} {_named(source, amount)}".replace(" °", "°").replace(" K", " K")
        right = f"{_shown(value)} {_named(target, value)}".replace(" °", "°")
        note = " (US measure)" if _FACTORS[source][0] == "volume" and {source, target} & {
            "cup", "pint", "quart", "gallon", "fluid ounce"} else ""
        return ToolResult.success(f"{left} = **{right}**{note}", value=value, source=source, target=target)


def looks_like_conversion(text: str) -> bool:
    return parse(text) is not None
