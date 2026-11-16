"""Питание: выключение, перезагрузка, сон и блокировка."""

import asyncio
import contextlib
import subprocess
from datetime import datetime, timedelta

from .config import OS, log

POWER_COMMANDS = {
    "Windows": {
        "shutdown": ["shutdown", "/s", "/t", "0"],
        "reboot": ["shutdown", "/r", "/t", "0"],
        "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
    },
    "Linux": {
        "shutdown": ["systemctl", "poweroff"],
        "reboot": ["systemctl", "reboot"],
        "sleep": ["systemctl", "suspend"],
        "lock": ["loginctl", "lock-session"],
    },
    "Darwin": {
        "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
        "reboot": ["osascript", "-e", 'tell app "System Events" to restart'],
        "sleep": ["pmset", "sleepnow"],
        "lock": ["pmset", "displaysleepnow"],
    },
}


ACTION_NAMES = {
    "shutdown": "Выключение",
    "reboot": "Перезагрузка",
    "sleep": "Спящий режим",
    "lock": "Блокировка",
}


pending_power: asyncio.Task | None = None


pending_info: str = ""  # что и когда запланировано (для показа)


async def run_power(action: str, delay: int) -> None:
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        log.info("Запланированное действие отменено")
        raise
    cmd = POWER_COMMANDS[OS][action]
    log.info("Выполняю: %s", cmd)
    subprocess.Popen(cmd)


def schedule_power(action: str, delay: int) -> str:
    global pending_power, pending_info
    if pending_power is not None and not pending_power.done():
        pending_power.cancel()
    pending_power = asyncio.create_task(run_power(action, delay))
    at = (datetime.now() + timedelta(seconds=delay)).strftime("%H:%M")
    pending_info = f"{ACTION_NAMES[action]} в {at}"
    return at


async def cancel_pending_power() -> bool:
    global pending_power, pending_info
    task, pending_power = pending_power, None
    pending_info = ""
    if task is None or task.done():
        return False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return True
