"""Информация о компьютере: процессор, память, диски, GPU и датчики."""

import html
import platform
import shutil
import subprocess
import time

import mss.tools
import psutil

from .config import IS_WIN
from .power import pending_info, pending_power


def take_screenshot() -> bytes:
    make = getattr(mss, "MSS", None) or mss.mss  # в новых версиях mss.mss устарел
    with make() as sct:
        shot = sct.grab(sct.monitors[0])  # все мониторы
        png = mss.tools.to_png(shot.rgb, shot.size)
    if png is None:
        raise RuntimeError("Не удалось закодировать снимок экрана в PNG")
    return png


def _to_number(value: object) -> float | None:
    """'12.3' -> 12.3, '[N/A]' -> None."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def get_cpu_info() -> dict:
    """Подробная информация о процессоре."""
    cores = psutil.cpu_count(logical=False) or 1
    logical = psutil.cpu_count(logical=True) or 1
    model = platform.processor() or ""
    if not model and IS_WIN:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as handle:
                model = str(winreg.QueryValueEx(handle, "ProcessorNameString")[0]).strip()
        except OSError:
            model = ""
    frequency = 0
    try:
        cpufreq = psutil.cpu_freq()
        frequency = int(cpufreq.current) if cpufreq else 0
    except (AttributeError, NotImplementedError, OSError, ZeroDivisionError):
        frequency = 0
    return {
        "cores": cores,
        "logical": logical,
        "model": model or "Unknown",
        "frequency_mhz": frequency,
    }


def get_ram_info() -> dict:
    """Информация об оперативной памяти."""
    try:
        mem = psutil.virtual_memory()
    except psutil.Error:
        return {"total_gb": 0.0, "used_gb": 0.0, "free_gb": 0.0, "percent": 0.0}
    return {
        "total_gb": round(mem.total / (1024**3), 2),
        "used_gb": round(mem.used / (1024**3), 2),
        "free_gb": round(mem.free / (1024**3), 2),
        "percent": mem.percent,
    }


def get_disk_info() -> list[dict]:
    """Информация по всем дискам. Недоступные разделы просто пропускаются."""
    disks = []
    for partition in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except (psutil.Error, OSError):
            continue
        disks.append(
            {
                "device": partition.device,
                "mountpoint": partition.mountpoint,
                "fstype": partition.fstype,
                "total_gb": round(usage.total / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "percent": usage.percent,
            }
        )
    return disks


def _nvidia_gpu_info() -> list[dict]:
    """Реальные данные по видеокарте через nvidia-smi, если он установлен."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        result = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gpus = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 5:
            continue
        gpus.append(
            {
                "name": parts[0] or "NVIDIA",
                "load": _to_number(parts[3]),
                "memory_mb": _to_number(parts[1]),
                "memory_used_mb": _to_number(parts[2]),
                "temperature": _to_number(parts[4]),
            }
        )
    return gpus


def _wmi_gpu_info() -> list[dict]:
    """Видеокарты через WMI — работает с любым GPU, но без загрузки в процентах."""
    try:
        import win32com.client
    except ImportError:
        return []
    try:
        wmi = win32com.client.GetObject("winmgmts:")
        gpus = []
        for gpu in wmi.Query("SELECT * FROM Win32_VideoController"):
            ram = int(gpu.AdapterRAM or 0)
            gpus.append(
                {
                    "name": gpu.Name or "Unknown",
                    "load": None,
                    "memory_mb": ram // (1024**2),
                    "memory_used_mb": None,
                    "temperature": None,
                }
            )
        return gpus
    except Exception:  # noqa: BLE001 — WMI может отдать что угодно
        return []


def get_gpu_info() -> list[dict]:
    """Видеокарты: сначала nvidia-smi (реальная загрузка и VRAM), затем WMI."""
    gpus = _nvidia_gpu_info()
    if gpus:
        return gpus
    return _wmi_gpu_info()


