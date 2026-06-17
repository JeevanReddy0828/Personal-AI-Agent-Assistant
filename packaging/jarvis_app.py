"""Entry point for the packaged J.A.R.V.I.S desktop application.

PyInstaller bundles this into a single windowless executable. Running it starts
the local engine and opens the frameless desktop app window (Chrome/Edge --app),
so the end user just double-clicks the app — no terminal, no browser tab.

The app still uses the system's Chrome or Edge to render its window (kept out of
the bundle to stay small and to keep the Web Speech API working for voice).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _load_env_beside_exe() -> None:
    """Load a .env that sits next to the executable, so a downloaded app can be
    configured by dropping a .env beside it (config.py also auto-loads .env)."""
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    env_path = base / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    _load_env_beside_exe()
    from laptop_agent.webui import run_desktop

    run_desktop()


if __name__ == "__main__":
    main()
