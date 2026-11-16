"""Процессы: список, сортировка, закрытие."""

import contextlib
import os
import time

import psutil

PROTECTED = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe",
    "dwm.exe", "fontdrvhost.exe", "memory compression", "secure system",
    # оболочка и системные службы: их закрытие ломает рабочий стол
    "explorer.exe", "conhost.exe", "searchhost.exe", "searchapp.exe",
    "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "lockapp.exe", "runtimebroker.exe", "sihost.exe",
    "wudfhost.exe", "ctfmon.exe", "taskhostw.exe", "spoolsv.exe",
    "lsaiso.exe", "securityhealthservice.exe", "audiodg.exe", "wmiprvse.exe",
    "rundll32.exe", "dllhost.exe", "wmiapsrv.exe",
}


def protected_pids() -> set[int]:
    """PID'ы, которые нельзя закрывать: система, сам бот и панель управления."""
    pids = {0, 4, os.getpid()}
    with contextlib.suppress(OSError):
        pids.add(os.getppid())
    return pids


def process_rows(n: int | None = 10) -> list[tuple[float, float, int, str]]:
    """Список процессов: (CPU %, RAM %, PID, имя). n=None — без ограничения."""
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.cpu_percent(None)
    time.sleep(0.7)
    cores = psutil.cpu_count() or 1
    rows = []
    for p in procs:
        if p.pid in (0, 4):
            continue
        try:
            rows.append((p.cpu_percent(None) / cores, p.memory_percent(), p.pid, p.info["name"] or "?"))
        except psutil.Error:
            continue
    rows.sort(reverse=True)
    return rows if n is None else rows[:n]


def sort_rows(rows: list[tuple[float, float, int, str]], sort: str) -> list[tuple[float, float, int, str]]:
    """Сортирует процессы: по CPU, по RAM или по имени."""
    if sort == "ram":
        return sorted(rows, key=lambda row: (-row[1], row[3].lower()))
    if sort == "name":
        return sorted(rows, key=lambda row: row[3].lower())
    return sorted(rows, key=lambda row: (-row[0], row[3].lower()))


def format_rows(rows) -> str:
    out = ["CPU%  RAM%   PID  Имя"]
    for cpu, mem, pid, name in rows:
        out.append(f"{cpu:4.1f} {mem:5.1f} {pid:6d}  {name}")
    return "\n".join(out)


def kill_process(pid: int) -> str:
    if pid in protected_pids():
        raise RuntimeError("Это сам бот или системный процесс — его так не закрыть")
    p = psutil.Process(pid)
    name = p.name()
    if name.lower() in PROTECTED:
        raise RuntimeError(f"{name} — системный процесс, его закрывать нельзя")
    p.terminate()
    try:
        p.wait(timeout=3)
    except psutil.TimeoutExpired:
        p.kill()
    return name


def close_all_processes() -> dict:
    """Закрыть все пользовательские процессы (не системные)."""
    result: dict[str, list[dict[str, object]]] = {
        "success": [],
        "errors": [],
        "protected": [],
    }
    protected = protected_pids()
    for p in psutil.process_iter(["name", "pid"]):
        if p.pid in (0, 4):
            continue
        try:
            name = p.name()
            if name.lower() in PROTECTED or p.pid in protected:
                result["protected"].append({"pid": p.pid, "name": name})
                continue
            kill_process(p.pid)
            result["success"].append({"pid": p.pid, "name": name})
        except psutil.NoSuchProcess:
            result["errors"].append({"pid": p.pid, "name": "?", "reason": "уже закрыт"})
        except psutil.AccessDenied:
            result["errors"].append({"pid": p.pid, "name": "?", "reason": "нет прав"})
        except Exception as e:  # noqa: BLE001
            result["errors"].append({"pid": p.pid, "name": "?", "reason": str(e)})
    return result
