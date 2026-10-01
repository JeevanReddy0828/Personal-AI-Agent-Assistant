from __future__ import annotations

import copy
import json
import math
import re
import sys
import shutil
import subprocess
import threading
import time

from laptop_agent.failures import record_failure

_CACHE_TTL_SECONDS = 2.0
_cache_lock = threading.Lock()
_cached_metrics: dict[str, object] | None = None
_cached_at = 0.0
_refresh_thread: threading.Thread | None = None
_collect_lock = threading.Lock()
_failure_lock = threading.Lock()
_reported_failures: set[tuple[str, str]] = set()


def _failure_once(probe: str, cause: str) -> None:
    with _failure_lock:
        key = (probe, cause)
        if key in _reported_failures:
            return
        _reported_failures.add(key)
    record_failure("metrics/" + probe, cause)


def _empty_metrics() -> dict[str, object]:
    return {"cpu_percent": None, "ram_used_mb": None, "ram_total_mb": None,
            "ram_percent": None, "gpus": []}


def _refresh_metrics(force: bool = False) -> None:
    global _cached_at, _cached_metrics
    with _collect_lock:
        with _cache_lock:
            if not force and _cached_metrics is not None and time.monotonic() - _cached_at < _CACHE_TTL_SECONDS:
                return
        try:
            fresh = _collect_system_metrics()
        except Exception as exc:
            _failure_once("refresh", type(exc).__name__)
            fresh = None
        with _cache_lock:
            if fresh is not None:
                _cached_metrics = fresh
            elif _cached_metrics is None:
                _cached_metrics = _empty_metrics()
            _cached_at = time.monotonic()


def system_metrics(*, force: bool = False) -> dict[str, object]:
    """Independent snapshots; Windows subprocess probes never block a normal caller.

    First Windows read returns unknown fields while one worker samples the counters.
    Later reads return the last snapshot during refresh. `force` is a synchronous
    diagnostic refresh, never used by the HTTP endpoint. Other platforms keep their
    synchronous cache behavior.
    """
    global _refresh_thread
    if force or sys.platform != "win32":
        with _cache_lock:
            fresh = _cached_metrics is not None and time.monotonic() - _cached_at < _CACHE_TTL_SECONDS
            if fresh and not force:
                return copy.deepcopy(_cached_metrics)
        _refresh_metrics(force=force)
    else:
        with _cache_lock:
            expired = time.monotonic() - _cached_at >= _CACHE_TTL_SECONDS
            if (_cached_metrics is None or expired) and (_refresh_thread is None or not _refresh_thread.is_alive()):
                _refresh_thread = threading.Thread(target=_refresh_metrics, name="metrics-refresh", daemon=True)
                _refresh_thread.start()
            return copy.deepcopy(_cached_metrics) if _cached_metrics is not None else _empty_metrics()
    with _cache_lock:
        return copy.deepcopy(_cached_metrics) if _cached_metrics is not None else _empty_metrics()


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


