from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from laptop_agent.planner.openai_compatible import _PERMANENT_ADVICE
from laptop_agent.storage import storage_warnings
from laptop_agent.tools.websearch import _API_BACKENDS


def system_health(orchestrator: Any, llm_reachable: bool | None, config: Any) -> dict[str, object]:
    """A production self-check: is the brain, memory, and mail wired and reachable?

    Pure and side-effect free so it can be unit tested. `llm_reachable` is a cached
    result (None = not applicable / not yet checked) supplied by the caller, so the
    health endpoint never blocks on a live network ping.
    """
    provider = getattr(getattr(orchestrator, "planner", None), "provider", None)
    llm_configured = provider is not None and "OpenAI" in type(provider).__name__

    context = getattr(orchestrator, "context", None)
    obsidian = getattr(context, "obsidian", None)
    vault_connected = bool(obsidian.available()) if obsidian is not None else False

    email_configured = bool(
        getattr(config, "imap_host", None)
        and getattr(config, "imap_username", None)
        and getattr(config, "imap_password", None)
    )

    if not llm_configured:
        overall = "setup"  # no AI connected — first-run state
    elif llm_reachable is False:
        overall = "degraded"  # configured but the endpoint is unreachable
    elif llm_reachable is None:
        overall = "checking"
    else:
        overall = "ok"

    # Per-tier reachability from real chat turns (fast/smart/ultra), so the UI can
    # show "the advanced model is busy" even while the fast tier is healthy.
    status = getattr(orchestrator, "model_status", None)
    tier_status = (status.snapshot() if status is not None
                   else {"tiers": {}, "degraded": False, "broken": [], "reasons": {}})

    return {
        "overall": overall,
        "storage_warnings": storage_warnings(),
        "llm": {
            "configured": llm_configured,
            "reachable": llm_reachable,
            "provider": "openai-compatible" if llm_configured else "heuristic",
            "tiers": tier_status["tiers"],
            "degraded_tier": tier_status["degraded"],
            # A busy tier is worth waiting for; a broken one never recovers on its own, so
            # it is named separately along with what to change. Reported as "busy", a
            # retired model id had people waiting for an endpoint that was never coming
            # back - see ERRORS.md, twice.
            "broken_tiers": tier_status.get("broken", []),
            "tier_reasons": tier_status.get("reasons", {}),
        },
        # Health is served without a chat token, so expose connection state only;
        # model identifiers and local vault paths belong in local configuration.
        "vault": {"connected": vault_connected},
        "email": {"configured": email_configured},
        "search": {
            "provider": getattr(config, "search_provider", "") or "duckduckgo",
            "api_key": bool(getattr(config, "search_api_key", None)),
        },
        "smart_planner": getattr(orchestrator, "smart_planner", None) is not None,
        "vision_planner": getattr(orchestrator, "vision_planner", None) is not None,
    }


# What each row that is not ready says to do. Environment variable names and install commands
# only: never a value, a path or a model id, since this is shown in the page.
_NEXT = {
    "chat": "Set LAPTOP_AGENT_LLM_PROVIDER=openai-compatible and OPENAI_API_KEY in .env (with OPENAI_BASE_URL "
            "and OPENAI_MODEL for your provider), then restart.",
    "deeper": "Set OPENAI_SMART_MODEL and OPENAI_ULTRA_MODEL for harder questions.",
    "vision": "Set OPENAI_VISION_MODEL to describe pictures and the screen.",
    "pictures": "Set OPENAI_IMAGE_KEY (or OPENAI_API_KEY) to draw pictures.",
    "stt": "pip install laptop-agent[stt] and put a Vosk model in models/ (light), or "
           "pip install laptop-agent[transcribe] for Whisper.",
    "tts": "pip install laptop-agent[voice] for an offline voice in the app window, or "
           "laptop-agent[riva] with OPENAI_API_KEY for NVIDIA's hosted one.",
    "ocr": "pip install laptop-agent[ocr] and install Tesseract, or set OPENAI_API_KEY for hosted parsing.",
    "docs": "pip install laptop-agent[docs]",
    "browser": "pip install laptop-agent[browser], then python -m playwright install chromium",
    "youtube": "pip install laptop-agent[youtube]",
    "email": "Set IMAP_HOST, IMAP_USERNAME and IMAP_PASSWORD (for Gmail, an app password) in .env, "
             "and SMTP_HOST, SMTP_USERNAME and SMTP_PASSWORD to send.",
    "vault": "Set OBSIDIAN_VAULT to your vault's folder.",
    "metrics": "pip install laptop-agent[metrics]",
    "window": "pip install laptop-agent[app] (optional: the browser tab works without it).",
    "sign_in": "Set up an owner account in the settings popover.",
    "lan": "Set LAPTOP_AGENT_HOST=0.0.0.0 with sign-in on (or LAPTOP_AGENT_LAN_PASSCODE) to use it "
           "from a phone on the same network.",
    "backup": "Set OPENROUTER_API_KEY for a backup when the main models are busy (optional).",
}
_STT = {"riva:parakeet": "NVIDIA Parakeet, hosted, with a local engine behind it",
        "vosk": "Vosk, on this computer", "whisper": "Whisper, on this computer"}
