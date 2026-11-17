"""Файловый менеджер: просмотр папок, поиск, архивы и корзина."""

import ctypes
import os
import string
import zipfile
from pathlib import Path

from .config import log

MAX_ENTRIES = 12


def human_size(size: int) -> str:
    """1536 -> '1.5 КБ'."""
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{value:.1f} {unit}" if unit != "Б" else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


def drives() -> list[Path]:
    """Существующие диски: C:\\, D:\\ и т. д."""
    result = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists():
            result.append(root)
    return result


def list_dir(path: Path, limit: int = MAX_ENTRIES) -> list[dict]:
    """Содержимое папки: сначала каталоги, затем файлы, по алфавиту."""
    entries: list[dict] = []
    try:
        children = list(os.scandir(path))
    except (OSError, ValueError) as error:
        log.debug("Не удалось прочитать %s: %s", path, error)
        return entries
    entries = []
    for child in children:
        try:
            is_dir = child.is_dir()
        except OSError:
            is_dir = False
        try:
            size = 0 if is_dir else child.stat().st_size
        except OSError:
            size = 0
        entries.append({"name": child.name, "is_dir": is_dir, "size": size})
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
    return entries[:limit]


def parent_of(path: Path) -> Path | None:
    """Родительская папка, если она есть."""
    parent = path.parent
    return parent if parent != path else None


def search_files(root: Path, query: str, limit: int = 20) -> list[Path]:
    """Ищет файлы по подстроке в имени, не заходя глубже пяти уровней."""
    matches: list[Path] = []
    query = query.casefold()
    base_depth = len(root.parts)
    try:
        for current, dirs, files in os.walk(root):
            if len(Path(current).parts) - base_depth >= 5:
                dirs[:] = []
            for name in files:
                if query in name.casefold():
                    matches.append(Path(current) / name)
                    if len(matches) >= limit:
                        return matches
    except OSError as error:
        log.debug("Поиск прерван в %s: %s", root, error)
    return matches


def size_of(path: Path, limit: int = 5000) -> int:
    """Размер папки или файла; для больших деревьев останавливается на лимите."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    seen = 0
    for current, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(current) / name).stat().st_size
            except OSError:
                continue
            seen += 1
            if seen >= limit:
                return total
    return total


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def delete_to_recycle(path: Path) -> None:
    """Удаляет файл в корзину, а не безвозвратно."""
    if os.name == "nt":
        # Двойной ноль в конце — требование SHFileOperation.
        source = str(path) + "\0\0"
        operation = _SHFILEOPSTRUCTW()
        operation.wFunc = 3  # FO_DELETE
        operation.pFrom = source
        operation.fFlags = 0x40 | 0x10 | 0x4  # корзина, без подтверждения, без звука
        code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        if code != 0:
            raise OSError(f"Windows не смогла удалить файл (код {code})")
        return
    path.unlink()


def make_zip(path: Path, target: Path | None = None) -> Path:
    """Упаковывает файл или папку в zip-архив."""
    archive = target or path.with_suffix(path.suffix + ".zip")
    if archive == path:
        archive = path.with_name(path.name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        if path.is_file():
            bundle.write(path, path.name)
        else:
            for current, _dirs, files in os.walk(path):
                for name in files:
                    full = Path(current) / name
                    bundle.write(full, full.relative_to(path.parent))
    return archive


def safe_join(base: Path, relative: str) -> Path | None:
    """Склеивает путь, не выпуская его за пределы base."""
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate
