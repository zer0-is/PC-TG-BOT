"""Windows-интеграции: клавиши, буфер, камера, программы и Steam."""

import ctypes
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from .config import IS_WIN, OS


def need_windows() -> None:
    if not IS_WIN:
        raise RuntimeError("Эта функция работает только на Windows")


VK = {
    "vol_up": 0xAF,
    "vol_down": 0xAE,
    "mute": 0xAD,
    "play": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
}


def press_media_key(name: str, times: int = 1) -> None:
    need_windows()
    user32 = ctypes.windll.user32
    for _ in range(times):
        user32.keybd_event(VK[name], 0, 0, 0)
        user32.keybd_event(VK[name], 0, 2, 0)  # KEYEVENTF_KEYUP
        time.sleep(0.02)


def show_message_box(text: str) -> None:
    """Показывает окно поверх всех и ждёт, пока его закроют."""
    need_windows()
    # MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_TOPMOST | MB_SETFOREGROUND
    ctypes.windll.user32.MessageBoxW(None, text, "Сообщение из Telegram", 0x40 | 0x1000 | 0x40000 | 0x10000)


CF_UNICODETEXT = 13


def _clipboard_api():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
    return user32, kernel32


def _open_clipboard(user32) -> None:
    for _ in range(20):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise RuntimeError("Буфер обмена занят другой программой, попробуй ещё раз")


def clipboard_get() -> str:
    need_windows()
    user32, kernel32 = _clipboard_api()
    _open_clipboard(user32)
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def clipboard_set(text: str) -> None:
    need_windows()
    user32, kernel32 = _clipboard_api()
    data = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(data)
    _open_clipboard(user32)
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
        if not handle:
            raise RuntimeError("Не удалось выделить память под буфер")
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, data, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            raise RuntimeError("Не удалось записать в буфер")
    finally:
        user32.CloseClipboard()


def webcam_photo() -> bytes:
    try:
        import cv2  # opencv-python-headless
    except ImportError as e:
        raise RuntimeError("Не установлена библиотека камеры — запусти update.bat") from e
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW) if IS_WIN else cv2.VideoCapture(0)
    if not cap.isOpened():
        raise RuntimeError("Камера не найдена или занята другой программой")
    try:
        for _ in range(15):  # камере нужно пару кадров, чтобы настроить яркость
            cap.read()
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError("Не удалось снять кадр")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Не удалось сохранить фото")
        return buf.tobytes()
    finally:
        cap.release()


APPS = {
    "steam": ("🎮 Steam", ["steam://open/main"]),
    "telegram": ("✈️ Telegram", [r"%APPDATA%\Telegram Desktop\Telegram.exe"]),
    "music": ("🎵 Яндекс Музыка", [r"%LOCALAPPDATA%\Programs\YandexMusic\Яндекс Музыка.exe"]),
    "chrome": (
        "🌐 Chrome",
        [
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ],
    ),
    "explorer": ("📁 Проводник", ["explorer.exe"]),
    "taskmgr": ("📈 Диспетчер задач", ["taskmgr.exe"]),
}


def open_target(target: str) -> None:
    if IS_WIN:
        os.startfile(target)  # type: ignore[attr-defined]
    elif OS == "Darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])


def launch_app(key: str) -> None:
    title, variants = APPS[key]
    if key == "telegram" and IS_WIN:
        app_alias = shutil.which("Telegram.exe")
        if app_alias:
            open_target(app_alias)
            return
    for v in variants:
        path = os.path.expandvars(v)
        if "://" in path or not ("\\" in path or "/" in path) or Path(path).exists():
            open_target(path)
            return
    raise RuntimeError(f"{title}: не нашёл программу на компьютере")


SKIP_APPIDS = {"228980", "1070560", "1391110", "1628350", "1493710", "961940"}  # служебные пакеты Steam


def steam_dir() -> Path | None:
    if IS_WIN:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                path = Path(winreg.QueryValueEx(key, "SteamPath")[0])
                if path.exists():
                    return path
        except OSError:
            pass
    for guess in (
        Path(os.path.expandvars(r"%ProgramFiles(x86)%\Steam")),
        Path(os.path.expandvars(r"%ProgramFiles%\Steam")),
        Path.home() / ".steam" / "steam",
    ):
        if guess.exists():
            return guess
    return None


def _vdf_value(text: str, key: str) -> str:
    m = re.search(rf'"{key}"\s+"([^"]*)"', text, re.IGNORECASE)
    return m.group(1) if m else ""


def steam_libraries() -> set[Path]:
    root = steam_dir()
    if not root:
        raise RuntimeError("Steam не найден на компьютере")
    libraries = {root}
    vdf = root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'"path"\s+"([^"]+)"', text):
            libraries.add(Path(m.group(1).replace("\\\\", "\\")))
    return libraries


def steam_games() -> list[tuple[str, str, int]]:
    """Установленные игры: (appid, название, когда последний раз играл), свежие — первыми."""
    games = {}
    for lib in steam_libraries():
        for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
            try:
                text = acf.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            appid = _vdf_value(text, "appid")
            name = _vdf_value(text, "name")
            if not appid or not name or appid in SKIP_APPIDS or "redistributable" in name.lower():
                continue
            last = int(_vdf_value(text, "LastPlayed") or 0)
            games[appid] = (appid, name, last)
    return sorted(games.values(), key=lambda g: (-g[2], g[1].lower()))


def run_steam_game(appid: str) -> None:
    open_target(f"steam://rungameid/{appid}")


def running_steam_games() -> list[tuple[int, str, str]]:
    """Запущенные игры из Steam: (pid, имя процесса, папка игры)."""
    commons = [str(lib / "steamapps" / "common").lower() + os.sep for lib in steam_libraries()]
    found = []
    for p in psutil.process_iter(["exe", "name"]):
        exe = (p.info.get("exe") or "").lower()
        for common in commons:
            if exe.startswith(common):
                folder = Path(p.info["exe"]).parts[len(Path(common).parts)]
                found.append((p.pid, p.info["name"] or "?", folder))
                break
    return found


SITES = [
    ("▶️ YouTube", "https://www.youtube.com"),
    ("💙 ВКонтакте", "https://vk.com"),
    ("🔎 Яндекс", "https://ya.ru"),
    ("🗺 2ГИС", "https://2gis.ru"),
    ("🎬 Кинопоиск", "https://www.kinopoisk.ru"),
    ("🟣 Twitch", "https://www.twitch.tv"),
]


TG_LINKS: list[tuple[str, str]] = [
]


def open_site(url: str) -> None:
    for v in APPS["chrome"][1]:
        path = os.path.expandvars(v)
        if Path(path).exists():
            subprocess.Popen([path, url])
            return
    open_target(url)  # нет Chrome — откроем в браузере по умолчанию
