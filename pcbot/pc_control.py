"""Управление Windows: громкость, яркость, монитор, окна, сеть, автозапуск."""

import ctypes
import re
import subprocess
import sys

import psutil

from .config import IS_WIN, log
from .windows_api import need_windows, press_media_key


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------- громкость


def change_volume(direction: int, steps: int = 5) -> str:
    """Меняет громкость мультимедийными клавишами (шаг системы — 2 %)."""
    need_windows()
    press_media_key("vol_up" if direction > 0 else "vol_down", times=max(1, steps))
    return "Громче" if direction > 0 else "Тише"


def toggle_mute() -> None:
    need_windows()
    press_media_key("mute")


# ---------------------------------------------------------------- яркость


def get_brightness() -> int | None:
    """Текущая яркость экрана в процентах (ноутбуки и часть мониторов)."""
    if not IS_WIN:
        return None
    try:
        import win32com.client

        for monitor in win32com.client.GetObject("winmgmts:").Query(
            "SELECT * FROM WmiMonitorBrightness"
        ):
            return int(monitor.CurrentBrightness)
    except Exception as error:  # noqa: BLE001 — WMI отвечает чем угодно
        log.debug("Яркость недоступна: %s", error)
    return None


def set_brightness(percent: int) -> int:
    """Устанавливает яркость, возвращая фактическое значение (0–100)."""
    need_windows()
    value = max(0, min(100, percent))
    import win32com.client

    wmi = win32com.client.GetObject("winmgmts:")
    methods = wmi.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods")
    for method in methods:
        method.WmiSetBrightness(1, value)
    return value


def change_brightness(delta: int) -> int:
    """Сдвигает яркость на delta процентов и возвращает новое значение."""
    current = get_brightness()
    if current is None:
        raise RuntimeError("Этот экран не умеет менять яркость через WMI")
    return set_brightness(current + delta)


# ---------------------------------------------------------------- монитор и обои


def monitor_off() -> None:
    """Гасит экран; разбудить можно движением мыши."""
    need_windows()
    user32 = ctypes.windll.user32
    user32.SendMessageW(0xFFFF, 0x0112, 0xF170, 2)  # WM_SYSCOMMAND, SC_MONITORPOWER


def monitor_on() -> None:
    need_windows()
    ctypes.windll.user32.mouse_event(0x0001, 1, 0, 0, 0)  # лёгкий сдвиг мыши


def set_wallpaper(path) -> None:
    """Ставит картинку обоями рабочего стола."""
    need_windows()
    target = str(path)
    ok = ctypes.windll.user32.SystemParametersInfoW(20, 0, target, 3)
    if not ok:
        raise RuntimeError("Windows не приняла файл как обои")


# ---------------------------------------------------------------- окна


def list_windows(limit: int = 40) -> list[tuple[int, str]]:
    """Видимые окна с заголовками: [(hwnd, заголовок)]."""
    need_windows()
    import win32gui

    windows: list[tuple[int, str]] = []

    def collect(hwnd, _param) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd).strip()
        if title:
            windows.append((hwnd, title))
        return True

    win32gui.EnumWindows(collect, None)
    return windows[:limit]


def activate_window(hwnd: int) -> str:
    """Разворачивает и выводит окно на передний план."""
    need_windows()
    import win32gui

    title = win32gui.GetWindowText(hwnd)
    win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 — Windows запрещает фокус из фона
        win32gui.BringWindowToTop(hwnd)
    return title


def minimize_window(hwnd: int) -> str:
    need_windows()
    import win32gui

    title = win32gui.GetWindowText(hwnd)
    win32gui.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    return title


def close_window(hwnd: int) -> str:
    """Вежливо просит окно закрыться (WM_CLOSE), а не убивает процесс."""
    need_windows()
    import win32gui

    title = win32gui.GetWindowText(hwnd)
    win32gui.PostMessage(hwnd, 0x0010, 0, 0)
    return title


# ---------------------------------------------------------------- сеть


def network_info() -> dict:
    """Адреса и трафик по сетевым интерфейсам."""
    addresses = []
    for name, entries in psutil.net_if_addrs().items():
        for entry in entries:
            if entry.family == 2 and not entry.address.startswith("127."):  # IPv4
                addresses.append({"name": name, "ip": entry.address})
    counters = psutil.net_io_counters()
    return {
        "addresses": addresses,
        "sent_mb": round(counters.bytes_sent / 1024**2, 1),
        "received_mb": round(counters.bytes_recv / 1024**2, 1),
        "wifi": wifi_ssid(),
    }


