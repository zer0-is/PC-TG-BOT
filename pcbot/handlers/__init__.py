"""Обработчики Telegram.

Каждый модуль регистрирует свои обработчики при импорте через декораторы
общего диспетчера, поэтому достаточно импортировать пакет целиком.
"""

from . import (
    apps,
    clipboard,
    control,
    errors,
    files,
    files_mgr,
    input,
    menu,
    processes,
    recording,
    screen,
    shell,
)

__all__ = [
    "apps",
    "clipboard",
    "control",
    "errors",
    "files",
    "files_mgr",
    "input",
    "menu",
    "processes",
    "recording",
    "screen",
    "shell",
]
