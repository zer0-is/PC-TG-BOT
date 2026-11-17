"""Графическая панель PC Bot на PySide6.

Управление ботом и его логами, редактирование `.env`, значок в трее.
Запускается в Windows и хранит настройки в отдельном `.env` рядом
с приложением. Режим ``--bot`` превращает процесс в безграфического
работника бота — так панель запускает его отдельным процессом.
"""

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
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pystray
from dotenv import dotenv_values
from PIL import Image, ImageDraw
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

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
    # Удалённый ввод (мышь и клавиатура) и запись экрана/микрофона.
    "ALLOW_INPUT": "false",
    "ALLOW_AUDIO": "false",
    # Сколько секунд писать по умолчанию.
    "RECORD_SECONDS": "10",
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

# ------------------------------------------------------------------ палитра

BG = "#1B1B1F"
CARD = "#232329"
CARD_INNER = "#1E1E23"
FIELD = "#16161A"
BORDER = "#33333B"
TEXT = "#F2F2F4"
MUTED = "#9A9AA4"
FAINT = "#6E6E78"
ACCENT = "#5B8DEF"
GREEN = "#3FBF7F"
GREEN_HOVER = "#4BD08D"
RED = "#E5484D"
RED_HOVER = "#EF5A5F"
NEUTRAL = "#2E2E36"
NEUTRAL_HOVER = "#3A3A44"

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}

QFrame#card, QFrame#titleBar {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QFrame#titleBar {{ border: none; border-radius: 0; }}
QLabel#appTitle {{ font-size: 17pt; font-weight: 600; color: {TEXT}; }}
QLabel#appSubtitle {{ color: {MUTED}; font-size: 10pt; }}
QLabel#cardTitle {{ font-size: 12pt; font-weight: 600; color: {TEXT}; }}
QLabel#muted {{ color: {MUTED}; font-size: 9pt; }}
QLabel#faint {{ color: {FAINT}; font-size: 9pt; }}

QPushButton {{ border: none; border-radius: 12px; padding: 0 18px; }}
QPushButton#nav {{
    text-align: left; padding: 0 16px; min-height: 42px;
    color: {MUTED}; background: transparent; border-radius: 12px;
    font-size: 11pt;
}}
QPushButton#nav:hover {{ background: {CARD}; color: {TEXT}; }}
QPushButton#nav:checked {{ background: {CARD}; color: {TEXT}; font-weight: 600; }}

QPushButton#start, QPushButton#stop, QPushButton#neutral, QPushButton#danger {{
    min-height: 42px; font-weight: 600;
}}
QPushButton#start {{ background: {GREEN}; color: #0B1F16; }}
QPushButton#start:hover {{ background: {GREEN_HOVER}; }}
QPushButton#start:disabled {{ background: {NEUTRAL}; color: {FAINT}; }}
QPushButton#stop {{ background: {RED}; color: #FFF4F4; }}
QPushButton#stop:hover {{ background: {RED_HOVER}; }}
QPushButton#stop:disabled {{ background: {NEUTRAL}; color: {FAINT}; }}
QPushButton#neutral {{ background: {NEUTRAL}; color: {TEXT}; }}
QPushButton#neutral:hover {{ background: {NEUTRAL_HOVER}; }}
QPushButton#neutral:disabled {{ color: {FAINT}; }}
QPushButton#dangerFlat {{
    background: transparent; color: {MUTED}; min-height: 36px;
}}
QPushButton#dangerFlat:hover {{ color: {RED}; }}

QPushButton#titleButton, QPushButton#closeButton {{
    background: {NEUTRAL}; color: {TEXT};
    min-width: 38px; max-width: 38px; min-height: 34px; max-height: 34px;
    font-size: 13pt;
}}
QPushButton#titleButton:hover {{ background: {NEUTRAL_HOVER}; }}
QPushButton#closeButton:hover {{ background: {RED}; color: #FFFFFF; }}

