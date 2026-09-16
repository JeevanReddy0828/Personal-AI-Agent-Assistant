"""The J.A.R.V.I.S web interface, assembled from its asset files.

This was one 2529-line module holding a 183KB raw string: the HTML, 47KB of CSS and 121KB
of JavaScript, all as source inside triple quotes. Nothing could lint, format or
syntax-highlight any of it, and a stray backslash in a regex was indistinguishable from a
deliberate escape - a mistake that has cost real time in this repo more than once.

The three parts now live in ``webui_assets/`` as ``app.html``, ``app.css`` and ``app.js``
and are stitched back together here. The page is still served as **one inlined document**
on purpose: the CSP is ``script-src 'nonce-...'`` with no ``'self'``, so a ``<script src>``
would be blocked outright, and a second request would buy nothing. This is a source-level
split, not a served-level one - the bytes on the wire are unchanged, and the extraction
was verified byte-identical against a snapshot of the old string.

``{{PLANNER}}``, ``{{SMART}}``, ``{{ULTRA}}``, ``{{VISION}}``, ``{{NONCE}}`` and
``{{API_TOKEN}}`` are still substituted by ``webui._rendered_page()`` on the assembled
page, so it does not matter which file they sit in. Edits to any asset need a restart,
since the page is read once at import.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ASSET_DIR_NAME = "webui_assets"


def _asset_dir() -> Path:
    """Where the page's assets live, in a checkout and inside a packaged build.

    PyInstaller ``--onefile`` extracts ``--add-data`` into ``sys._MEIPASS`` and puts the
    package's own modules there too, so the ordinary lookup beside this file already
    resolves inside a bundle. The ``_MEIPASS`` branches cover a build that lands the data
    at the bundle root instead - the same trap that left the packaged app unable to find
    the Vosk model it shipped with.
    """
    beside = Path(__file__).resolve().parent / _ASSET_DIR_NAME
    if beside.is_dir():
        return beside
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        for candidate in (Path(bundled) / "laptop_agent" / _ASSET_DIR_NAME,
                          Path(bundled) / _ASSET_DIR_NAME):
            if candidate.is_dir():
                return candidate
    return beside  # so the error below names the location that was expected


def _read(name: str) -> str:
    """One asset as text.

    Universal newlines on purpose: git may check these out with CRLF, and the page has to
    assemble identically either way.
    """
    path = _asset_dir() / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"The web app cannot start: {name} is missing from {path.parent}. "
            f"A packaged build needs --add-data for {_ASSET_DIR_NAME} (see packaging/)."
        ) from exc


def build_page() -> str:
    """The complete document, assets inlined."""
    return (
        _read("app.html")
        .replace("{{STYLE}}", _read("app.css"))
        .replace("{{SCRIPT}}", _read("app.js"))
    )


PAGE = build_page()
