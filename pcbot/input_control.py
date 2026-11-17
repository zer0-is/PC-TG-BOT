"""Удалённый ввод: мышь и клавиатура. Работает только при ALLOW_INPUT."""

import ctypes

from .config import IS_WIN
from .windows_api import need_windows

# Виртуальные коды клавиш для сочетаний.
VK_CODES = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
}

MOUSE_BUTTONS = {"left": 0x0002, "right": 0x0008, "middle": 0x0020}
MOUSE_UP = {"left": 0x0004, "right": 0x0010, "middle": 0x0040}


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _INPUT(ctypes.Structure):
    class _PAYLOAD(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT)]

    _anonymous_ = ("payload",)
    _fields_ = [("type", ctypes.c_ulong), ("payload", _PAYLOAD)]


def _send(*inputs: _INPUT) -> None:
    if not IS_WIN:
        raise RuntimeError("Ввод поддерживается только на Windows")
    array = (_INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))
    if sent != len(inputs):
        raise RuntimeError("Windows отклонила событие ввода")


def _key_event(vk: int, up: bool = False) -> _INPUT:
    event = _INPUT()
    event.type = 1  # INPUT_KEYBOARD
    event.ki = _KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0x0002 if up else 0, time=0)
    return event


def _unicode_event(char: str, up: bool = False) -> _INPUT:
    event = _INPUT()
    event.type = 1
    flags = 0x0004 | (0x0002 if up else 0)  # KEYEVENTF_UNICODE [+ KEYUP]
    event.ki = _KEYBDINPUT(wVk=0, wScan=ord(char), dwFlags=flags, time=0)
    return event


def mouse_position() -> tuple[int, int]:
    if not IS_WIN:
        raise RuntimeError("Мышь доступна только на Windows")
    point = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def mouse_move(x: int, y: int) -> tuple[int, int]:
    need_windows()
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    return mouse_position()


def mouse_click(button: str = "left", double: bool = False) -> str:
    need_windows()
    down = MOUSE_BUTTONS.get(button)
    up = MOUSE_UP.get(button)
    if down is None or up is None:
        raise ValueError("Кнопка должна быть left, right или middle")
    clicks = 2 if double else 1
    events = []
    for _ in range(clicks):
        events.append(_mouse_event(down))
        events.append(_mouse_event(up))
    _send(*events)
    return f"{'Двойной клик' if double else 'Клик'} ({button})"


def _mouse_event(flags: int) -> _INPUT:
    event = _INPUT()
    event.type = 0  # INPUT_MOUSE
    event.mi = _MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flags, time=0)
    return event


def mouse_scroll(amount: int) -> str:
    """amount > 0 — вверх, < 0 — вниз (шаг 120 = один «щелчок» колеса)."""
    need_windows()
    step = 120 if amount > 0 else -120
    clicks = max(1, abs(int(amount)))
    events = []
    for _ in range(min(clicks, 20)):
        event = _INPUT()
        event.type = 0
        event.mi = _MOUSEINPUT(
            dx=0, dy=0, mouseData=ctypes.c_ulong(step & 0xFFFFFFFF).value,
            dwFlags=0x0800, time=0,
        )
        events.append(event)
    _send(*events)
    return f"Прокрутка {'вверх' if amount > 0 else 'вниз'}"


def type_text(text: str) -> int:
    """Вводит текст в активное окно, поддерживая кириллицу."""
    need_windows()
    if not text:
        return 0
    events: list[_INPUT] = []
    for char in text:
        events.append(_unicode_event(char))
        events.append(_unicode_event(char, up=True))
    for start in range(0, len(events), 20):
        _send(*events[start:start + 20])
    return len(text)


def press_combo(combo: str) -> str:
    """Нажимает сочетание вроде 'win+d', 'alt+f4' или одну клавишу 'enter'."""
    need_windows()
    parts = [part.strip().lower() for part in combo.split("+") if part.strip()]
    if not parts:
        raise ValueError("Пустое сочетание клавиш")
    codes = []
    for part in parts:
        if len(part) == 1 and part.isalnum():
            code = ctypes.windll.user32.VkKeyScanW(ord(part)) & 0xFF
        elif part in VK_CODES:
            code = VK_CODES[part]
        else:
            raise ValueError(f"Неизвестная клавиша: {part}")
        codes.append(code)
    events = [_key_event(code) for code in codes]
    events += [_key_event(code, up=True) for code in reversed(codes)]
    _send(*events)
    return combo


def click_at(x: int, y: int, button: str = "left") -> str:
    """Двигает мышь в точку и кликает — удобно по координатам со скриншота."""
    mouse_move(x, y)
    return mouse_click(button)
