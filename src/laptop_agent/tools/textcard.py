"""Render exact text as a real image.

A diffusion model cannot spell. Asked for "an image with the current time on it", FLUX
produces something clock-shaped with invented glyphs - the same failure that once turned a
resolved referent into "a picture of unreadable text". When the whole point of the image
IS the text, the text has to be drawn, not dreamt.

So this draws it: Pillow onto a card in the app's own palette, with a real system font.
Pillow ships with the `ocr` extra; without it the tool says so and suggests asking for the
text instead, which is the honest failure rather than a picture of nonsense.
"""

from __future__ import annotations

import re
from pathlib import Path

from laptop_agent.tools.base import ToolResult

# The app's dark palette, so a generated card looks like it belongs to the app.
BACKGROUND = (11, 16, 25)
PANEL = (18, 25, 38)
ACCENT = (98, 211, 234)
TEXT = (231, 237, 243)
MUTED = (135, 147, 163)

WIDTH, HEIGHT = 1200, 630

# Fonts worth trying, best first. A card is mostly large numerals, so a clean sans wins.
_FONT_CANDIDATES = (
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)

# An image request whose subject is text to display, not a scene to imagine. The model
# cannot render glyphs, so these never reach it.
_WANTS_TEXT = re.compile(
    r"\b(?:current\s+(?:time|date)|time\s+(?:and|&)\s+date|date\s+(?:and|&)\s+time"
    r"|the\s+time|the\s+date|today'?s\s+date|a?\s*clock\s+(?:showing|with|display)"
    r"|digital\s+clock|timestamp)\b",
    re.IGNORECASE,
)


def wants_text_rendered(subject: str) -> bool:
    """Whether the point of this image is exact text rather than a scene."""
    return bool(_WANTS_TEXT.search(subject or ""))


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    # Pillow's built-in is a small bitmap face; a card is better than no card.
    return ImageFont.load_default()


def render_card(lines: list[str], destination: Path, footer: str = "") -> ToolResult:
    """Draw `lines` onto a card at `destination`. First line is the headline."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return ToolResult.failure(
            "Drawing exact text into an image needs Pillow: pip install pillow "
            "(or install this app's 'ocr' extra). Ask me for the text itself and I can "
            "give you that now."
        )
    if not lines:
        return ToolResult.failure("There is nothing to put on the card.")

    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)

    # A panel inset from the edge, with one accent rule - the same restraint the app's
    # own surfaces use, so this does not look like a stock template.
    margin = 56
    draw.rounded_rectangle(
        [margin, margin, WIDTH - margin, HEIGHT - margin], radius=28, fill=PANEL
    )
    draw.rectangle([margin, margin + 26, margin + 5, HEIGHT - margin - 26], fill=ACCENT)

    headline = _load_font(118)
    secondary = _load_font(46)
    small = _load_font(28)

    x = margin + 58
    y = margin + 88
    draw.text((x, y), lines[0], font=headline, fill=TEXT)
    y += 150
    for line in lines[1:4]:
        draw.text((x, y), line, font=secondary, fill=MUTED)
        y += 64
    if footer:
        draw.text((x, HEIGHT - margin - 62), footer, font=small, fill=MUTED)

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG", optimize=True)
    return ToolResult.success(
        f"Rendered a card to {destination.name}.",
        path=str(destination),
        width=WIDTH,
        height=HEIGHT,
        lines=list(lines),
    )
