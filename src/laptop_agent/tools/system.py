"""The laptop's own controls: lock, sleep, shut down, brightness, wallpaper, processes, Settings pages.

"lock my computer", "turn the brightness down" and "set my wallpaper to …" had no tool: the model
router reached for the nearest one, and "set my wallpaper to a beach photo" generated a picture
(docs/design/routing.md). Each control here is one Windows call with no extra package (ctypes, or
a fixed built-in command, never a shell line built from the words), behind a backend the tests
replace, so no test locks, dims or shuts down this laptop. Each keeps the risk its effect has:
locking and brightness run, sleep and the wallpaper ask, shutting down and ending a process ask
as CRITICAL.
"""
from __future__ import annotations

import csv
import ctypes
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from laptop_agent.safety import ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.storage import atomic_write_text, read_json
from laptop_agent.tools.base import ToolResult

IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
SHUTDOWN_GRACE_SECONDS = 60
# Ending any of these takes Windows down with it, or signs the user out.
PROTECTED = {"system", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
             "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "registry", "memory compression"}
SETTINGS_PAGES = {
    "display": "display", "screen": "display", "brightness": "display", "sound": "sound", "audio": "sound",
    "bluetooth": "bluetooth", "wifi": "network-wifi", "wi-fi": "network-wifi", "network": "network",
    "internet": "network", "battery": "batterysaver", "power": "powersleep", "update": "windowsupdate",
    "updates": "windowsupdate", "windows update": "windowsupdate", "apps": "appsfeatures",
    "privacy": "privacy", "notifications": "notifications", "wallpaper": "personalization-background",
    "background": "personalization-background", "personalization": "personalization", "storage": "storagesense",
    "mouse": "mousetouchpad", "touchpad": "devices-touchpad", "keyboard": "keyboard", "language": "regionlanguage",
    "time": "dateandtime", "date": "dateandtime", "accounts": "yourinfo", "default apps": "defaultapps",
    "printers": "printers", "vpn": "network-vpn", "night light": "nightlight",
}


class SystemBackend(Protocol):
    def lock(self) -> None: ...
    def sleep(self) -> None: ...
    def power(self, action: str) -> None: ...
    def brightness(self) -> int | None: ...
    def set_brightness(self, percent: int) -> None: ...
    def wallpaper(self) -> str: ...
    def set_wallpaper(self, path: str) -> None: ...
    def processes(self) -> list[dict[str, object]]: ...
    def end_process(self, name: str) -> int: ...
    def open_uri(self, uri: str) -> None: ...


def _run(args: list[str], timeout: float = 15) -> str:
    done = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode != 0:
        raise OSError((done.stderr or done.stdout or f"{args[0]} failed").strip()[:300])
    return done.stdout


class WindowsSystem:
    _SPI_GETDESKWALLPAPER = 0x0073
    _SPI_SETDESKWALLPAPER = 0x0014
    _SPIF_UPDATE = 0x01 | 0x02

    def lock(self) -> None:
        if not ctypes.windll.user32.LockWorkStation():
            raise OSError("Windows refused to lock.")

    def sleep(self) -> None:
        # SetSuspendState(hibernate=False, force=False, wakeupEventsDisabled=False)
        if not ctypes.WinDLL("powrprof").SetSuspendState(False, False, False):
            raise OSError("Windows refused to sleep.")

    def power(self, action: str) -> None:
        flags = {"shutdown": ["/s", "/t", str(SHUTDOWN_GRACE_SECONDS)],
                 "restart": ["/r", "/t", str(SHUTDOWN_GRACE_SECONDS)], "cancel": ["/a"]}[action]
        _run(["shutdown", *flags])

    def brightness(self) -> int | None:
        out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                    "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop)"
                    ".CurrentBrightness | Select-Object -First 1"])
        value = out.strip()
        return int(value) if value.isdigit() else None

    def set_brightness(self, percent: int) -> None:
        _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
              "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | "
              f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1;Brightness={int(percent)}}}"])

    def wallpaper(self) -> str:
        buffer = ctypes.create_unicode_buffer(520)
        ctypes.windll.user32.SystemParametersInfoW(self._SPI_GETDESKWALLPAPER, len(buffer), buffer, 0)
        return buffer.value

    def set_wallpaper(self, path: str) -> None:
        if not ctypes.windll.user32.SystemParametersInfoW(self._SPI_SETDESKWALLPAPER, 0, path, self._SPIF_UPDATE):
            raise OSError("Windows refused that picture as the wallpaper.")

    def processes(self) -> list[dict[str, object]]:
        rows = csv.reader(io.StringIO(_run(["tasklist", "/FO", "CSV", "/NH"])))
        found = []
        for row in rows:
            if len(row) >= 5:
                memory = int(re.sub(r"\D", "", row[4]) or 0) // 1024
                found.append({"name": row[0], "pid": int(row[1]) if row[1].isdigit() else 0, "memory_mb": memory})
        return found

    def end_process(self, name: str) -> int:
        out = _run(["taskkill", "/IM", name])
        return len(re.findall(r"SUCCESS", out, re.IGNORECASE)) or 1

    def open_uri(self, uri: str) -> None:
        os.startfile(uri)  # type: ignore[attr-defined]


