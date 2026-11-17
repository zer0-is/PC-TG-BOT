"""Тесты новых функций: файловый менеджер, яркость, флаги и меню."""

from pathlib import Path

import bot
from pcbot import config, files_mgr, pc_control
from pcbot.handlers.input import _parse_point


def test_human_size_units():
    assert files_mgr.human_size(0) == "0 Б"
    assert files_mgr.human_size(999) == "999 Б"
    assert files_mgr.human_size(1024) == "1.0 КБ"
    assert files_mgr.human_size(1536) == "1.5 КБ"
    assert files_mgr.human_size(5 * 1024**2) == "5.0 МБ"
    assert files_mgr.human_size(3 * 1024**3) == "3.0 ГБ"
    assert files_mgr.human_size(10**15).endswith("ТБ")


def test_list_dir_puts_folders_first(tmp_path: Path):
    (tmp_path / "b_folder").mkdir()
    (tmp_path / "a_folder").mkdir()
    (tmp_path / "z_file.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "a_file.txt").write_text("12345", encoding="utf-8")

    entries = files_mgr.list_dir(tmp_path)
    names = [entry["name"] for entry in entries]
    assert names == ["a_folder", "b_folder", "a_file.txt", "z_file.txt"]
    assert entries[0]["is_dir"] is True
    sizes = {entry["name"]: entry["size"] for entry in entries}
    assert sizes["a_file.txt"] == 5


def test_list_dir_respects_limit(tmp_path: Path):
    for index in range(20):
        (tmp_path / f"file_{index:02d}.txt").write_text("x", encoding="utf-8")
    assert len(files_mgr.list_dir(tmp_path, limit=5)) == 5


def test_list_dir_missing_folder_returns_empty(tmp_path: Path):
    assert files_mgr.list_dir(tmp_path / "нет-такой-папки") == []


def test_safe_join_keeps_path_inside_base(tmp_path: Path):
    inside = files_mgr.safe_join(tmp_path, "sub/file.txt")
    assert inside == (tmp_path / "sub" / "file.txt").resolve()


def test_safe_join_rejects_escape(tmp_path: Path):
    assert files_mgr.safe_join(tmp_path, "../outside.txt") is None


def test_change_brightness_uses_current_value(monkeypatch):
    monkeypatch.setattr(pc_control, "get_brightness", lambda: 50)
    seen = {}

    def fake_set(value: int) -> int:
        seen["value"] = value
        return max(0, min(100, value))

    monkeypatch.setattr(pc_control, "set_brightness", fake_set)
    assert pc_control.change_brightness(10) == 60
    assert seen["value"] == 60
    monkeypatch.setattr(pc_control, "get_brightness", lambda: 5)
    assert pc_control.change_brightness(-20) == 0
    monkeypatch.setattr(pc_control, "get_brightness", lambda: 95)
    assert pc_control.change_brightness(20) == 100


def test_change_brightness_without_support(monkeypatch):
    monkeypatch.setattr(pc_control, "get_brightness", lambda: None)
    try:
        pc_control.change_brightness(10)
    except RuntimeError as error:
        assert "яркость" in str(error).lower()
    else:
        raise AssertionError("change_brightness должен ругаться без поддержки WMI")


def test_dangerous_features_are_off_by_default():
    assert config.ALLOW_INPUT is False
    assert config.ALLOW_AUDIO is False
    assert config.RECORD_SECONDS >= 1


def test_env_bool_parsing(monkeypatch):
    monkeypatch.setenv("FLAG_X", "true")
    assert config._env_bool("FLAG_X") is True
    monkeypatch.setenv("FLAG_X", "True")
    assert config._env_bool("FLAG_X") is True
    monkeypatch.setenv("FLAG_X", "false")
    assert config._env_bool("FLAG_X") is False
    monkeypatch.setenv("FLAG_X", "1")
    assert config._env_bool("FLAG_X") is False  # только слово true
    monkeypatch.delenv("FLAG_X")
    assert config._env_bool("FLAG_X") is False


def test_parse_point_accepts_common_formats():
    assert _parse_point("640,480") == (640, 480)
    assert _parse_point(" 640 ; 480 ") == (640, 480)
    for bad in ("640", "a,b", "1,2,3"):
        try:
            _parse_point(bad)
        except ValueError:
            continue
        raise AssertionError(f"ожидалась ошибка для {bad!r}")


def _menu_values(markup) -> set[str]:
    values = set()
    for row in markup.inline_keyboard:
        for button in row:
            data = button.callback_data
            if data and "|" in data:
                unsigned = bot.unsign_data(data)
                if unsigned:
                    values.add(unsigned)
    return values


def test_recording_button_hidden_without_flag():
    assert config.ALLOW_AUDIO is False
    assert "rec" not in _menu_values(bot.main_menu())


def test_input_button_hidden_without_flag():
    assert config.ALLOW_INPUT is False
    assert "input" not in _menu_values(bot.control_menu())


def test_new_menus_are_fully_signed():
    menus = {
        "monitor": bot.monitor_menu(),
        "control": bot.control_menu(),
        "input": bot.input_menu(),
        "recording": bot.recording_menu(),
    }
    for name, markup in menus.items():
        assert markup.inline_keyboard, f"{name}: пустое меню"
        for row in markup.inline_keyboard:
            for button in row:
                assert button.callback_data, f"{name}: кнопка без callback_data"
                assert bot.unsign_data(button.callback_data) is not None, (
                    f"{name}: подпись {button.callback_data} не проверяется"
                )