_TTS = {"riva:magpie": "NVIDIA Magpie, hosted",
        "pyttsx3": "An offline voice on this computer"}
_OCR = {"nemotron-parse": "NVIDIA parse, hosted, with Tesseract behind it", "tesseract": "Tesseract, on this computer"}


def _row(key: str, name: str, state: str, detail: str, next_step: str | None = None) -> dict[str, object]:
    # Set-up advice is for what is off or missing. Busy means wait, and a broken tier says why.
    if next_step is None and state in ("off", "missing"):
        next_step = _NEXT.get(key)
    return {"key": key, "name": name, "state": state, "detail": detail, "next": next_step}


def _broken_advice(reason: str, variable: str) -> str | None:
    """What to change for a misconfigured tier, rebuilt from the HTTP status alone: the stored
    reason names the model id (`classify_failure`), and this report never shows one."""
    found = re.search(r"HTTP (\d{3})\b", reason)
    status = int(found[1]) if found else None
    if status not in _PERMANENT_ADVICE:
        return None
    fix = {401: "OPENAI_API_KEY", 403: f"OPENAI_API_KEY and {variable}"}.get(status, variable)
    return f"{_PERMANENT_ADVICE[status]} (HTTP {status}): check {fix}."


def _playwright_browsers(package: Path) -> Path:
    """Where Playwright keeps its browsers, read the way Playwright reads it."""
    chosen = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if chosen == "0":
        return package / "driver" / "package" / ".local-browsers"
    if chosen:
        return Path(chosen)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"