# Both counters share one sampling interval. Only compact samples leave PowerShell;
# process paths and command lines are never requested.
_GPU_COUNTER_SCRIPT = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
$errorsSeen=@()
$samples=@((Get-Counter -Counter '\GPU Engine(*engtype_3D)\Utilization Percentage','\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue -ErrorVariable errorsSeen).CounterSamples)
[pscustomobject]@{samples=@($samples | Select-Object InstanceName,CookedValue,Status);failed=($errorsSeen.Count -gt 0)} | ConvertTo-Json -Depth 4 -Compress
"""
_LUID = r"luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_(\d+)"
_ENGINE = re.compile(r"pid_\d+_" + _LUID + r"_eng_(\d+)_engtype_3d", re.I)
_MEMORY = re.compile(_LUID, re.I)


def _dxgi_adapters() -> dict[tuple[int, int], tuple[str, int]]:
    """Match names/capacity by LUID, never WMI order (Optimus may hide one card).

    DXGI 1.1's factory/adapter COM ABI, using only stdlib ctypes. Both acquired
    interfaces are released even when enumeration fails.
    """
    import ctypes as c
    import uuid

    class Luid(c.Structure):
        _fields_ = [("low", c.c_uint32), ("high", c.c_int32)]

    class Description(c.Structure):
        _fields_ = [("name", c.c_wchar * 128), ("vendor", c.c_uint32),
                    ("device", c.c_uint32), ("subsystem", c.c_uint32), ("revision", c.c_uint32),
                    ("video", c.c_size_t), ("system", c.c_size_t), ("shared", c.c_size_t),
                    ("luid", Luid), ("flags", c.c_uint32)]

    def method(pointer, index, result, *arguments):
        table = c.cast(pointer, c.POINTER(c.POINTER(c.c_void_p))).contents
        return c.WINFUNCTYPE(result, c.c_void_p, *arguments)(table[index])

    factory = c.c_void_p()
    adapters = {}
    try:
        # IDXGIFactory1 IID; IUnknown::Release=2, EnumAdapters1=12, GetDesc1=10.
        iid = (c.c_ubyte * 16).from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
        create = c.WinDLL("dxgi").CreateDXGIFactory1
        create.argtypes, create.restype = [c.c_void_p, c.POINTER(c.c_void_p)], c.c_int32
        if create(c.byref(iid), c.byref(factory)) < 0:
            raise OSError("CreateDXGIFactory1 failed")
        for index in range(64):
            adapter = c.c_void_p()
            code = method(factory, 12, c.c_int32, c.c_uint32, c.POINTER(c.c_void_p))(factory, index, c.byref(adapter))
            if code & 0xffffffff == 0x887a0002:  # DXGI_ERROR_NOT_FOUND: enumeration complete
                break
            if code < 0:
                raise OSError("EnumAdapters1 failed")
            try:
                desc = Description()
                if method(adapter, 10, c.c_int32, c.POINTER(Description))(adapter, c.byref(desc)) < 0:
                    raise OSError("GetDesc1 failed")
                if not desc.flags & 2:  # software rendering is not a physical adapter
                    adapters[(desc.luid.high & 0xffffffff, desc.luid.low)] = (desc.name, desc.video)
            finally:
                method(adapter, 2, c.c_uint32)(adapter)
    except (OSError, AttributeError) as exc:
        _failure_once("gpu-dxgi", type(exc).__name__)
    finally:
        if factory:
            method(factory, 2, c.c_uint32)(factory)
    return adapters


def _counter_gpus(output: str, adapters: dict) -> list[dict[str, object]]:
    data = json.loads(output)
    if not isinstance(data, dict) or not isinstance(data.get("samples"), list):
        raise ValueError("counter response shape")
    if data.get("failed"):
        _failure_once("gpu-counters", "counter read failed")
    engines, memory, seen = {}, {}, set()
    for sample in data["samples"]:
        try:
            instance = sample["InstanceName"].lower()
            engine, adapter = _ENGINE.fullmatch(instance), _MEMORY.fullmatch(instance)
            match = engine or adapter
            if not match or instance in seen:
                continue
            seen.add(instance)
            status = sample["Status"]
            if type(status) is not int or status not in (0, 1):
                _failure_once("gpu-counters", "invalid counter status")
                continue
            value = float(sample["CookedValue"])
            if not math.isfinite(value) or value < 0:
                raise ValueError("invalid counter value")
            luid = (int(match[1], 16), int(match[2], 16))
            if engine:
                groups = engines.setdefault(luid, {})
                physical_engine = (int(match[3]), int(match[4]))
                groups[physical_engine] = groups.get(physical_engine, 0.0) + value
            else:
                memory[luid] = memory.get(luid, 0.0) + value
        except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
            _failure_once("gpu-counters", "invalid counter sample")
    result = []
    for index, luid in enumerate(sorted(engines.keys() | memory.keys()), 1):
        name, capacity = adapters.get(luid, (f"GPU {index}", 0))
        busy = max(engines[luid].values()) if luid in engines else None
        result.append({"name": name or f"GPU {index}",
                       "util_percent": round(min(100.0, busy), 1) if busy is not None else None,
                       "mem_used_mb": round(memory[luid] / 1_048_576, 1) if luid in memory else None,
                       "mem_total_mb": round(capacity / 1_048_576) if capacity else None})
    if not result:
        _failure_once("gpu-counters", "no valid adapter samples")
    return result


def _gpu(*, runner=None, adapter_reader=None) -> list[dict[str, object]]:
    runner = runner or subprocess.run
    options = dict(capture_output=True, text=True, timeout=6)
    if sys.platform == "win32":
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            completed = runner([smi, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                                "--format=csv,noheader,nounits"], **options)
            if completed.returncode:
                _failure_once("gpu-nvidia", f"exit {completed.returncode}")
            else:
                gpus = []
                for line in completed.stdout.strip().splitlines():
                    try:
                        name, util, used, total = [part.strip() for part in line.split(",")]
                        util, used, total = float(util), int(used), int(total)
                        if not name or not math.isfinite(util) or not 0 <= util <= 100 or used < 0 or total < 0:
                            raise ValueError("invalid NVIDIA sample")
                        gpus.append({"name": name, "util_percent": util, "mem_used_mb": used, "mem_total_mb": total})
                    except ValueError:
                        _failure_once("gpu-nvidia", "invalid sample")
                if gpus:
                    return gpus
                _failure_once("gpu-nvidia", "no valid samples")
        except (OSError, subprocess.SubprocessError) as exc:
            _failure_once("gpu-nvidia", type(exc).__name__)
    if sys.platform != "win32":
        return []
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        _failure_once("gpu-counters", "PowerShell missing")
        return []
    try:
        completed = runner([powershell, "-NoProfile", "-NonInteractive", "-Command", _GPU_COUNTER_SCRIPT],
                           **options, encoding="utf-8")
        if completed.returncode:
            _failure_once("gpu-counters", f"exit {completed.returncode}")
            return []
        return _counter_gpus(completed.stdout, (adapter_reader or _dxgi_adapters)())
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        _failure_once("gpu-counters", type(exc).__name__)
        return []


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
