import asyncio
import ctypes
import hashlib
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk
import pystray
from dotenv import dotenv_values
from PIL import Image, ImageDraw

APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
LOG_FILE = APP_DIR / "logs" / "bot.log"
ENV_FILE = APP_DIR / ".env"
AUTOSTART_NAME = "PCBot Control Panel.bat"
ENV_DEFAULTS = {
    "BOT_TOKEN": "",
    "ALLOWED_USER_IDS": "",
    "OPENROUTER_API_KEY": "",
    "OPENROUTER_MODEL": "inclusionai/ling-3.2-flash:free",
    "ALLOW_SHELL": "false",
    "ALLOW_FILES": "false",
    "POWER_DELAY": "25",
    # Ключ подписи кнопок. Пусто — бот выводит его из токена.
    "SIGNING_KEY": "",
    # Сколько команд в минуту разрешено одному пользователю.
    "RATE_LIMIT": "20",
}

# Файл-блокировка: две копии панели означают двух ботов с одним токеном,
# а Telegram такие запросы отклоняет ("terminated by other getUpdates").
LOCK_FILE = Path(tempfile.gettempdir()) / (
    "pcbot-panel-" + hashlib.sha256(str(APP_DIR).encode("utf-8")).hexdigest()[:8] + ".lock"
)