def chromium_installed(package: Path | None = None, browsers: Path | None = None) -> bool:
    """Whether the Chromium the installed Playwright expects is on disk, offline: the revision
    its own browsers.json names, looked for in its browsers directory. The package alone is
    not enough (Codex's review of #144), and neither is any Chromium: an upgrade leaves the
    old revision behind, and Playwright will not launch it. Nor is the revision's folder: an
    interrupted install leaves it empty, so a revision counts only with Playwright's own
    `INSTALLATION_COMPLETE` marker and a browser executable inside (Codex's re-review)."""
    try:
        if package is None:
            spec = importlib.util.find_spec("playwright")
            if spec is None or not spec.origin:
                return False
            package = Path(spec.origin).parent
        listed = json.loads((package / "driver" / "package" / "browsers.json").read_text(encoding="utf-8"))
        wanted = [f"{entry['name'].replace('-', '_')}-{entry['revision']}" for entry in listed["browsers"]
                  if entry.get("name") in ("chromium", "chromium-headless-shell")]
    except (ImportError, OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    browsers = browsers or _playwright_browsers(package)
    return any(_installed(browsers / name) for name in wanted)


# What a finished Chromium download holds one folder down (chrome-win64, chrome-linux,
# chrome-headless-shell-win64, ...): named per platform, so matched by name, not by path.
_CHROMIUM_EXECUTABLES = {"chrome.exe", "chrome", "chrome-headless-shell.exe", "chrome-headless-shell",
                         "headless_shell.exe", "headless_shell"}


def _installed(revision: Path) -> bool:
    if not (revision / "INSTALLATION_COMPLETE").is_file():
        return False
    return any((entry.is_file() and entry.name.lower() in _CHROMIUM_EXECUTABLES)
               or (entry.is_dir() and entry.suffix == ".app") for entry in revision.glob("*/*"))


def setup_report(orchestrator: Any, config: Any, *, llm_reachable: bool | None, stt_engine: str | None,
                 tts_engine: str | None, ocr_engine: str | None, sign_in: bool, lan_mode: bool, find_spec=None,
                 which=None, browser_engine=None) -> list[dict[str, object]]:
    """Each capability, whether it is ready and what to do if not. States: `ready`, `off`
    (optional and not set up), `missing` (a package or engine it needs is absent), `busy` (a
    model tier is loaded or unreachable) and `broken` (a tier is misconfigured, with why).

    Offline and cheap: packages are looked up with `find_spec`, programs with `which` and
    Playwright's Chromium with `browser_engine`, all injectable, so nothing heavy is imported
    and nothing goes over the network."""
    import shutil

    find_spec = find_spec or importlib.util.find_spec
    which = which or shutil.which
    browser_engine = browser_engine or chromium_installed

    def has(module: str) -> bool:
        try:
            return find_spec(module) is not None
        except (ImportError, ValueError):
            return False

    status = getattr(orchestrator, "model_status", None)
    snapshot = status.snapshot() if status is not None else {"tiers": {}, "broken": [], "reasons": {}}

    def tier(name: str) -> str:
        if name in snapshot.get("broken", []):
            return "broken"
        return "busy" if snapshot.get("tiers", {}).get(name) not in (None, "ok") else "ready"

    rows: list[dict[str, object]] = []
    provider = getattr(getattr(orchestrator, "planner", None), "provider", None)
    if provider is None or "OpenAI" not in type(provider).__name__:
        rows.append(_row("chat", "Chat model", "off", "Replies come from built-in rules only."))
    elif llm_reachable is False and tier("fast") != "broken":
        rows.append(_row("chat", "Chat model", "busy", "Configured, but the endpoint is not answering.",
                         "Check OPENAI_BASE_URL and the network."))
    else:
        state = tier("fast")
        reason = str(snapshot.get("reasons", {}).get("fast") or "")
        detail = {"ready": "Answering.", "busy": "Busy at the moment; replies fall back.",
                  "broken": "Misconfigured: this will not recover by waiting."}[state]
        rows.append(_row("chat", "Chat model", state, detail,
                         (_broken_advice(reason, "OPENAI_MODEL") or "Check OPENAI_API_KEY and OPENAI_MODEL.")
                         if state == "broken" else None))

    deeper = [(label, name) for label, attr, name in (("balanced", "smart_planner", "smart"),
                                                      ("deep", "ultra_planner", "ultra"))
              if getattr(orchestrator, attr, None) is not None]
    if not deeper:
        rows.append(_row("deeper", "Deeper models", "off", "Harder questions use the chat model."))
    else:
        states = {name: tier(name) for _, name in deeper}
        state = "broken" if "broken" in states.values() else "busy" if "busy" in states.values() else "ready"
        named = " and ".join(label for label, _ in deeper).capitalize()
        variables = {"smart": "OPENAI_SMART_MODEL", "ultra": "OPENAI_ULTRA_MODEL"}
        advice = "; ".join(_broken_advice(str(snapshot.get("reasons", {}).get(name) or ""), variables[name])
                           or f"check OPENAI_API_KEY and {variables[name]}."
                           for name, s in states.items() if s == "broken")
        rows.append(_row("deeper", "Deeper models", state, f"{named} configured." if state == "ready"
                         else f"{named} configured; one is {state}.", advice or None))

    vision = getattr(orchestrator, "vision_planner", None) is not None
    rows.append(_row("vision", "Vision", "ready" if vision else "off",
                     "Describes pictures and the screen." if vision else "Pictures and the screen are not described."))
    image_key = bool(getattr(config, "llm_image_api_key", None))
    rows.append(_row("pictures", "Pictures", "ready" if image_key else "off",
                     "Draws pictures from a description." if image_key else "Drawing is not set up."))

    # Named only from the providers the backend knows: the setting's own text is never shown, since
    # a key pasted into SEARCH_PROVIDER would otherwise be printed in the page.
    chosen = (getattr(config, "search_provider", "") or "").strip().lower()
    key = bool(getattr(config, "search_api_key", None))
    known = ", ".join(sorted(_API_BACKENDS))
    if chosen in _API_BACKENDS and key:
        rows.append(_row("search", "Web search", "ready", f"{chosen.title()}, with DuckDuckGo behind it."))
    elif chosen in _API_BACKENDS:
        rows.append(_row("search", "Web search", "ready", "DuckDuckGo, because the chosen provider has no key.",
                         "Set SEARCH_API_KEY for the chosen provider."))
    elif chosen and chosen != "duckduckgo":
        rows.append(_row("search", "Web search", "ready", "DuckDuckGo: SEARCH_PROVIDER names no provider it knows.",
                         f"Set SEARCH_PROVIDER to one of {known}."))
    elif key and not chosen:
        rows.append(_row("search", "Web search", "ready", "DuckDuckGo: a search key is set but no provider.",
                         f"Set SEARCH_PROVIDER to one of {known}."))
    else:
        rows.append(_row("search", "Web search", "ready", "DuckDuckGo, no key needed."))

    rows.append(_row("stt", "Speech to text", "ready" if stt_engine else "missing",
                     _STT.get(stt_engine or "", "No engine here: voice uses the browser's own recognizer.")))
    voice = _TTS.get(tts_engine or "", "No voice here: the browser tab still speaks.")
    if tts_engine == "riva:magpie" and has("pyttsx3"):
        voice += ", with the offline voice behind it"
    rows.append(_row("tts", "Spoken replies in the app window", "ready" if tts_engine else "missing", voice))
    if ocr_engine == "tesseract" and not which("tesseract"):
        rows.append(_row("ocr", "Reading text in images", "missing", "The Tesseract program is not installed.",
                         "Install Tesseract and put it on PATH."))
    else:
        rows.append(_row("ocr", "Reading text in images", "ready" if ocr_engine else "missing",
                         _OCR.get(ocr_engine or "", "No engine.")))

    formats = {"Word": has("docx"), "PowerPoint": has("pptx"), "PDF reading": has("pdfplumber") or has("pypdf")}
    rows.append(_row("docs", "Documents", "ready" if all(formats.values()) else "missing",
                     ", ".join(f"{name}: {'yes' if ok else 'no'}" for name, ok in formats.items()) + "."))
    browser = has("playwright")
    engine = browser and browser_engine()
    rows.append(_row("browser", "Browser and PDF export", "ready" if engine else "missing",
                     "Reads pages and writes PDFs." if engine else
                     "Playwright is installed but not the Chromium it uses." if browser else
                     "Page reading and PDF export are off.",
                     "python -m playwright install chromium" if browser and not engine else None))
    youtube = has("youtube_transcript_api")
    rows.append(_row("youtube", "YouTube summaries", "ready" if youtube else "missing",
                     "Summarises a video from its transcript." if youtube else "Videos cannot be summarised."))

    reads = all(getattr(config, key, None) for key in ("imap_host", "imap_username", "imap_password"))
    sends = all(getattr(config, key, None) for key in ("smtp_host", "smtp_username", "smtp_password"))
    rows.append(_row("email", "Email", "ready" if reads and sends else "off",
                     "Reads and sends." if reads and sends else "Reads only." if reads else
                     "Sends only." if sends else "Not connected."))
    obsidian = getattr(getattr(orchestrator, "context", None), "obsidian", None)
    vault = bool(obsidian.available()) if obsidian is not None else False
    rows.append(_row("vault", "Notes vault", "ready" if vault else "off", "Connected." if vault else "No vault connected."))
    # Without psutil, metrics.py reads Windows' own counters through PowerShell.
    metrics, counters = has("psutil"), bool(which("powershell"))
    rows.append(_row("metrics", "Usage meters", "ready" if metrics or counters else "missing",
                     "CPU and memory in this drawer." if metrics else
                     "CPU and memory from Windows' own counters." if counters else "This drawer shows no usage."))
    window = has("webview")
    rows.append(_row("window", "Desktop window", "ready" if window else "off",
                     "Opens as its own window." if window else "Opens in a browser tab."))
    rows.append(_row("sign_in", "Sign-in", "ready" if sign_in else "off",
                     "On: everyone signs in." if sign_in else "Off: anyone who opens the app here can use it."))
    rows.append(_row("lan", "Phone access", "ready" if lan_mode else "off",
                     "On: devices on this network can reach it." if lan_mode else "This computer only."))
    backup = bool(getattr(config, "openrouter_api_key", None))
    rows.append(_row("backup", "Backup model", "ready" if backup else "off",
                     "Answers when the main models are busy." if backup else "No backup provider."))
    return rows