def get_system_uptime() -> str:
    """Время работы системы."""
    try:
        uptime_seconds = time.time() - psutil.boot_time()
    except psutil.Error:
        return "Неизвестно"
    days = int(uptime_seconds // 86400)
    hours = int((uptime_seconds % 86400) // 3600)
    minutes = int((uptime_seconds % 3600) // 60)
    return f"{days}д {hours}ч {minutes}мин"


def get_windows_info() -> str:
    """Версия и разрядность ОС."""
    if IS_WIN:
        try:
            # platform.win32_ver() возвращает (release, version, csd, ptype),
            # а номер сборки лежит в version — например "10.0.22631".
            _release, version, csd, _ptype = platform.win32_ver()
            parts = version.split(".")
            if len(parts) < 3:
                raise ValueError(f"Неожиданный формат версии: {version!r}")
            major, minor, build = int(parts[0]), int(parts[1]), int(parts[2])
            if build >= 22000:
                name = "Windows 11"
            elif major == 10:
                name = "Windows 10"
            else:
                name = f"Windows {major}.{minor}"
            arch = "x64" if platform.architecture()[0] == "64bit" else "x86"
            suffix = f" {csd}" if csd else ""
            return f"{name} {arch} (сборка {build}{suffix})"
        except (OSError, ValueError):
            pass
    return platform.platform()


def get_battery_info() -> dict:
    """Состояние батареи (для ноутбуков)."""
    try:
        battery = psutil.sensors_battery()
    except (AttributeError, NotImplementedError):
        battery = None
    if battery is None:
        return {"percent": None, "power_plugged": False, "secs_left": None}
    secs_left = battery.secsleft if battery.secsleft != -1 else None
    return {
        "percent": battery.percent,
        "power_plugged": bool(battery.power_plugged),
        "secs_left": secs_left,
    }


def get_temperatures():
    """Get temperature sensor readings."""
    try:
        temps = psutil.sensors_temperatures()
        return {
            f"{name} {index + 1}": {
                "value": entry.current,
                "high": entry.high,
                "critical": entry.critical,
            }
            for name, entries in (temps or {}).items()
            for index, entry in enumerate(entries)
            if entry.current is not None
        }
    except (AttributeError, NotImplementedError):
        return {}


def system_stats() -> str:
    cpu = psutil.cpu_percent(interval=1)
    cpu_info = get_cpu_info()
    ram_info = get_ram_info()
    disks = get_disk_info()
    gpus = get_gpu_info()
    uptime = get_system_uptime()
    windows = get_windows_info()
    battery = get_battery_info()
    temps = get_temperatures()

    frequency = cpu_info.get("frequency_mhz") or 0
    cpu_text = (
        f"🟢 <b>Загрузка CPU:</b> {cpu:.1f}%\n"
        f"💻 {html.escape(cpu_info['model'])} — {cpu_info['cores']} ядер "
        f"({cpu_info['logical']} потоков)"
        + (f", {frequency} МГц" if frequency else "")
    )
    ram_text = (
        f"🧠 <b>Память:</b> {ram_info['percent']}% "
        f"({ram_info['used_gb']:.1f} / {ram_info['total_gb']:.1f} ГБ)"
    )

    disk_parts = []
    for disk in disks[:3]:
        disk_parts.append(
            f"• <code>{html.escape(disk['mountpoint'])}</code>: "
            f"занято {disk['percent']:.1f}% "
            f"({disk['used_gb']:.1f} / {disk['total_gb']:.1f} ГБ), "
            f"свободно {disk['free_gb']:.1f} ГБ"
        )
    disk_text = "\n".join(disk_parts) if disk_parts else "Диски не найдены."

    gpu_parts = []
    for gpu in gpus[:2]:
        details = []
        if gpu.get("load") is not None:
            details.append(f"загрузка {gpu['load']:.0f}%")
        if gpu.get("memory_mb"):
            used = gpu.get("memory_used_mb")
            memory = f"{gpu['memory_mb']:.0f} МБ"
            if used is not None:
                memory = f"{used:.0f} / {gpu['memory_mb']:.0f} МБ"
            details.append(f"VRAM {memory}")
        if gpu.get("temperature") is not None:
            details.append(f"{gpu['temperature']:.0f} °C")
        suffix = f" ({', '.join(details)})" if details else ""
        gpu_parts.append(f"• {html.escape(gpu['name'])}{suffix}")
    gpu_text = "\n".join(gpu_parts) if gpu_parts else "Информация о GPU недоступна."

    if battery["percent"] is None:
        battery_text = "Данные о батарее недоступны."
    else:
        power = "от сети" if battery["power_plugged"] else "от батареи"
        battery_text = f"{battery['percent']:.0f}% ({power})"
        if battery["secs_left"]:
            battery_text += f", осталось {battery['secs_left'] // 60} мин"

    if temps:
        temp_text = "\n".join(
            f"• {html.escape(name)}: {info['value']:.1f} °C"
            for name, info in list(temps.items())[:3]
        )
    else:
        temp_text = "Датчики температуры недоступны."

    lines = [
        "🖥 <b>Состояние компьютера</b>",
        "",
        cpu_text,
        ram_text,
        "",
        "💿 <b>Диски</b>",
        disk_text,
        "",
        f"🎮 <b>GPU</b>\n{gpu_text}",
        f"🔋 <b>Батарея:</b> {battery_text}",
        f"⏰ <b>Время работы:</b> {uptime}",
        f"🪟 <b>Система:</b> {html.escape(windows)}",
        "🌡 <b>Температура</b>",
        temp_text,
    ]
    if pending_info and pending_power and not pending_power.done():
        lines.extend(["", f"⏳ <b>Запланировано:</b> {html.escape(pending_info)}"])
    return "\n".join(lines)