class SingleInstance:
    """Гарантирует, что панель работает в единственном экземпляре."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: object | None = None

    def acquire(self) -> bool:
        """Забирает блокировку. False — панель уже запущена."""
        if os.name != "nt":
            return True
        try:
            import msvcrt
        except ImportError:
            return True
        handle = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b")  # noqa: SIM115 — держим открытым ради блокировки
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii"))
            handle.flush()
            self._handle = handle
            return True
        except OSError:
            if handle is not None:
                handle.close()
            return False


def read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {key: value or "" for key, value in dotenv_values(path).items()}


def write_env_values(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    else:
        lines = []

    assignment = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    updated = set()
    result = []
    for line in lines:
        match = assignment.match(line)
        key = match.group(1) if match else None
        if key in values:
            escaped = values[key].replace("\\", "\\\\").replace("'", "\\'")
            result.append(f"{key}='{escaped}'")
            updated.add(key)
        else:
            result.append(line)
    for key, value in values.items():
        if key not in updated:
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            result.append(f"{key}='{escaped}'")

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write("\n".join(result) + "\n")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def configuration_is_complete(values: dict[str, str]) -> bool:
    token = values.get("BOT_TOKEN", "").strip()
    ids = [
        part.strip()
        for part in values.get("ALLOWED_USER_IDS", "").split(",")
        if part.strip()
    ]
    return bool(token and ids and all(part.isdecimal() for part in ids))


def run_bot_worker() -> int:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[handler],
        force=True,
    )
    os.environ["PCBOT_GUI_CHILD"] = "1"
    try:
        import bot

        asyncio.run(bot.main())
    except SystemExit as error:
        if error.code not in (None, 0):
            logging.getLogger("pcbot").error("%s", error.code)
            return error.code if isinstance(error.code, int) else 1
    except Exception:
        logging.getLogger("pcbot").exception("Критическая ошибка процесса бота")
        return 1
    return 0


class ControlPanel:
    def __init__(self, auto_start: bool = False):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.overrideredirect(True)
        self.root.title("PC Bot — Панель управления")
        self.root.geometry("980x720")
        self.root.resizable(False, False)
        self.root.configure(fg_color="#292929")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        self.commands: queue.Queue[str] = queue.Queue()
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.state = "stopped"
        self.closing = False
        self.shutdown_complete = False
        self.log_offset = 0
        self.first_run = False

        self._create_widgets()
        self._start_tray_icon()
        self._apply_windows_glass()
        self._load_env_form()
        self._load_existing_logs()

        self.worker = threading.Thread(target=self._control_loop, daemon=True)
        self.worker.start()
        self.root.after(150, self._poll)
        self._refresh_autostart_button()
        self._refresh_controls()

        if auto_start and not self.first_run:
            self.root.after(350, self.start_bot)
            self.root.after(700, self.hide_to_tray)

    @staticmethod
    def _create_tray_image() -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        for y in range(64):
            ratio = y / 63
            shade = int(74 + (146 - 74) * ratio)
            color = (shade, shade, shade, 255)
            draw.line((0, y, 63, y), fill=color)
        mask = Image.new("L", (64, 64), 0)
        ImageDraw.Draw(mask).rounded_rectangle((2, 2, 61, 61), radius=19, fill=255)
        image.putalpha(mask)
        draw = ImageDraw.Draw(image)
        draw.ellipse((17, 17, 47, 47), outline=(255, 255, 255, 235), width=3)
        draw.ellipse((26, 26, 38, 38), fill=(255, 255, 255, 235))
        return image

    def _start_tray_icon(self) -> None:
        self.tray_icon = pystray.Icon(
            "PCBotControl",
            self._create_tray_image(),
            "PC Bot",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "Открыть панель",
                    lambda icon, item: self.root.after(0, self.show_window),
                    default=True,
                ),
                pystray.MenuItem(
                    "Запустить бота",
                    lambda icon, item: self.root.after(0, self.start_bot),
                ),
                pystray.MenuItem(
                    "Остановить бота",
                    lambda icon, item: self.root.after(0, self.stop_bot),
                ),
                pystray.MenuItem(
                    "Перезапустить бота",
                    lambda icon, item: self.root.after(0, self.restart_bot),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Выход",
                    lambda icon, item: self.root.after(0, self.exit_app),
                ),
            ),
        )
        self.tray_icon.run_detached()

    def _apply_windows_glass(self) -> None:
        if os.name != "nt":
            return
        self.root.update_idletasks()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        hwnd_value = self.root.winfo_id()
        hwnd = user32.GetParent(hwnd_value) or hwnd_value

        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwmapi.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        backdrop = ctypes.c_int(3)
        dwmapi.DwmSetWindowAttribute(
            hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
        )
        corners = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners)
        )

    def _begin_window_drag(self, event) -> None:
        self.drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_window(self, event) -> None:
        if hasattr(self, "drag_origin"):
            x = event.x_root - self.drag_origin[0]
            y = event.y_root - self.drag_origin[1]
            self.root.geometry(f"+{x}+{y}")

    def _create_widgets(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        page = ctk.CTkFrame(
            self.root,
            fg_color="transparent",
            corner_radius=0,
        )
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(1, weight=1)
        page.grid_configure(padx=22, pady=20)

        header = ctk.CTkFrame(page, fg_color="transparent", corner_radius=0)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(
            header,
            text="◈  PC BOT",
            text_color="#F4F4F4",
            font=("Segoe UI", 24, "bold"),
            anchor="w",
        )
        title.grid(row=0, column=0, sticky="w")
        subtitle = ctk.CTkLabel(
            header,
            text="Панель управления  ·  работает в системном трее",
            text_color="#B8B8B8",
            font=("Segoe UI", 10),
            anchor="w",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(3, 0))

        window_controls = ctk.CTkFrame(header, fg_color="transparent", corner_radius=0)
        window_controls.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(16, 0))
        ctk.CTkButton(
            window_controls,
            text="–",
            command=self.hide_to_tray,
            font=("Segoe UI", 16),
            fg_color="#424242",
            hover_color="#555555",
            text_color="#F4F4F4",
            corner_radius=18,
            width=40,
            height=36,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            window_controls,
            text="×",
            command=self.hide_to_tray,
            font=("Segoe UI", 16),
            fg_color="#424242",
            hover_color="#555555",
            text_color="#F4F4F4",
            corner_radius=18,
            width=40,
            height=36,
        ).pack(side="left")
        for widget in (header, title, subtitle):
            widget.bind("<ButtonPress-1>", self._begin_window_drag)
            widget.bind("<B1-Motion>", self._drag_window)

        self.notebook = ctk.CTkTabview(
            page,
            corner_radius=24,
            border_width=1,
            border_color="#858585",
            fg_color="#282828",
            segmented_button_fg_color="#202020",
            segmented_button_selected_color="#535353",
            segmented_button_selected_hover_color="#626262",
            segmented_button_unselected_color="#303030",
            segmented_button_unselected_hover_color="#414141",
            text_color="#F0F0F0",
            segmented_button_font=("Segoe UI", 10, "bold"),
        )
        self.notebook.grid(row=1, column=0, sticky="nsew")
        self.notebook.add("ПАНЕЛЬ")
        self.notebook.add("API / .ENV")
        self.main_tab = self.notebook.tab("ПАНЕЛЬ")
        self.settings_tab = self.notebook.tab("API / .ENV")
        self.main_tab.grid_columnconfigure(0, weight=1)
        self.main_tab.grid_rowconfigure(1, weight=1)
        self.main_tab.grid_configure(padx=14, pady=(12, 14))
        self.settings_tab.grid_columnconfigure(0, weight=1)
        self.settings_tab.grid_configure(padx=14, pady=(12, 14))

        controls = ctk.CTkFrame(
            self.main_tab,
            fg_color="#3A3A3A",
            border_color="#828282",
            border_width=1,
            corner_radius=24,
        )
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        controls.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            controls,
            text="● Выключен",
            text_color="#E4E4E4",
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        self.status_label.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 9))

        buttons = ctk.CTkFrame(controls, fg_color="transparent", corner_radius=0)
        buttons.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.start_button = ctk.CTkButton(
            buttons,
            text="▶  Запустить",
            fg_color="#168B62",
            hover_color="#22A777",
            text_color="#FFFFFF",
            corner_radius=18,
            height=42,
            font=("Segoe UI", 10, "bold"),
            command=self.start_bot,
        )
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ctk.CTkButton(
            buttons,
            text="■  Остановить",
            fg_color="#C9474F",
            hover_color="#E15D65",
            text_color="#FFFFFF",
            corner_radius=18,
            height=42,
            font=("Segoe UI", 10, "bold"),
            command=self.stop_bot,
        )
        self.stop_button.pack(side="left", padx=8)
        self.restart_button = ctk.CTkButton(
            buttons,
            text="↻  Перезапустить",
            fg_color="#4B4B4B",
            hover_color="#5D5D5D",
            text_color="#F0F0F0",
            corner_radius=18,
            height=42,
            font=("Segoe UI", 10, "bold"),
            command=self.restart_bot,
        )
        self.restart_button.pack(side="left", padx=8)
        self.autostart_button = ctk.CTkButton(
            buttons,
            text="Добавить в автозагрузку",
            fg_color="#424242",
            hover_color="#555555",
            text_color="#F0F0F0",
            corner_radius=18,
            height=42,
            font=("Segoe UI", 10, "bold"),
            command=self.toggle_autostart,
        )
        self.autostart_button.pack(side="right", padx=(8, 0))

        log_card = ctk.CTkFrame(
            self.main_tab,
            fg_color="#303030",
            border_color="#777777",
            border_width=1,
            corner_radius=24,
        )
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)
        log_header = ctk.CTkFrame(log_card, fg_color="transparent", corner_radius=0)
        log_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 9))
        log_header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            log_header,
            text="Логи бота",
            text_color="#F1F1F1",
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            log_header,
            text="logs\\bot.log",
            text_color="#B5B5B5",
            font=("Segoe UI", 9),
            anchor="e",
        ).grid(row=0, column=1, sticky="e")

        self.log_view = ctk.CTkTextbox(
            log_card,
            wrap="word",
            state="disabled",
            fg_color="#1D1D1D",
            text_color="#E0E0E0",
            border_color="#5F5F5F",
            scrollbar_button_color="#686868",
            scrollbar_button_hover_color="#818181",
            border_width=1,
            corner_radius=18,
            font=("Cascadia Mono", 9),
        )
        self.log_view.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        ctk.CTkLabel(
            self.main_tab,
            text="Закрытие окна скрывает панель в трей. Для полного выхода открой меню значка.",
            text_color="#B8B8B8",
            font=("Segoe UI", 9),
            anchor="w",
        ).grid(row=2, column=0, sticky="w", pady=(10, 0))

        self._create_settings_widgets()

    def _create_settings_widgets(self) -> None:
        self.settings_tab.grid_rowconfigure(0, weight=1)
        self.settings_content = ctk.CTkScrollableFrame(
            self.settings_tab,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_fg_color="#303030",
            scrollbar_button_color="#686868",
            scrollbar_button_hover_color="#818181",
        )
        self.settings_content.grid(row=0, column=0, sticky="nsew")
        self.settings_content.grid_columnconfigure(0, weight=1)

        self.settings_notice = ctk.CTkLabel(
            self.settings_content,
            text="",
            text_color="#C5C5C5",
            font=("Segoe UI", 10),
            wraplength=840,
            justify="left",
            anchor="w",
        )
        self.settings_notice.grid(row=0, column=0, sticky="ew", pady=(0, 10), padx=4)

        credentials = ctk.CTkFrame(
            self.settings_content,
            fg_color="#3A3A3A",
            border_color="#828282",
            border_width=1,
            corner_radius=24,
        )
        credentials.grid(row=1, column=0, sticky="new")
        credentials.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            credentials,
            text="Telegram и OpenRouter",
            text_color="#F1F1F1",
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(14, 8))

        self.env_vars = {
            key: tk.StringVar(master=self.root, value=value)
            for key, value in ENV_DEFAULTS.items()
        }
        self.show_secrets = tk.BooleanVar(master=self.root, value=False)
        self.secret_entries = []
        fields = (
            ("Токен Telegram", "BOT_TOKEN", True),
            ("Ваш Telegram ID", "ALLOWED_USER_IDS", False),
            ("Ключ OpenRouter (необязательно)", "OPENROUTER_API_KEY", True),
            ("Модель OpenRouter", "OPENROUTER_MODEL", False),
        )
        for row, (label, key, secret) in enumerate(fields, start=1):
            ctk.CTkLabel(
                credentials,
                text=label,
                text_color="#E4E4E4",
                font=("Segoe UI", 10),
                anchor="w",
            ).grid(
                row=row, column=0, sticky="w", padx=(18, 14), pady=5
            )
            entry = ctk.CTkEntry(
                credentials,
                textvariable=self.env_vars[key],
                show="*" if secret else "",
                fg_color="#202020",
                border_color="#686868",
                text_color="#F1F1F1",
                placeholder_text_color="#9C9C9C",
                corner_radius=15,
                height=36,
                font=("Segoe UI", 10),
            )
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 18), pady=5)
            if secret:
                self.secret_entries.append(entry)

        ctk.CTkLabel(
            credentials,
            text="Токен выдаёт @BotFather, ID можно узнать у @userinfobot. "
            "Ключ OpenRouter нужен только для команды /ask.",
            text_color="#B8B8B8",
            font=("Segoe UI", 9),
            wraplength=780,
            justify="left",
            anchor="w",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=18, pady=(7, 2))
        ctk.CTkCheckBox(
            credentials,
            text="Показать токены",
            variable=self.show_secrets,
            command=self._toggle_secret_visibility,
            text_color="#E4E4E4",
            fg_color="#777777",
            hover_color="#898989",
            border_color="#A0A0A0",
            checkmark_color="#FFFFFF",
            corner_radius=6,
            font=("Segoe UI", 9),
        ).grid(row=6, column=1, sticky="w", padx=(0, 18), pady=(6, 14))

        advanced = ctk.CTkFrame(
            self.settings_content,
            fg_color="#303030",
            border_color="#777777",
            border_width=1,
            corner_radius=24,
        )
        advanced.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        advanced.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            advanced,
            text="Дополнительные параметры",
            text_color="#F1F1F1",
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(13, 6))
        self.allow_shell = tk.BooleanVar(master=self.root, value=False)
        self.allow_files = tk.BooleanVar(master=self.root, value=False)
        checkbox_style = {
            "text_color": "#E4E4E4",
            "fg_color": "#777777",
            "hover_color": "#898989",
            "border_color": "#A0A0A0",
            "checkmark_color": "#FFFFFF",
            "corner_radius": 6,
            "font": ("Segoe UI", 9),
        }
        ctk.CTkCheckBox(
            advanced,
            text="Разрешить выполнение команд /run",
            variable=self.allow_shell,
            **checkbox_style,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=18, pady=3)
        ctk.CTkCheckBox(
            advanced,
            text="Разрешить получение файлов /get",
            variable=self.allow_files,
            **checkbox_style,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=18, pady=3)
        ctk.CTkLabel(
            advanced,
            text="Задержка выключения, секунд",
            text_color="#E4E4E4",
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=3, column=0, sticky="w", padx=(18, 14), pady=5)
        ctk.CTkEntry(
            advanced,
            textvariable=self.env_vars["POWER_DELAY"],
            fg_color="#202020",
            border_color="#686868",
            text_color="#F1F1F1",
            corner_radius=15,
            height=34,
            width=130,
            font=("Segoe UI", 10),
        ).grid(row=3, column=1, sticky="w", padx=(0, 18), pady=5)
        ctk.CTkLabel(
            advanced,
            text="Лимит команд в минуту",
            text_color="#E4E4E4",
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=4, column=0, sticky="w", padx=(18, 14), pady=5)
        ctk.CTkEntry(
            advanced,
            textvariable=self.env_vars["RATE_LIMIT"],
            fg_color="#202020",
            border_color="#686868",
            text_color="#F1F1F1",
            corner_radius=15,
            height=34,
            width=130,
            font=("Segoe UI", 10),
        ).grid(row=4, column=1, sticky="w", padx=(0, 18), pady=5)
        ctk.CTkLabel(
            advanced,
            text="Ключ подписи кнопок",
            text_color="#E4E4E4",
            font=("Segoe UI", 10),
            anchor="w",
        ).grid(row=5, column=0, sticky="w", padx=(18, 14), pady=5)
        signing_entry = ctk.CTkEntry(
            advanced,
            textvariable=self.env_vars["SIGNING_KEY"],
            fg_color="#202020",
            border_color="#686868",
            text_color="#F1F1F1",
            corner_radius=15,
            height=34,
            font=("Segoe UI", 10),
        )
        signing_entry.grid(row=5, column=1, sticky="ew", padx=(0, 18), pady=5)
        self.secret_entries.append(signing_entry)
        ctk.CTkLabel(
            advanced,
            text="Команды и доступ к файлам дают боту расширенный доступ к компьютеру. "
            "Оставляй их выключенными, если они не нужны. "
            "Ключ подписи защищает кнопки от подделки — если оставить поле пустым, "
            "бот выведет его из токена.",
            text_color="#C5C5C5",
            font=("Segoe UI", 9),
            wraplength=780,
            justify="left",
            anchor="w",
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=18, pady=(7, 13))

        actions = ctk.CTkFrame(
            self.settings_content, fg_color="transparent", corner_radius=0
        )
        actions.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.save_button = ctk.CTkButton(
            actions,
            text="Сохранить .env",
            fg_color="#4B4B4B",
            hover_color="#5D5D5D",
            text_color="#F0F0F0",
            corner_radius=18,
            height=40,
            font=("Segoe UI", 10, "bold"),
            command=self.save_env,
        )
        self.save_button.pack(side="left")
        self.save_restart_button = ctk.CTkButton(
            actions,
            text="Сохранить и перезапустить",
            fg_color="#4B4B4B",
            hover_color="#5D5D5D",
            text_color="#F0F0F0",
            corner_radius=18,
            height=40,
            font=("Segoe UI", 10, "bold"),
            command=lambda: self.save_env(restart=True),
        )
        self.save_restart_button.pack(side="left", padx=(8, 0))
        self.settings_status = ctk.CTkLabel(
            actions,
            text="",
            text_color="#B8B8B8",
            font=("Segoe UI", 9),
            wraplength=430,
            justify="left",
            anchor="w",
        )
        self.settings_status.pack(side="left", padx=(14, 0))

    def _toggle_secret_visibility(self) -> None:
        mask = "" if self.show_secrets.get() else "*"
        for entry in self.secret_entries:
            entry.configure(show=mask)

    def _load_env_form(self) -> None:
        try:
            values = read_env_values(ENV_FILE)
        except (OSError, UnicodeError) as error:
            self.first_run = True
            self.settings_notice.configure(
                text=f"Не удалось прочитать .env: {error}",
                text_color="#C5C5C5",
            )
            self.notebook.set("API / .ENV")
            return

        for key, default in ENV_DEFAULTS.items():
            value = values.get(key, default)
            if key in ("ALLOW_SHELL", "ALLOW_FILES"):
                variable = self.allow_shell if key == "ALLOW_SHELL" else self.allow_files
                variable.set(value.strip().lower() in ("1", "true", "yes", "on"))
            else:
                self.env_vars[key].set(value)

        self.first_run = not configuration_is_complete(values)
        if self.first_run:
            if ENV_FILE.exists():
                notice = "Заполни обязательные поля и сохрани настройки, чтобы запустить бота."
            else:
                notice = "Первый запуск: введи токен Telegram и свой ID, затем создай .env кнопкой сохранения."
            self.settings_notice.configure(text=notice, text_color="#C5C5C5")
            self.notebook.set("API / .ENV")
        else:
            self.settings_notice.configure(
                text="Изменения применятся после перезапуска бота.",
                text_color="#B8B8B8",
            )

    def save_env(self, restart: bool = False) -> None:
        token = self.env_vars["BOT_TOKEN"].get().strip()
        ids = [
            part.strip()
            for part in self.env_vars["ALLOWED_USER_IDS"].get().split(",")
            if part.strip()
        ]
        if not token:
            messagebox.showerror("Настройки", "Укажи токен Telegram.", parent=self.root)
            return
        if not ids or any(not part.isdecimal() for part in ids):
            messagebox.showerror(
                "Настройки",
                "Укажи один или несколько числовых Telegram ID через запятую.",
                parent=self.root,
            )
            return

        model = self.env_vars["OPENROUTER_MODEL"].get().strip()
        if not model:
            messagebox.showerror(
                "Настройки", "Укажи slug модели OpenRouter.", parent=self.root
            )
            return
        delay_text = self.env_vars["POWER_DELAY"].get().strip()
        if not delay_text.isdecimal() or int(delay_text) < 1:
            messagebox.showerror(
                "Настройки",
                "Задержка должна быть положительным целым числом секунд.",
                parent=self.root,
            )
            return
        rate_text = self.env_vars["RATE_LIMIT"].get().strip()
        if not rate_text.isdecimal() or int(rate_text) < 1:
            messagebox.showerror(
                "Настройки",
                "Лимит команд должен быть положительным целым числом.",
                parent=self.root,
            )
            return

        values = {
            "BOT_TOKEN": token,
            "ALLOWED_USER_IDS": ", ".join(ids),
            "OPENROUTER_API_KEY": self.env_vars["OPENROUTER_API_KEY"].get().strip(),
            "OPENROUTER_MODEL": model,
            "ALLOW_SHELL": str(self.allow_shell.get()).lower(),
            "ALLOW_FILES": str(self.allow_files.get()).lower(),
            "POWER_DELAY": delay_text,
            "SIGNING_KEY": self.env_vars["SIGNING_KEY"].get().strip(),
            "RATE_LIMIT": rate_text,
        }
        try:
            write_env_values(ENV_FILE, values)
        except OSError as error:
            messagebox.showerror(
                "Настройки", f"Не удалось сохранить .env:\n{error}", parent=self.root
            )
            return

        self.first_run = False
        self.settings_notice.configure(
            text="Настройки сохранены в .env. Секреты не выводятся в логи.",
            text_color="#B8B8B8",
        )
        self.settings_status.configure(
            text=(
                "Сохранено; бот перезапускается."
                if restart
                else "Сохранено. Перезапусти бота, чтобы применить."
            ),
            text_color="#B8B8B8",
        )
        self._append_log("Настройки .env сохранены.\n")
        if restart:
            self.restart_bot()

    def _startup_path(self) -> Path:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("Не удалось определить папку профиля Windows.")
        return (
            Path(appdata)
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / AUTOSTART_NAME
        )

    def _refresh_autostart_button(self) -> None:
        try:
            enabled = self._startup_path().exists()
        except RuntimeError:
            enabled = False
        self.autostart_button.configure(
            text="Убрать из автозагрузки" if enabled else "Добавить в автозагрузку"
        )

    def toggle_autostart(self) -> None:
        try:
            path = self._startup_path()
            if path.exists():
                path.unlink()
                self._append_log("Автозапуск панели и бота отключён.\n")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if getattr(sys, "frozen", False):
                    command = f'start "" "{sys.executable}" --autostart'
                else:
                    script = Path(__file__).resolve()
                    command = f'start "" "{sys.executable}" "{script}" --autostart'
                path.write_text(f"@echo off\r\n{command}\r\n", encoding="utf-8")
                self._append_log("Автозапуск панели и бота включён.\n")
            self._refresh_autostart_button()
        except OSError as error:
            messagebox.showerror("Автозагрузка", f"Не удалось изменить настройку:\n{error}")
        except RuntimeError as error:
            messagebox.showerror("Автозагрузка", str(error))

    def _worker_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--bot"]
        return [sys.executable, str(Path(__file__).resolve()), "--bot"]

    def _launch_worker(self) -> subprocess.Popen | None:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            for key in ENV_DEFAULTS:
                env.pop(key, None)
            env["PCBOT_GUI_CHILD"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen(
                self._worker_command(),
                cwd=str(APP_DIR),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self.events.put(("error", f"Не удалось запустить бота: {error}"))
            self.events.put(("state", "error"))
            return None
        self.events.put(("state", "running"))
        return process

    @staticmethod
    def _stop_worker(process: subprocess.Popen | None) -> None:
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            if process.poll() is not None:
                return
            raise
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("Процесс бота не завершился после остановки.") from error

    def _stop_managed_worker(self, process: subprocess.Popen | None) -> bool:
        try:
            self._stop_worker(process)
            return True
        except (OSError, RuntimeError) as error:
            self.events.put(("error", f"Не удалось остановить бота: {error}"))
            return process is None or process.poll() is not None

    def _control_loop(self) -> None:
        process = None
        while True:
            try:
                command = self.commands.get(timeout=0.2)
            except queue.Empty:
                command = ""

            if command == "start":
                if process is not None and process.poll() is None:
                    self.events.put(("state", "running"))
                else:
                    process = self._launch_worker()
            elif command == "stop":
                self.events.put(("state", "stopping"))
                if self._stop_managed_worker(process):
                    process = None
                    self.events.put(("state", "stopped"))
                else:
                    self.events.put(("state", "error"))
            elif command == "restart":
                self.events.put(("state", "stopping"))
                if self._stop_managed_worker(process):
                    process = self._launch_worker()
                else:
                    self.events.put(("state", "error"))
            elif command == "shutdown":
                if self._stop_managed_worker(process):
                    self.events.put(("shutdown_done", None))
                    return
                self.events.put(("shutdown_failed", None))

            if process is not None and process.poll() is not None:
                exit_code = process.returncode
                process = None
                self.events.put(("state", "stopped"))
                if exit_code:
                    self.events.put(("notice", f"Процесс бота завершился с кодом {exit_code}."))

    def _refresh_controls(self) -> None:
        colors = {
            "running": ("● Работает", "#E4E4E4"),
            "starting": ("● Запускается…", "#E4E4E4"),
            "stopping": ("● Останавливается…", "#E4E4E4"),
            "error": ("● Ошибка запуска", "#E4E4E4"),
            "stopped": ("● Выключен", "#E4E4E4"),
        }
        text, color = colors.get(self.state, colors["stopped"])
        self.status_label.configure(text=text, text_color=color)
        busy = self.state in ("starting", "stopping") or self.closing
        self.start_button.configure(
            state="disabled" if busy or self.state == "running" else "normal"
        )
        self.stop_button.configure(
            state="normal"
            if self.state in ("running", "error") and not self.closing
            else "disabled"
        )
        self.restart_button.configure(state="disabled" if busy else "normal")

    def _set_state(self, state: str) -> None:
        self.state = state
        self._refresh_controls()

    def _append_log(self, text: str) -> None:
        self.log_view.configure(state="normal")
        self.log_view.insert("end", text)
        line_count = int(self.log_view.index("end-1c").split(".")[0])
        if line_count > 2500:
            self.log_view.delete("1.0", f"{line_count - 2500}.0")
        self.log_view.see("end")
        self.log_view.configure(state="disabled")

    def _load_existing_logs(self) -> None:
        if not LOG_FILE.exists():
            return
        try:
            data = LOG_FILE.read_bytes()
            self.log_offset = len(data)
            lines = data.decode("utf-8", errors="replace").splitlines(keepends=True)
            self._append_log("".join(lines[-400:]))
        except OSError as error:
            self._append_log(f"Не удалось прочитать файл логов: {error}\n")

    def _read_new_logs(self) -> None:
        try:
            size = LOG_FILE.stat().st_size
            if size < self.log_offset:
                self.log_offset = 0
            if size == self.log_offset:
                return
            with LOG_FILE.open("rb") as log_file:
                log_file.seek(self.log_offset)
                data = log_file.read()
                self.log_offset = log_file.tell()
            if data:
                self._append_log(data.decode("utf-8", errors="replace"))
        except FileNotFoundError:
            return
        except OSError as error:
            self._append_log(f"Не удалось прочитать файл логов: {error}\n")

    def _poll(self) -> None:
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "state":
                self._set_state(str(value))
            elif event == "error":
                self._append_log(f"ОШИБКА: {value}\n")
            elif event == "notice":
                self._append_log(f"{value}\n")
            elif event == "shutdown_done":
                self.shutdown_complete = True
                self.tray_icon.stop()
            elif event == "shutdown_failed":
                self.closing = False
                self._set_state("error")
                messagebox.showerror(
                    "Ошибка",
                    "Не удалось безопасно остановить процесс бота. "
                    "Проверьте логи и попробуйте ещё раз.",
                    parent=self.root,
                )

        self._read_new_logs()
        if self.closing:
            if self.shutdown_complete:
                self.root.destroy()
            else:
                self.root.after(100, self._poll)
        else:
            self.root.after(150, self._poll)

    def start_bot(self) -> None:
        if self.state in ("running", "starting", "stopping") or self.closing:
            return
        if self.first_run:
            self.notebook.set("API / .ENV")
            self.settings_notice.configure(
                text="Сначала заполни обязательные поля и сохрани настройки .env.",
                text_color="#C5C5C5",
            )
            return
        self._set_state("starting")
        self.commands.put("start")

    def stop_bot(self) -> None:
        if self.state not in ("running", "error") or self.closing:
            return
        self._set_state("stopping")
        self.commands.put("stop")

    def restart_bot(self) -> None:
        if self.state in ("starting", "stopping") or self.closing:
            return
        self._set_state("starting")
        self.commands.put("restart")

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_to_tray(self) -> None:
        self.root.withdraw()

    def exit_app(self) -> None:
        if self.closing:
            return
        if self.state in ("running", "starting", "stopping"):
            should_close = messagebox.askyesno(
                "Выход",
                "Остановить бота и закрыть приложение?",
                parent=self.root,
            )
            if not should_close:
                return
        self.closing = True
        self.root.withdraw()
        self._set_state("stopping")
        self.commands.put("shutdown")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    if "--bot" in sys.argv:
        raise SystemExit(run_bot_worker())
    instance = SingleInstance(LOCK_FILE)
    if not instance.acquire():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "PC Bot",
            "Панель управления уже запущена.\n"
            "Посмотри значок в системном трее.",
            parent=root,
        )
        root.destroy()
        raise SystemExit(0)
    ControlPanel(auto_start="--autostart" in sys.argv).run()
