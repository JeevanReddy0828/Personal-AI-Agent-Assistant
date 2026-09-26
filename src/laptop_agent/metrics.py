from __future__ import annotations

import copy
import shutil
import subprocess
import threading
import time

_CACHE_TTL_SECONDS = 2.0
_cache_lock = threading.Lock()
_cached_metrics: dict[str, object] | None = None
_cached_at = 0.0


def system_metrics(*, force: bool = False) -> dict[str, object]:
    """Return a short-lived, independent metrics snapshot.

    Several UI panels request metrics at once. Caching avoids repeating process
    probes and GPU subprocesses while returning a copy so callers cannot mutate
    the shared snapshot.
    """
    global _cached_at, _cached_metrics
    with _cache_lock:
        now = time.monotonic()
        if not force and _cached_metrics is not None and now - _cached_at < _CACHE_TTL_SECONDS:
            return copy.deepcopy(_cached_metrics)
        fresh = _collect_system_metrics()
        _cached_metrics = fresh
        _cached_at = now
        return copy.deepcopy(fresh)


def _collect_system_metrics() -> dict[str, object]:
    """Best-effort CPU / RAM / GPU usage. Uses psutil if present, else platform tools.

    Every field degrades to None when unavailable, so callers can render "n/a".
    """
    cpu, ram_used, ram_total = _cpu_ram()
    gpus = _gpu()
    return {
        "cpu_percent": cpu,
        "ram_used_mb": ram_used,
        "ram_total_mb": ram_total,
        "ram_percent": round(ram_used / ram_total * 100) if ram_used and ram_total else None,
        "gpus": gpus,
    }


def _cpu_ram() -> tuple[float | None, int | None, int | None]:
    try:
        import psutil  # type: ignore

        memory = psutil.virtual_memory()
        return (
            round(psutil.cpu_percent(interval=None), 1),
            round(memory.used / 1_048_576),
            round(memory.total / 1_048_576),
        )
    except ImportError:
        pass
    return _windows_cpu_ram()


def _windows_cpu_ram() -> tuple[float | None, int | None, int | None]:
    cpu = ram_used = ram_total = None
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        return cpu, ram_used, ram_total
    script = (
        "$c=(Get-CimInstance Win32_Processor|Measure-Object -Property LoadPercentage -Average).Average;"
        "$o=Get-CimInstance Win32_OperatingSystem;"
        "Write-Output ('{0}|{1}|{2}' -f $c,$o.FreePhysicalMemory,$o.TotalVisibleMemorySize)"
    )
    try:
        out = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            capture_output=True, text=True, timeout=6,
        ).stdout.strip()
        load, free_kb, total_kb = out.split("|")
        cpu = round(float(load), 1) if load else None
        total_total = round(int(total_kb) / 1024)
        free = round(int(free_kb) / 1024)
        ram_total = total_total
        ram_used = total_total - free
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return cpu, ram_used, ram_total


def _gpu() -> list[dict[str, object]]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return []
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    gpus: list[dict[str, object]] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            gpus.append(
                {
                    "name": parts[0],
                    "util_percent": float(parts[1]),
                    "mem_used_mb": int(parts[2]),
                    "mem_total_mb": int(parts[3]),
                }
            )
        except ValueError:
            continue
    return gpus


def battery_status() -> dict[str, object] | None:
    """Charge and whether it is plugged in, or None on a machine with no battery.

    psutil when present; otherwise the platform's own report, so "how much battery do I
    have" does not depend on an optional package. Asked of a chat model, that question
    got a guess - the model cannot see the machine it runs beside.
    """
    try:
        import psutil  # type: ignore

        battery = psutil.sensors_battery()
        if battery is None:
            return None
        return {"percent": round(battery.percent), "plugged": bool(battery.power_plugged)}
    except (ImportError, AttributeError, OSError):
        pass
    import sys

    if sys.platform.startswith("win"):
        import ctypes

        class _PowerStatus(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                        ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]

        status = _PowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        if status.BatteryFlag == 128 or status.BatteryLifePercent == 255:   # no battery / unknown
            return None
        return {"percent": int(status.BatteryLifePercent), "plugged": status.ACLineStatus == 1}
    from pathlib import Path

    for supply in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            percent = int((supply / "capacity").read_text().strip())
            state = (supply / "status").read_text().strip().lower()
        except (OSError, ValueError):
            continue
        return {"percent": percent, "plugged": state in {"charging", "full", "not charging"}}
    return None
