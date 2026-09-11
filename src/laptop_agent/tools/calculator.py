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
_WORDS = (
    (r"\bdivided by\b|\bover\b", "/"),
    (r"\bmultiplied by\b|\btimes\b", "*"),
    (r"\bplus\b|\badded to\b", "+"),
    (r"\bminus\b|\bsubtract\b", "-"),
    (r"\bto the power of\b|\braised to\b|\bsquared\b", "**2" ),
    (r"\bpercent of\b", "% of "),
    (r"\bmod(?:ulo)?\b", "%%"),
)

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
    cleaned = (text or "").strip().rstrip("?=.")
    cleaned = re.sub(
        r"^\s*(?:what(?:'s| is)|whats|how much is|calculate|compute|work out|evaluate|solve)\s+",
        "", cleaned, flags=re.IGNORECASE,
    )
    for pattern, replacement in _WORDS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("×", "*").replace("÷", "/").replace("−", "-")
    # 1,234,567 is one number; a comma inside digits is a separator, not an argument.
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", cleaned)
    return cleaned.strip()


def looks_like_arithmetic(text: str) -> bool:
    """Whether this is a sum rather than a question that happens to contain numbers.

    Deliberately strict: it must reduce to digits and operators, and contain an operator.
    "should I use 2 or 3 replicas" must keep going to the advisor.
    """
    cleaned = normalize(text)
    if not cleaned or not _LOOKS_ARITHMETIC.match(cleaned):
        return False
    return bool(re.search(r"[+\-*/%^]", cleaned)) and bool(re.search(r"\d", cleaned))


def _present(value) -> str:
    if isinstance(value, Fraction):
        if value.denominator == 1:
            return f"{value.numerator:,}"
        exact = f"{value.numerator:,}/{value.denominator:,}"
        return f"{float(value):.10g} (exactly {exact})"
    if isinstance(value, int):
        return f"{value:,}"
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
            value=float(value) if isinstance(value, (int, float, Fraction)) else None,
        )
