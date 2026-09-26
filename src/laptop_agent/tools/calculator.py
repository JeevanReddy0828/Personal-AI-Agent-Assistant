"""Exact arithmetic, because a language model is the wrong tool for it.

Reported: `solve - 67458363*37834872` produced a decision framework and never reached a
number. The right answer is 2552278529434536, and no amount of prompting makes token
prediction reliable at 8-digit multiplication. This evaluates the expression instead.

Parsing is a hand-written recursive-descent parser over a tokenizer, not `eval`: `eval`
on user text is arbitrary code execution, and `ast.literal_eval` cannot do operators.
Integers stay exact (Python ints are arbitrary precision) and division falls back to
Fraction so `1/3*3` is 1 rather than 0.9999999999999998.
"""

from __future__ import annotations

import math
import re
from fractions import Fraction

from laptop_agent.tools.base import ToolResult

# Word forms people actually dictate. Voice input arrives as words, not symbols.
# "to the power of" is an operator, not "**2": it was rewritten to "**2", so "2 to the
# power of 10" became "2 **2 10" and failed with "Unexpected '10' at the end".
_WORDS = (
    (r"\bdivided by\b|\bover\b", "/"),
    (r"\bmultiplied by\b|\btimes\b", "*"),
    (r"\bplus\b|\badded to\b", "+"),
    (r"\bminus\b|\bsubtract\b", "-"),
    (r"\bto the (\d+)(?:st|nd|rd|th)(?: power)?\b", r"**\1"),
    (r"\b(?:to the power of|raised to(?: the power of)?)\b", "**"),
    (r"\bsquared\b", "**2"),
    (r"\bcubed\b", "**3"),
    (r"\bmod(?:ulo)?\b", "%"),
)

# Everyday money and percentage phrasings, rewritten into plain arithmetic before parsing.
# A bare "%" stays modulo ("10 % 3" is 1); only these shapes mean a percentage.
_NUMBER = r"(\d+(?:\.\d+)?)"
_PHRASES = (
    # "15% of 80", "15 percent of 80", "a 20% tip on 45", "20% tip for 45"
    (rf"{_NUMBER}\s*(?:%|percent|per\s+cent)\s*(?:tip\s+)?(?:of|on|for)\s+{_NUMBER}", r"(\1/100*\2)"),
    # "25% off 80" -> the discounted price
    (rf"{_NUMBER}\s*(?:%|percent|per\s+cent)\s*off\s+{_NUMBER}", r"(\2*(1-\1/100))"),
    # "square root of 144", "the square root of 2", "sqrt 2", "sqrt of 2"
    (rf"\b(?:the\s+)?square\s+root\s+of\s+{_NUMBER}", r"sqrt(\1)"),
    (rf"\bsqrt\s+(?:of\s+)?{_NUMBER}", r"sqrt(\1)"),
    # "split 120 between 4 people", "divide 90 among 3", "120 split 4 ways"
    (rf"\b(?:split|divide|share)\s+{_NUMBER}\s+(?:between|among|amongst|by|into|with)\s+{_NUMBER}"
     r"(?:\s+(?:people|persons|friends|ways|of us))?", r"(\1/\2)"),
    (rf"{_NUMBER}\s+split\s+{_NUMBER}\s+ways", r"(\1/\2)"),
)

# Dictated numbers: "five plus five", "twelve times twelve", "one hundred and twenty".
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_ANY_NUMBER_WORD = "|".join(sorted(list(_UNITS) + list(_SCALES), key=len, reverse=True))
# "and" joins only after a scale word ("one hundred and five"); "two and three" is two
# numbers, not five.
_NUMBER_WORD = re.compile(
    rf"\b(?:{_ANY_NUMBER_WORD})"
    rf"(?:(?:(?:(?<=hundred)|(?<=thousand)|(?<=million)|(?<=billion))\s+and\s+|[\s-]+)"
    rf"(?:{_ANY_NUMBER_WORD}))*\b",
    re.IGNORECASE,
)


def _words_to_number(words: str) -> str:
    total, current = 0, 0
    for word in re.split(r"[\s-]+", words.lower()):
        if word == "and":
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        else:
            total += (current or 1) * _SCALES[word]
            current = 0
    return str(total + current)

_TOKEN = re.compile(
    r"\s*(?:(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<name>[A-Za-z_][A-Za-z_0-9]*)"
    r"|(?P<op>\*\*|//|[-+*/%()^,]))"
)

_FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "min": min, "max": max,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
# About 300,000 digits: computes in milliseconds, prints in scientific form.
_MAX_RESULT_BITS = 1_000_000

# An expression is arithmetic only if it has an operator and no stray words. "how much is
# 2+2" is arithmetic; "should I use 2 or 3 replicas" is not, and must not be hijacked.
_LOOKS_ARITHMETIC = re.compile(r"^[\d\s.,()+\-*/%^eE]+$")


class CalculatorError(ValueError):
    """The text is not an expression this can evaluate."""


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        match = _TOKEN.match(text, position)
        if not match:
            raise CalculatorError(f"I cannot read {text[position]!r} as part of a sum.")
        position = match.end()
        for kind in ("number", "name", "op"):
            value = match.group(kind)
            if value is not None:
                tokens.append((kind, value))
                break
    return tokens


class _Parser:
    """expression := term (('+'|'-') term)*
    term       := unary (('*'|'/'|'//'|'%') unary)*
    unary      := ('+'|'-') unary | power
    power      := atom ('**' unary)?       -- right associative

    atom       := number | name | name '(' args ')' | '(' expression ')'

    Unary minus binds *looser* than exponentiation, so -2**2 is -(2**2) = -4, not
    (-2)**2 = 4. Putting unary above power rather than inside it is what makes that
    come out right, and it also lets the exponent itself be signed: 2**-3.
    """

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.index = 0

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise CalculatorError("The expression ends before it is finished.")
        self.index += 1
        return token

    def expect(self, value: str) -> None:
        token = self.take()
        if token[1] != value:
            raise CalculatorError(f"Expected {value!r} but found {token[1]!r}.")

    def parse(self):
        value = self.expression()
        if self.peek() is not None:
            raise CalculatorError(f"Unexpected {self.peek()[1]!r} at the end.")
        return value

    def expression(self):
        value = self.term()
        while (token := self.peek()) and token[1] in ("+", "-"):
            self.take()
            right = self.term()
            value = value + right if token[1] == "+" else value - right
        return value

    def term(self):
        value = self.unary()
        while (token := self.peek()) and token[1] in ("*", "/", "//", "%"):
            self.take()
            right = self.unary()
            if token[1] == "*":
                value = value * right
            elif token[1] == "/":
                if right == 0:
                    raise CalculatorError("That divides by zero.")
                # Fraction keeps division exact; float only when something already is.
                if isinstance(value, float) or isinstance(right, float):
                    value = value / right
                else:
                    value = Fraction(value) / Fraction(right)
            elif token[1] == "//":
                if right == 0:
                    raise CalculatorError("That divides by zero.")
                value = value // right
            else:
                if right == 0:
                    raise CalculatorError("That divides by zero.")
                value = value % right
        return value

    def unary(self):
        token = self.peek()
        if token and token[1] in ("+", "-"):
            self.take()
            value = self.unary()
            return value if token[1] == "+" else -value
        return self.power()

    def power(self):
        base = self.atom()
        token = self.peek()
        if token and token[1] in ("**", "^"):
            self.take()
            exponent = self.unary()
            if isinstance(exponent, (int, Fraction)) and abs(exponent) > 4096:
                raise CalculatorError("That exponent is too large to compute safely.")
            # The exponent cap alone does not bound the work: (123456789**4096)**4096 has a
            # small exponent at each step and ran for over 100 seconds, holding a worker
            # thread and a CPU. Estimate the size of the answer before building it.
            if isinstance(exponent, int) and isinstance(base, (int, Fraction)):
                size = (base.numerator.bit_length() + base.denominator.bit_length()
                        if isinstance(base, Fraction) else base.bit_length())
                if abs(exponent) * size > _MAX_RESULT_BITS:
                    raise CalculatorError("That result is too large to compute safely.")
            return base ** exponent
        return base

    def atom(self):
        kind, value = self.take()
        if kind == "number":
            if "." in value or "e" in value.lower():
                return float(value)
            return int(value)
        if kind == "name":
            lowered = value.lower()
            if lowered in _CONSTANTS:
                return _CONSTANTS[lowered]
            if lowered in _FUNCTIONS:
                self.expect("(")
                args = [self.expression()]
                while (token := self.peek()) and token[1] == ",":
                    self.take()
                    args.append(self.expression())
                self.expect(")")
                try:
                    return _FUNCTIONS[lowered](*(float(a) if isinstance(a, Fraction) else a
                                                 for a in args))
                except (ValueError, TypeError, OverflowError) as exc:
                    raise CalculatorError(f"{lowered}() could not be computed: {exc}") from exc
            raise CalculatorError(f"I do not know what {value!r} means in a sum.")
        if value == "(":
            inner = self.expression()
            self.expect(")")
            return inner
        raise CalculatorError(f"Unexpected {value!r}.")


