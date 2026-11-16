"""Тесты процессов, системной информации и работы с .env."""
import math

import bot
import control_panel


def test_process_rows_returns_sorted_list():
    rows = bot.process_rows(5)
    assert len(rows) <= 5
    for row in rows:
        cpu, ram, pid, name = row
        assert isinstance(cpu, float)
        assert isinstance(ram, float)
        assert isinstance(pid, int)
        assert isinstance(name, str)


def test_process_rows_all_returns_more_than_top():
    top = bot.process_rows(5)
    everything = bot.process_rows(None)
    assert len(everything) >= len(top)
    assert len(everything) > 5


def test_sort_rows_by_ram():
    rows = [(1.0, 5.0, 10, "a"), (9.0, 1.0, 11, "b"), (5.0, 3.0, 12, "c")]
    assert [row[3] for row in bot.sort_rows(rows, "ram")] == ["a", "c", "b"]
    assert [row[3] for row in bot.sort_rows(rows, "cpu")] == ["b", "c", "a"]
    assert [row[3] for row in bot.sort_rows(rows, "name")] == ["a", "b", "c"]


def test_procs_menu_paginates():
    rows = [(float(i), 1.0, 1000 + i, f"proc{i}") for i in range(40)]
    pages = math.ceil(40 / bot.PROC_PAGE_SIZE)
    assert pages == 5

    first = bot.procs_menu(rows, page=0, sort="cpu")
    flat_first = [button for row in first.inline_keyboard for button in row]
    # 8 кнопок закрытия + 3 сортировки + «1/5» и «▶️» + «назад»
    assert len(flat_first) == bot.PROC_PAGE_SIZE + 3 + 2 + 1

    middle = bot.procs_menu(rows, page=2, sort="cpu")
    flat_middle = [button for row in middle.inline_keyboard for button in row]
    # на средней странице появляется ещё и «◀️»
    assert len(flat_middle) == bot.PROC_PAGE_SIZE + 3 + 3 + 1


def test_procs_menu_last_page_is_not_empty():
    rows = [(float(i), 1.0, 1000 + i, f"proc{i}") for i in range(40)]
    markup = bot.procs_menu(rows, page=4, sort="cpu")
    flat = [button for row in markup.inline_keyboard for button in row]
    assert len(flat) > 0


def test_procs_text_mentions_page():
    rows = [(float(i), 1.0, 1000 + i, f"proc{i}") for i in range(40)]
    text = bot.procs_text(rows, page=1, sort="cpu")
    assert "Страница 2/5" in text
    assert "40" in text


def test_protected_pids_contains_bot():
    import os

    assert os.getpid() in bot.protected_pids()
    assert 0 in bot.protected_pids()
    assert 4 in bot.protected_pids()


def test_protected_list_includes_shell():
    """Оболочку Windows закрывать нельзя — рабочий стол рассыпается."""
    for name in ("explorer.exe", "svchost.exe", "lsass.exe", "dwm.exe"):
        assert name in bot.PROTECTED


def test_kill_process_refuses_to_kill_bot():
    import os

    try:
        bot.kill_process(os.getpid())
    except RuntimeError as error:
        assert "бот" in str(error).lower()
    else:
        raise AssertionError("Бот не должен позволять закрыть самого себя")


def test_system_info_helpers_do_not_crash():
    for getter in (
        bot.get_cpu_info,
        bot.get_ram_info,
        bot.get_disk_info,
        bot.get_gpu_info,
        bot.get_temperatures,
    ):
        result = getter()
        assert result is not None

    assert isinstance(bot.get_system_uptime(), str)
    assert isinstance(bot.get_windows_info(), str)
    assert isinstance(bot.get_battery_info(), dict)
    assert "percent" in bot.get_battery_info()


def test_system_stats_is_html_safe():
    stats = bot.system_stats()
    assert "<b>" in stats or "<pre>" in stats
    assert "\\n" not in stats, "В HTML-режиме Telegram переносы должны быть настоящими"


def test_env_roundtrip(tmp_path):
    """Значения с обратными слэшами и кавычками должны переживать запись и чтение."""
    path = tmp_path / ".env"
    values = {
        "BOT_TOKEN": "123456:AAHabc-def",
        "ALLOWED_USER_IDS": "111111",
        "OPENROUTER_API_KEY": "",
        "OPENROUTER_MODEL": "inclusionai/ling-3.2-flash:free",
        "ALLOW_SHELL": "false",
        "ALLOW_FILES": "false",
        "POWER_DELAY": "25",
        "SIGNING_KEY": "",
        "RATE_LIMIT": "20",
    }
    control_panel.write_env_values(path, values)
    read = control_panel.read_env_values(path)
    for key, expected in values.items():
        assert read.get(key) == expected, f"{key}: {read.get(key)!r} != {expected!r}"


def test_env_preserves_unknown_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# комментарий\nCUSTOM=value\n", encoding="utf-8")
    control_panel.write_env_values(path, {"BOT_TOKEN": "abc"})
    text = path.read_text(encoding="utf-8")
    assert "# комментарий" in text
    assert "CUSTOM" in text


def test_configuration_is_complete():
    assert control_panel.configuration_is_complete(
        {"BOT_TOKEN": "123:abc", "ALLOWED_USER_IDS": "111, 222"}
    )
    assert not control_panel.configuration_is_complete(
        {"BOT_TOKEN": "", "ALLOWED_USER_IDS": "111"}
    )
    assert not control_panel.configuration_is_complete(
        {"BOT_TOKEN": "123:abc", "ALLOWED_USER_IDS": "not-a-number"}
    )


def test_env_defaults_cover_bot_config():
    """Все настройки, которые читает бот, должны редактироваться в панели."""
    for key in (
        "BOT_TOKEN",
        "ALLOWED_USER_IDS",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "ALLOW_SHELL",
        "ALLOW_FILES",
        "POWER_DELAY",
        "SIGNING_KEY",
        "RATE_LIMIT",
    ):
        assert key in control_panel.ENV_DEFAULTS, f"{key} не редактируется в панели"