def wifi_ssid() -> str | None:
    """Имя сети Wi-Fi, если адаптер подключён."""
    if not IS_WIN:
        return None
    try:
        result = subprocess.run(
            ["netsh", "wlan", "show", "interfaces"],
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
            timeout=10,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"^\s*SSID\s*:\s*(.+)$", result.stdout, re.MULTILINE)
    return match.group(1).strip() if match else None


def ping(host: str = "8.8.8.8", count: int = 4) -> str:
    """Пингует узел и возвращает сводку."""
    command = (
        ["ping", "-n", str(count), host]
        if IS_WIN
        else ["ping", "-c", str(count), host]
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="cp866" if IS_WIN else "utf-8",
            errors="replace",
            timeout=60,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError) as error:
        return f"Не удалось выполнить ping: {error}"
    output = (result.stdout or "").strip()
    return output[-1500:] if output else "Нет ответа."


# ---------------------------------------------------------------- автозапуск и программы

RUN_KEYS = (
    ("HKCU", "Software\\Microsoft\\Windows\\CurrentVersion\\Run"),
    ("HKLM", "Software\\Microsoft\\Windows\\CurrentVersion\\Run"),
)
UNINSTALL_KEYS = (
    ("HKLM", "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
    ("HKLM", "Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
    ("HKCU", "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall"),
)


def _reg_root(name: str):
    import winreg

    return winreg.HKEY_CURRENT_USER if name == "HKCU" else winreg.HKEY_LOCAL_MACHINE


def startup_entries() -> list[str]:
    """Программы, которые запускаются вместе с Windows."""
    if not IS_WIN:
        return []
    import winreg

    entries: list[str] = []
    for root_name, key_path in RUN_KEYS:
        try:
            with winreg.OpenKey(_reg_root(root_name), key_path) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    entries.append(f"{name} — {value}")
                    index += 1
        except OSError:
            continue
    return entries


def installed_programs(limit: int = 40) -> list[str]:
    """Установленные программы из списка «Удаление программ»."""
    if not IS_WIN:
        return []
    import winreg

    programs: list[str] = []
    seen: set[str] = set()
    for root_name, key_path in UNINSTALL_KEYS:
        try:
            with winreg.OpenKey(_reg_root(root_name), key_path) as key:
                index = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                    except OSError:
                        continue
                    name = str(name).strip()
                    if name and name not in seen:
                        seen.add(name)
                        programs.append(name)
        except OSError:
            continue
    programs.sort(key=str.casefold)
    return programs[:limit]


def services_summary() -> dict:
    """Сколько служб Windows запущено и остановлено."""
    if not IS_WIN:
        return {"running": 0, "stopped": 0, "total": 0}
    running = stopped = 0
    try:
        for service in psutil.win_service_iter():
            try:
                status = service.status()
            except psutil.Error:
                continue
            if status == "running":
                running += 1
            elif status == "stopped":
                stopped += 1
    except (AttributeError, psutil.Error) as error:
        log.debug("Службы недоступны: %s", error)
    return {"running": running, "stopped": stopped, "total": running + stopped}


# ---------------------------------------------------------------- прочее


def empty_recycle_bin() -> None:
    need_windows()
    flags = 0x1 | 0x2 | 0x4  # без подтверждения, без прогресса, без звука
    ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)


def flush_dns() -> str:
    """Сбрасывает кэш DNS."""
    result = subprocess.run(
        ["ipconfig", "/flushdns"] if IS_WIN else ["resolvectl", "flush-caches"],
        capture_output=True,
        text=True,
        encoding="cp866" if IS_WIN else "utf-8",
        errors="replace",
        timeout=30,
        creationflags=_no_window(),
    )
    text = (result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError((result.stderr or text or "команда завершилась с ошибкой").strip())
    return text or "Готово."


def open_settings_page(page: str = "display") -> None:
    """Открывает нужный раздел «Параметров» Windows."""
    need_windows()
    subprocess.Popen(["cmd", "/c", "start", "", f"ms-settings:{page}"], close_fds=True)


def python_process_count() -> int:
    """Сколько процессов Python запущено (диагностика утечек)."""
    count = 0
    for process in psutil.process_iter(["name"]):
        if (process.info["name"] or "").lower() in {"python.exe", "pythonw.exe"}:
            count += 1
    return count


def is_admin() -> bool:
    """Запущен ли процесс с правами администратора."""
    if not IS_WIN:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def reload_environment() -> str:
    """Перечитывает переменные окружения текущего процесса (для отладки)."""
    return sys.executable