QLineEdit {{
    background: {FIELD}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 0 12px; min-height: 36px; color: {TEXT};
    selection-background-color: {ACCENT};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit[echoMode="2"] {{ letter-spacing: 1px; }}

QCheckBox {{ spacing: 10px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border-radius: 5px;
    border: 1px solid #4A4A54; background: {FIELD};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}

QPlainTextEdit#logView {{
    background: {CARD_INNER}; border: 1px solid {BORDER}; border-radius: 12px;
    color: {TEXT}; padding: 8px;
}}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #3C3C46; border-radius: 4px; min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{ background: #4A4A56; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""


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


# ------------------------------------------------------------------ процесс бота


class BotWorker(QObject):
    """Живёт в отдельном потоке и управляет процессом бота.

    Сигналы безопасно испускать из чужого потока: Qt сам доставит их
    в поток интерфейса.
    """

    stateChanged = Signal(str)
    errorOccurred = Signal(str)
    notice = Signal(str)
    shutdownDone = Signal()
    shutdownFailed = Signal()

    def __init__(self):
        super().__init__()
        self._commands: queue.Queue[str] = queue.Queue()
        self._process: subprocess.Popen | None = None

    def start(self) -> None:
        self._commands.put("start")

    def stop(self) -> None:
        self._commands.put("stop")

    def restart(self) -> None:
        self._commands.put("restart")

    def shutdown(self) -> None:
        self._commands.put("shutdown")

    def run(self) -> None:
        while True:
            try:
                command = self._commands.get(timeout=0.2)
            except queue.Empty:
                command = ""

            if command == "start":
                if self._process is not None and self._process.poll() is None:
                    self.stateChanged.emit("running")
                else:
                    self._launch()
            elif command == "stop":
                self.stateChanged.emit("stopping")
                if self._terminate():
                    self._process = None
                    self.stateChanged.emit("stopped")
                else:
                    self.stateChanged.emit("error")
            elif command == "restart":
                self.stateChanged.emit("stopping")
                if self._terminate():
                    self._launch()
                else:
                    self.stateChanged.emit("error")
            elif command == "shutdown":
                if self._terminate():
                    self.shutdownDone.emit()
                    return
                self.shutdownFailed.emit()

            if self._process is not None and self._process.poll() is not None:
                exit_code = self._process.returncode
                self._process = None
                self.stateChanged.emit("stopped")
                if exit_code:
                    self.notice.emit(f"Процесс бота завершился с кодом {exit_code}.")

    def _worker_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--bot"]
        return [sys.executable, str(Path(__file__).resolve()), "--bot"]

    def _launch(self) -> None:
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            for key in ENV_DEFAULTS:
                env.pop(key, None)
            env["PCBOT_GUI_CHILD"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            self._process = subprocess.Popen(
                self._worker_command(),
                cwd=str(APP_DIR),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self.errorOccurred.emit(f"Не удалось запустить бота: {error}")
            self.stateChanged.emit("error")
            return
        self.stateChanged.emit("running")

    def _terminate(self) -> bool:
        process = self._process
        if process is None or process.poll() is not None:
            return True
        try:
            process.terminate()
        except OSError:
            if process.poll() is not None:
                return True
            raise
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired as error:
                self.errorOccurred.emit("Процесс бота не завершился после остановки.")
                raise RuntimeError("Процесс бота не завершился.") from error
        return True

    def _terminate_safe(self) -> bool:
        try:
            return self._terminate()
        except (OSError, RuntimeError):
            return self._process is None or self._process.poll() is not None


class TrayBridge(QObject):
    """Мост между потоком значка в трее и потоком интерфейса."""

    openPanel = Signal()
    startRequested = Signal()
    stopRequested = Signal()
    restartRequested = Signal()
    exitRequested = Signal()


class TitleBar(QFrame):
    """Строка заголовка без рамки: перетаскивание и кнопки окна."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self._drag_offset = None

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt API
        self._drag_offset = None
        super().mouseReleaseEvent(event)


def create_tray_image() -> Image.Image:
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for y in range(64):
        ratio = y / 63
        shade = int(56 + (112 - 56) * ratio)
        color = (shade, shade, shade, 255)
        draw.line((0, y, 63, y), fill=color)
    mask = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(mask).rounded_rectangle((2, 2, 61, 61), radius=19, fill=255)
    image.putalpha(mask)
    draw = ImageDraw.Draw(image)
    draw.ellipse((17, 17, 47, 47), outline=(235, 235, 235, 240), width=3)
    draw.ellipse((26, 26, 38, 38), fill=(91, 141, 239, 255))
    return image


class ControlPanel(QMainWindow):
    STATE_COLORS = {
        "running": GREEN,
        "starting": "#E0B34C",
        "stopping": "#E0B34C",
        "error": RED,
        "stopped": MUTED,
    }
    STATE_TEXTS = {
        "running": "Работает",
        "starting": "Запускается…",
        "stopping": "Останавливается…",
        "error": "Ошибка запуска",
        "stopped": "Выключен",
    }

    def __init__(self, auto_start: bool = False):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setFixedSize(980, 720)
        self.setWindowTitle("PC Bot — Панель управления")

        self.state = "stopped"
        self.closing = False
        self.first_run = False
        self.log_offset = 0

        self.worker = BotWorker()
        self.worker_thread = QThread(self)
        self.worker.moveToThread(self.worker_thread)
        self.worker.stateChanged.connect(self.set_state)
        self.worker.errorOccurred.connect(self.append_error)
        self.worker.notice.connect(lambda text: self.append_log(text + "\n"))
        self.worker.shutdownDone.connect(self._finish_exit)
        self.worker.shutdownFailed.connect(self._exit_failed)
        self.worker_thread.started.connect(self.worker.run)
        self.worker_thread.start()

        self._build_ui()
        self._start_tray()
        self._apply_windows_rounding()
        self._load_env_form()
        self._load_existing_logs()
        self._refresh_autostart_button()
        self._refresh_controls()

        self.log_timer = QTimer(self)
        self.log_timer.setInterval(700)
        self.log_timer.timeout.connect(self._read_new_logs)
        self.log_timer.start()

        if auto_start and not self.first_run:
            QTimer.singleShot(350, self.start_bot)
            QTimer.singleShot(700, self.hide_to_tray)

    # ---------------------------------------------------------- интерфейс

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(12)

        root.addWidget(self._build_title_bar())
        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_sidebar(), 0)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_home_page())
        self.pages.addWidget(self._build_settings_page())
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

    def _build_title_bar(self) -> QFrame:
        bar = TitleBar(self)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 4)
        layout.setSpacing(8)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        title = QLabel("◈  PC BOT")
        title.setObjectName("appTitle")
        subtitle = QLabel("Панель управления  ·  работает в системном трее")
        subtitle.setObjectName("appSubtitle")
        titles.addWidget(title)
        titles.addWidget(subtitle)
        layout.addLayout(titles)
        layout.addStretch(1)

        minimize = QPushButton("–")
        minimize.setObjectName("titleButton")
        minimize.setToolTip("Свернуть в трей")
        minimize.clicked.connect(self.hide_to_tray)
        close = QPushButton("×")
        close.setObjectName("closeButton")
        close.setToolTip("Свернуть в трей")
        close.clicked.connect(self.hide_to_tray)
        layout.addWidget(minimize)
        layout.addWidget(close)
        return bar

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("card")
        sidebar.setFixedWidth(200)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(6)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for index, (icon, label) in enumerate(
            (("⌂", "Панель"), ("⚙", "Настройки"))
        ):
            button = QPushButton(f"{icon}   {label}")
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.nav_group.addButton(button, index)
            button.clicked.connect(lambda checked=False, i=index: self.pages.setCurrentIndex(i))
            layout.addWidget(button)

        layout.addStretch(1)
        version = QLabel("Windows 10/11\nPython 3.13")
        version.setObjectName("faint")
        version.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        layout.addWidget(version)
        return sidebar

    def _card(self, title: str | None = None) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setObjectName("cardTitle")
            layout.addWidget(label)
        return card, layout

    def _build_home_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        status_card, status_layout = self._card()
        self.status_label = QLabel()
        self.status_label.setObjectName("cardTitle")
        status_layout.addWidget(self.status_label)
        layout.addWidget(status_card)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(10)
        self.start_button = QPushButton("▶   Запустить")
        self.start_button.setObjectName("start")
        self.start_button.clicked.connect(self.start_bot)
        self.stop_button = QPushButton("■   Остановить")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self.stop_bot)
        self.restart_button = QPushButton("↻   Перезапустить")
        self.restart_button.setObjectName("neutral")
        self.restart_button.clicked.connect(self.restart_bot)
        self.autostart_button = QPushButton("Добавить в автозагрузку")
        self.autostart_button.setObjectName("neutral")
        self.autostart_button.clicked.connect(self.toggle_autostart)
        for button in (
            self.start_button,
            self.stop_button,
            self.restart_button,
        ):
            buttons_row.addWidget(button)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.autostart_button)
        layout.addLayout(buttons_row)

        log_card, log_layout = self._card()
        header = QHBoxLayout()
        log_title = QLabel("Логи бота")
        log_title.setObjectName("cardTitle")
        log_path = QLabel(str(LOG_FILE.relative_to(APP_DIR)))
        log_path.setObjectName("muted")
        header.addWidget(log_title)
        header.addStretch(1)
        header.addWidget(log_path)
        log_layout.addLayout(header)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2500)
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.log_view.setFont(font)
        log_layout.addWidget(self.log_view, 1)
        layout.addWidget(log_card, 1)

        hint = QLabel("Закрытие окна скрывает панель в трей. Для полного выхода открой меню значка.")
        hint.setObjectName("faint")
        layout.addWidget(hint)
        return page

    def _build_settings_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 12, 4)
        layout.setSpacing(12)

        self.settings_notice = QLabel("")
        self.settings_notice.setObjectName("muted")
        self.settings_notice.setWordWrap(True)
        layout.addWidget(self.settings_notice)

        credentials_card, credentials = self._card("Telegram и OpenRouter")
        self.env_edits: dict[str, QLineEdit] = {}
        self.show_secrets = QCheckBox("Показать токены")
        self.show_secrets.toggled.connect(self._toggle_secret_visibility)
        self.secret_edits: list[QLineEdit] = []
        fields = (
            ("Токен Telegram", "BOT_TOKEN", True),
            ("Ваш Telegram ID", "ALLOWED_USER_IDS", False),
            ("Ключ OpenRouter (необязательно)", "OPENROUTER_API_KEY", True),
            ("Модель OpenRouter", "OPENROUTER_MODEL", False),
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for row, (label, key, secret) in enumerate(fields):
            grid.addWidget(QLabel(label), row, 0)
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
            self.env_edits[key] = edit
            if secret:
                self.secret_edits.append(edit)
            grid.addWidget(edit, row, 1)
        credentials.addLayout(grid)

        note = QLabel(
            "Токен выдаёт @BotFather, ID можно узнать у @userinfobot. "
            "Ключ OpenRouter нужен только для команды /ask."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        credentials.addWidget(note)
        credentials.addWidget(self.show_secrets)
        layout.addWidget(credentials_card)

        advanced_card, advanced = self._card("Дополнительные параметры")
        self.allow_shell = QCheckBox("Разрешить выполнение команд /run")
        self.allow_files = QCheckBox("Разрешить получение файлов /get")
        self.allow_input = QCheckBox("Разрешить удалённый ввод: мышь и клавиатура")
        self.allow_audio = QCheckBox("Разрешить запись экрана и микрофона")
        for checkbox in (self.allow_shell, self.allow_files, self.allow_input, self.allow_audio):
            advanced.addWidget(checkbox)

        numeric_grid = QGridLayout()
        numeric_grid.setHorizontalSpacing(14)
        numeric_grid.setVerticalSpacing(8)
        numeric_fields = (
            ("Задержка выключения, секунд", "POWER_DELAY"),
            ("Длительность записи, секунд", "RECORD_SECONDS"),
            ("Лимит команд в минуту", "RATE_LIMIT"),
        )
        for row, (label, key) in enumerate(numeric_fields):
            numeric_grid.addWidget(QLabel(label), row, 0)
            edit = QLineEdit()
            edit.setFixedWidth(130)
            self.env_edits[key] = edit
            numeric_grid.addWidget(edit, row, 1)
        numeric_grid.addWidget(QLabel("Ключ подписи кнопок"), 3, 0)
        signing_edit = QLineEdit()
        signing_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.env_edits["SIGNING_KEY"] = signing_edit
        self.secret_edits.append(signing_edit)
        numeric_grid.addWidget(signing_edit, 3, 1)
        advanced.addLayout(numeric_grid)

        advanced_note = QLabel(
            "Команды, файлы, ввод и запись дают боту расширенный доступ к компьютеру. "
            "Оставляй их выключенными, если они не нужны. Ключ подписи защищает кнопки "
            "от подделки — если оставить поле пустым, бот выведет его из токена."
        )
        advanced_note.setObjectName("muted")
        advanced_note.setWordWrap(True)
        advanced.addWidget(advanced_note)
        layout.addWidget(advanced_card)

        save_row = QHBoxLayout()
        save_row.setSpacing(10)
        save = QPushButton("Сохранить .env")
        save.setObjectName("neutral")
        save.clicked.connect(lambda: self.save_env(restart=False))
        save_restart = QPushButton("Сохранить и перезапустить")
        save_restart.setObjectName("start")
        save_restart.clicked.connect(lambda: self.save_env(restart=True))
        save_row.addWidget(save)
        save_row.addWidget(save_restart)
        save_row.addStretch(1)
        layout.addLayout(save_row)

        self.save_status = QLabel("")
        self.save_status.setObjectName("muted")
        self.save_status.setWordWrap(True)
        layout.addWidget(self.save_status)
        layout.addStretch(1)

        scroll.setWidget(inner)
        return scroll

    def _apply_windows_rounding(self) -> None:
        """Скругляет окно средствами Windows 11; на Windows 10 остаётся прямым."""
        if os.name != "nt":
            return
        try:
            dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            dwmapi.DwmSetWindowAttribute.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
            hwnd = ctypes.c_void_p(int(self.winId()))
            corners = ctypes.c_int(2)  # DWMWCP_ROUND
            dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners)
            )
            backdrop = ctypes.c_int(3)  # DWMSBT_ACRYLIC
            dwmapi.DwmSetWindowAttribute(
                hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
            )
        except OSError:
            pass

    # ---------------------------------------------------------- трей

    def _start_tray(self) -> None:
        self.tray_bridge = TrayBridge()
        self.tray_bridge.openPanel.connect(self.show_window)
        self.tray_bridge.startRequested.connect(self.start_bot)
        self.tray_bridge.stopRequested.connect(self.stop_bot)
        self.tray_bridge.restartRequested.connect(self.restart_bot)
        self.tray_bridge.exitRequested.connect(self.exit_app)
        self.tray_icon = pystray.Icon(
            "PCBotControl",
            create_tray_image(),
            "PC Bot",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "Открыть панель",
                    lambda icon, item: self.tray_bridge.openPanel.emit(),
                    default=True,
                ),
                pystray.MenuItem(
                    "Запустить бота",
                    lambda icon, item: self.tray_bridge.startRequested.emit(),
                ),
                pystray.MenuItem(
                    "Остановить бота",
                    lambda icon, item: self.tray_bridge.stopRequested.emit(),
                ),
                pystray.MenuItem(
                    "Перезапустить бота",
                    lambda icon, item: self.tray_bridge.restartRequested.emit(),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Выход",
                    lambda icon, item: self.tray_bridge.exitRequested.emit(),
                ),
            ),
        )
        self.tray_icon.run_detached()

    # ---------------------------------------------------------- состояние

    def set_state(self, state: str) -> None:
        self.state = state
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        color = self.STATE_COLORS.get(self.state, MUTED)
        text = self.STATE_TEXTS.get(self.state, self.STATE_TEXTS["stopped"])
        self.status_label.setText(
            f'<span style="color:{color}; font-size:13pt;">●</span>  {text}'
        )
        busy = self.state in ("starting", "stopping") or self.closing
        self.start_button.setEnabled(not busy and self.state != "running")
        self.stop_button.setEnabled(
            self.state in ("running", "error") and not self.closing
        )
        self.restart_button.setEnabled(not busy)

    def append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text.rstrip("\n"))

    def append_error(self, text: str) -> None:
        self.append_log(f"ОШИБКА: {text}")

    def _load_existing_logs(self) -> None:
        if not LOG_FILE.exists():
            return
        try:
            data = LOG_FILE.read_bytes()
            self.log_offset = len(data)
            lines = data.decode("utf-8", errors="replace").splitlines(keepends=True)
            self.append_log("".join(lines[-400:]))
        except OSError as error:
            self.append_log(f"Не удалось прочитать файл логов: {error}\n")

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
                self.append_log(data.decode("utf-8", errors="replace"))
        except FileNotFoundError:
            return
        except OSError as error:
            self.append_log(f"Не удалось прочитать файл логов: {error}\n")

    # ---------------------------------------------------------- настройки

    def _load_env_form(self) -> None:
        try:
            values = read_env_values(ENV_FILE)
        except (OSError, UnicodeError) as error:
            self.first_run = True
            self.settings_notice.setText(f"Не удалось прочитать .env: {error}")
            self.pages.setCurrentIndex(1)
            return

        checkbox_vars = {
            "ALLOW_SHELL": self.allow_shell,
            "ALLOW_FILES": self.allow_files,
            "ALLOW_INPUT": self.allow_input,
            "ALLOW_AUDIO": self.allow_audio,
        }
        for key, default in ENV_DEFAULTS.items():
            value = values.get(key, default)
            if key in checkbox_vars:
                checkbox_vars[key].setChecked(value.strip().lower() in ("1", "true", "yes", "on"))
            else:
                self.env_edits[key].setText(value)

        self.first_run = not configuration_is_complete(values)
        if self.first_run:
            if ENV_FILE.exists():
                notice = "Заполни обязательные поля и сохрани настройки, чтобы запустить бота."
            else:
                notice = (
                    "Первый запуск: введи токен Telegram и свой ID, "
                    "затем создай .env кнопкой сохранения."
                )
            self.settings_notice.setText(notice)
            self.pages.setCurrentIndex(1)
        else:
            self.settings_notice.setText("Изменения применятся после перезапуска бота.")

    def _toggle_secret_visibility(self) -> None:
        mode = QLineEdit.EchoMode.Normal if self.show_secrets.isChecked() else QLineEdit.EchoMode.Password
        for edit in self.secret_edits:
            edit.setEchoMode(mode)

    def save_env(self, restart: bool) -> None:
        token = self.env_edits["BOT_TOKEN"].text().strip()
        ids = [
            part.strip()
            for part in self.env_edits["ALLOWED_USER_IDS"].text().split(",")
            if part.strip()
        ]
        if not token:
            QMessageBox.warning(self, "Настройки", "Укажи токен Telegram.")
            return
        if not ids or any(not part.isdecimal() for part in ids):
            QMessageBox.warning(
                self,
                "Настройки",
                "Укажи один или несколько числовых Telegram ID через запятую.",
            )
            return

        model = self.env_edits["OPENROUTER_MODEL"].text().strip()
        if not model:
            QMessageBox.warning(self, "Настройки", "Укажи slug модели OpenRouter.")
            return
        delay_text = self.env_edits["POWER_DELAY"].text().strip()
        if not delay_text.isdecimal() or int(delay_text) < 1:
            QMessageBox.warning(
                self,
                "Настройки",
                "Задержка должна быть положительным целым числом секунд.",
            )
            return
        rate_text = self.env_edits["RATE_LIMIT"].text().strip()
        if not rate_text.isdecimal() or int(rate_text) < 1:
            QMessageBox.warning(
                self,
                "Настройки",
                "Лимит команд должен быть положительным целым числом.",
            )
            return
        record_text = self.env_edits["RECORD_SECONDS"].text().strip()
        if not record_text.isdecimal() or not 1 <= int(record_text) <= 120:
            QMessageBox.warning(
                self,
                "Настройки",
                "Длительность записи — целое число секунд от 1 до 120.",
            )
            return

        values = {
            "BOT_TOKEN": token,
            "ALLOWED_USER_IDS": ", ".join(ids),
            "OPENROUTER_API_KEY": self.env_edits["OPENROUTER_API_KEY"].text().strip(),
            "OPENROUTER_MODEL": model,
            "ALLOW_SHELL": str(self.allow_shell.isChecked()).lower(),
            "ALLOW_FILES": str(self.allow_files.isChecked()).lower(),
            "ALLOW_INPUT": str(self.allow_input.isChecked()).lower(),
            "ALLOW_AUDIO": str(self.allow_audio.isChecked()).lower(),
            "RECORD_SECONDS": record_text,
            "POWER_DELAY": delay_text,
            "SIGNING_KEY": self.env_edits["SIGNING_KEY"].text().strip(),
            "RATE_LIMIT": rate_text,
        }
        try:
            write_env_values(ENV_FILE, values)
        except OSError as error:
            QMessageBox.warning(self, "Настройки", f"Не удалось сохранить .env:\n{error}")
            return

        self.first_run = False
        self.settings_notice.setText("Настройки сохранены в .env. Секреты не выводятся в логи.")
        self.save_status.setText(
            "Сохранено; бот перезапускается."
            if restart
            else "Сохранено. Перезапусти бота, чтобы применить."
        )
        self.append_log("Настройки .env сохранены.")
        if restart:
            self.restart_bot()

    # ---------------------------------------------------------- автозагрузка

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
        self.autostart_button.setText(
            "Убрать из автозагрузки" if enabled else "Добавить в автозагрузку"
        )

    def toggle_autostart(self) -> None:
        try:
            path = self._startup_path()
            if path.exists():
                path.unlink()
                self.append_log("Автозапуск панели и бота отключён.")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                if getattr(sys, "frozen", False):
                    command = f'start "" "{sys.executable}" --autostart'
                else:
                    script = Path(__file__).resolve()
                    command = f'start "" "{sys.executable}" "{script}" --autostart'
                path.write_text(f"@echo off\r\n{command}\r\n", encoding="utf-8")
                self.append_log("Автозапуск панели и бота включён.")
            self._refresh_autostart_button()
        except OSError as error:
            QMessageBox.warning(self, "Автозагрузка", f"Не удалось изменить настройку:\n{error}")
        except RuntimeError as error:
            QMessageBox.warning(self, "Автозагрузка", str(error))

    # ---------------------------------------------------------- управление ботом

    def start_bot(self) -> None:
        if self.state in ("running", "starting", "stopping") or self.closing:
            return
        if self.first_run:
            self.pages.setCurrentIndex(1)
            self.settings_notice.setText(
                "Сначала заполни обязательные поля и сохрани настройки .env."
            )
            return
        self.set_state("starting")
        self.worker.start()

    def stop_bot(self) -> None:
        if self.state not in ("running", "error") or self.closing:
            return
        self.set_state("stopping")
        self.worker.stop()

    def restart_bot(self) -> None:
        if self.state in ("starting", "stopping") or self.closing:
            return
        self.set_state("starting")
        self.worker.restart()

    # ---------------------------------------------------------- окно и выход

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self) -> None:
        self.hide()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        if self.closing:
            event.accept()
            return
        event.ignore()
        self.hide_to_tray()

    def exit_app(self) -> None:
        if self.closing:
            return
        if self.state in ("running", "starting", "stopping"):
            answer = QMessageBox.question(
                self,
                "Выход",
                "Остановить бота и закрыть приложение?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.closing = True
        self.hide()
        self.set_state("stopping")
        self.worker.shutdown()

    def _finish_exit(self) -> None:
        # Порядок важен: сначала завершаем поток работника, затем значок
        # и только потом цикл событий интерфейса.
        self.worker_thread.quit()
        self.worker_thread.wait(5000)
        self.tray_icon.stop()
        QApplication.quit()

    def _exit_failed(self) -> None:
        self.closing = False
        self.set_state("error")
        self.show_window()
        QMessageBox.critical(
            self,
            "Ошибка",
            "Не удалось безопасно остановить процесс бота. "
            "Проверьте логи и попробуйте ещё раз.",
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("PCBot")
    app.setStyleSheet(STYLESHEET)
    font = QFont("Segoe UI", 10)
    app.setFont(font)

    instance = SingleInstance(LOCK_FILE)
    if not instance.acquire():
        QMessageBox.information(
            None,
            "PC Bot",
            "Панель управления уже запущена.\nПосмотри значок в системном трее.",
        )
        return 0

    panel = ControlPanel(auto_start="--autostart" in sys.argv)
    panel.show()
    return app.exec()


if __name__ == "__main__":
    if "--bot" in sys.argv:
        raise SystemExit(run_bot_worker())
    raise SystemExit(main())