def system_backend() -> SystemBackend | None:
    """This machine's controls, or None where they are not built yet (anything but Windows)."""
    return WindowsSystem() if sys.platform == "win32" else None


def settings_page(words: str) -> str | None:
    """'bluetooth' -> 'bluetooth', 'wi-fi settings' -> 'network-wifi'; None for a page not known."""
    cleaned = re.sub(r"\b(?:settings?|page|the|my)\b", " ", (words or "").lower())
    cleaned = " ".join(cleaned.split())
    return SETTINGS_PAGES.get(cleaned) or (SETTINGS_PAGES.get(cleaned.replace(" ", "")) if cleaned else None)


class SystemTool:
    def __init__(self, approval_gate: ApprovalGate, backend: SystemBackend | None, data_dir: Path) -> None:
        self._gate = approval_gate
        self._backend = backend
        self._state = data_dir / "system.json"

    def _ask(self, action: str, risk: RiskLevel, reason: str) -> None:
        self._gate.require(ApprovalRequest(action=action, risk=risk, reason=reason))

    def _missing(self) -> ToolResult | None:
        if self._backend is None:
            return ToolResult.failure("Controlling the computer itself is built for Windows only so far.")
        return None

    def lock(self) -> ToolResult:
        if missing := self._missing():
            return missing
        self._ask("Lock the screen", RiskLevel.MEDIUM, "Locking signs nobody out; your password unlocks it.")
        self._backend.lock()
        return ToolResult.success("Locked.")

    def sleep(self) -> ToolResult:
        if missing := self._missing():
            return missing
        self._ask("Put the computer to sleep", RiskLevel.HIGH, "Sleep stops everything running until you wake it.")
        self._backend.sleep()
        return ToolResult.success("Going to sleep.")

    def power(self, action: str) -> ToolResult:
        if missing := self._missing():
            return missing
        if action == "cancel":
            try:
                self._backend.power("cancel")
            except OSError:
                return ToolResult.failure("There's no shutdown or restart waiting to be cancelled.")
            return ToolResult.success("Cancelled. The computer stays on.")
        verb = "Shut down" if action == "shutdown" else "Restart"
        self._ask(f"{verb} the computer in {SHUTDOWN_GRACE_SECONDS} seconds", RiskLevel.CRITICAL,
                  "Unsaved work in every open app is lost.")
        self._backend.power(action)
        return ToolResult.success(f"{verb.replace('Shut down', 'Shutting down').replace('Restart', 'Restarting')} in "
                                  f"{SHUTDOWN_GRACE_SECONDS} seconds. Say \"cancel shutdown\" to stop it.")

    def brightness(self, change: str = "") -> ToolResult:
        if missing := self._missing():
            return missing
        change = change.strip().lower().rstrip("%")
        try:
            current = self._backend.brightness()
        except OSError:
            current = None
        if not change:
            if current is None:
                return ToolResult.failure("This screen doesn't report its brightness (an external monitor sets its own).")
            return ToolResult.success(f"Brightness is at {current}%.", brightness=current)
        if change in {"up", "down"}:
            if current is None:
                return ToolResult.failure("This screen doesn't report its brightness, so I can't step it.")
            target = current + (20 if change == "up" else -20)
        elif change.isdigit():
            target = int(change)
        else:
            return ToolResult.failure("Say a brightness from 0 to 100, or up or down.")
        target = max(0, min(100, target))
        self._ask(f"Set brightness to {target}%", RiskLevel.MEDIUM, "Brightness is changed back the same way.")
        try:
            self._backend.set_brightness(target)
        except OSError:
            return ToolResult.failure("This screen won't take a brightness from Windows (an external monitor sets its own).")
        return ToolResult.success(f"Brightness set to {target}%.", brightness=target)

    def set_wallpaper(self, words: str) -> ToolResult:
        if missing := self._missing():
            return missing
        if words.strip().lower() in {"restore", "back", "undo", "previous"}:
            previous = str(read_json(self._state, {}).get("previous_wallpaper") or "")
            if not previous:
                return ToolResult.failure("I haven't changed the wallpaper, so there's nothing to put back.")
            words = previous
        path = Path(words.strip().strip("\"'")).expanduser()
        if not path.is_absolute() or path.suffix.lower() not in IMAGE_TYPES:
            return ToolResult.failure("Which picture? Give me the image file's full path, for example "
                                      "\"set my wallpaper to C:\\Pictures\\beach.jpg\".")
        if not path.is_file():
            return ToolResult.failure(f"There's no picture at {path}.")
        self._ask(f"Set the desktop wallpaper to {path}", RiskLevel.HIGH,
                  "This changes your desktop; \"restore my wallpaper\" puts the old one back.")
        before = self._backend.wallpaper()
        self._backend.set_wallpaper(str(path))
        if before and before != str(path):
            atomic_write_text(self._state, json.dumps({"previous_wallpaper": before}))
        return ToolResult.success(f"Wallpaper set to {path.name}.", path=str(path))

    def processes(self) -> ToolResult:
        if missing := self._missing():
            return missing
        found = sorted(self._backend.processes(), key=lambda row: -int(row.get("memory_mb") or 0))
        if not found:
            return ToolResult.failure("Windows didn't list any processes.")
        merged: dict[str, dict[str, object]] = {}
        for row in found:
            entry = merged.setdefault(str(row["name"]), {"name": row["name"], "count": 0, "memory_mb": 0})
            entry["count"] = int(entry["count"]) + 1
            entry["memory_mb"] = int(entry["memory_mb"]) + int(row.get("memory_mb") or 0)
        top = sorted(merged.values(), key=lambda row: -int(row["memory_mb"]))[:12]
        lines = [f"- {row['name']}" + (f" ×{row['count']}" if int(row["count"]) > 1 else "") +
                 f": {int(row['memory_mb']):,} MB" for row in top]
        return ToolResult.success("**Using the most memory**\n" + "\n".join(lines), processes=top)

    def end_process(self, name: str) -> ToolResult:
        if missing := self._missing():
            return missing
        name = name.strip().strip("\"'")
        if not re.fullmatch(r"[\w .+-]{1,60}", name):
            return ToolResult.failure("Name the program, for example \"end process notepad\".")
        image = name if name.lower().endswith(".exe") else f"{name}.exe"
        if image.lower() in PROTECTED or name.lower() in PROTECTED:
            return ToolResult.failure(f"I won't end {image}: Windows needs it to keep running.")
        own = Path(sys.executable).name.lower()
        if image.lower() == own:
            return ToolResult.failure(f"I won't end {image}: I'm running in it.")
        if not any(str(row["name"]).lower() == image.lower() for row in self._backend.processes()):
            return ToolResult.failure(f"Nothing called {image} is running.")
        self._ask(f"End every {image} process", RiskLevel.CRITICAL, "Anything unsaved in it is lost.")
        try:
            ended = self._backend.end_process(image)
        except OSError as exc:
            return ToolResult.failure(f"{image} didn't close: {exc}. It may need to be closed from its own window.")
        return ToolResult.success(f"Asked {image} to close ({ended} process{'es' if ended != 1 else ''}).")

    def open_settings(self, words: str) -> ToolResult:
        if missing := self._missing():
            return missing
        page = settings_page(words)
        if page is None and words.strip():
            return ToolResult.failure(f"I don't know a Settings page for {words.strip()!r}. Try display, sound, "
                                      "bluetooth, wifi, battery or updates.")
        target, label = f"ms-settings:{page or ''}", f"{words.strip().title() or 'Windows'} settings"
        self._ask(f"Open {label}", RiskLevel.MEDIUM, "Opening a Settings page changes nothing by itself.")
        self._backend.open_uri(target)
        return ToolResult.success(f"Opened {label}.")
