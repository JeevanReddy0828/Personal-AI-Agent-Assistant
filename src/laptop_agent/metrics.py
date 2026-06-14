from __future__ import annotations

import shutil
import subprocess


def system_metrics() -> dict[str, object]:
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
            round(psutil.cpu_percent(interval=0.1), 1),
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
