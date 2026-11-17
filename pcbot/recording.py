"""Запись экрана и микрофона. Включается флагом ALLOW_AUDIO."""

import ctypes
import time
from datetime import datetime
from pathlib import Path

from .config import DOWNLOADS, IS_WIN

MAX_SECONDS = 120
DEFAULT_FPS = 8
MAX_WIDTH = 1280


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _prepare_dir() -> Path:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    return DOWNLOADS


def record_screen(seconds: int = 10, fps: int = DEFAULT_FPS) -> Path:
    """Пишет экран в mp4 и возвращает путь к файлу."""
    import cv2
    import mss
    import numpy as np

    duration = max(1, min(int(seconds), MAX_SECONDS))
    rate = max(1, min(int(fps), 30))

    with mss.mss() as sct:
        monitor = sct.monitors[1]
        source_width = monitor["width"]
        source_height = monitor["height"]
        scale = min(1.0, MAX_WIDTH / source_width)
        width = int(source_width * scale) // 2 * 2
        height = int(source_height * scale) // 2 * 2

        path = _prepare_dir() / f"screen_{_stamp()}.mp4"
        fourcc = getattr(cv2, "VideoWriter_fourcc")  # noqa: B009 — нет в стабах OpenCV 5
        writer = cv2.VideoWriter(
            str(path), fourcc(*"mp4v"), rate, (width, height)
        )
        if not writer.isOpened():
            raise RuntimeError("Не удалось открыть кодировщик видео")
        try:
            frames = duration * rate
            for _ in range(frames):
                frame = np.array(sct.grab(monitor))[:, :, :3]
                if (width, height) != (source_width, source_height):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()
    return path


def record_microphone(seconds: int = 10) -> Path:
    """Пишет микрофон в wav через MCI — без сторонних библиотек."""
    if not IS_WIN:
        raise RuntimeError("Запись микрофона реализована только для Windows")
    duration = max(1, min(int(seconds), MAX_SECONDS))
    path = _prepare_dir() / f"mic_{_stamp()}.wav"

    mci = ctypes.windll.winmm.mciSendStringW
    buffer = ctypes.create_unicode_buffer(512)

    def command(text: str) -> None:
        code = mci(text, buffer, len(buffer), None)
        if code != 0:
            raise RuntimeError(f"Аудиоустройство не ответило (код {code})")

    command("open new type waveaudio alias pcbotrec")
    try:
        command("set pcbotrec time format milliseconds")
        command("record pcbotrec")
        time.sleep(duration)
        command("stop pcbotrec")
        command(f'save pcbotrec "{path}"')
    finally:
        mci("close pcbotrec", buffer, len(buffer), None)
    return path
