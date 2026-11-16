"""Обработчики Telegram.

Каждый модуль регистрирует свои обработчики при импорте через декораторы
общего диспетчера, поэтому достаточно импортировать пакет целиком.
"""

from . import apps, clipboard, errors, files, menu, processes, screen, shell

__all__ = [
    "apps",
    "clipboard",
    "errors",
    "files",
    "menu",
    "processes",
    "screen",
    "shell",
]