def normalize(text: str) -> str:
    """Turn dictated words into operators, and strip a leading question."""
    cleaned = (text or "").strip().rstrip("?=.!")
    cleaned = re.sub(
        r"^\s*(?:what(?:'s| is)|whats|how much is|calculate|compute|work out|evaluate|solve)\s+"
        r"(?:(?:a|an|the)\s+(?=\d|[$€£₹]))?",
        "", cleaned, flags=re.IGNORECASE,
    )
    # Money is arithmetic too: "$45", "120 dollars".
    cleaned = re.sub(r"[$€£₹]\s*(?=\d)", "", cleaned)
    cleaned = re.sub(r"(?<=\d)\s*(?:dollars?|bucks|euros?|rupees?)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\ba\s+(?=(?:hundred|thousand|million|billion)\b)", "one ", cleaned, flags=re.IGNORECASE)
    cleaned = _NUMBER_WORD.sub(lambda m: _words_to_number(m.group(0)), cleaned)
    for pattern, replacement in _PHRASES:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    for pattern, replacement in _WORDS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("×", "*").replace("÷", "/").replace("−", "-")
    cleaned = re.sub(r"(?<=\d)\s*[xX]\s*(?=\d)", "*", cleaned)   # "12 x 13"
    # 1,234,567 is one number; a comma inside digits is a separator, not an argument.
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", cleaned)
    return cleaned.strip()


def looks_like_arithmetic(text: str) -> bool:
    """Whether this is a sum rather than a question that happens to contain numbers.

    Deliberately strict: it must reduce to digits and operators, and contain an operator
    (a square root counts as one). "should I use 2 or 3 replicas" must keep going to the
    advisor.
    """
    cleaned = normalize(text)
    probe = cleaned.replace("sqrt(", "(")
    if not probe or not _LOOKS_ARITHMETIC.match(probe):
        return False
    operated = bool(re.search(r"[+\-*/%^]", probe)) or "sqrt(" in cleaned
    return operated and bool(re.search(r"\d", probe))


def _int_text(number: int) -> str:
    """Digits with separators, or scientific form past Python's int-to-text limit.

    Formatting a 5,000-digit integer raises ValueError (the interpreter refuses to print
    more than 4,300 digits), and it used to escape from here and end the whole turn.
    """
    try:
        return f"{number:,}"
    except ValueError:
        exponent = math.floor(math.log10(abs(number)))
        mantissa = 10 ** (math.log10(abs(number)) - exponent)
        sign = "-" if number < 0 else ""
        return f"{sign}{mantissa:.6f}e+{exponent} (a {exponent + 1:,}-digit number)"


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (OverflowError, ValueError):
        return None


def _present(value) -> str:
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return _int_text(value.numerator)
        exact = f"{_int_text(value.numerator)}/{_int_text(value.denominator)}"
        approx = _as_float(value)
        return f"{approx:.10g} (exactly {exact})" if approx is not None else exact
    if isinstance(value, int):
        return _int_text(value)
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:.10g}"
    return str(value)


def evaluate(expression: str):
    """The value of an expression, exactly. Raises CalculatorError on anything else."""
    cleaned = normalize(expression)
    if not cleaned:
        raise CalculatorError("There is nothing to calculate.")
    if len(cleaned) > 500:
        raise CalculatorError("That expression is too long.")
    return _Parser(_tokenize(cleaned)).parse()


class CalculatorTool:
    """`calculate <expression>` — read-only and local, so no approval gate."""

    def compute(self, expression: str) -> ToolResult:
        try:
            value = evaluate(expression)
        except CalculatorError as exc:
            return ToolResult.failure(str(exc), expression=expression)
        except (ArithmeticError, RecursionError, MemoryError) as exc:
            return ToolResult.failure(f"That could not be computed: {exc}", expression=expression)
        shown = _present(value)
        return ToolResult.success(
            f"{normalize(expression)} = **{shown}**",
            expression=normalize(expression),
            result=shown,
            value=_as_float(value) if isinstance(value, (int, float, Fraction)) else None,
        )
