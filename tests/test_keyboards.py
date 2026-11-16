"""Тест, который не даёт «забыть» кнопку без обработчика.

Разбирает исходники пакета pcbot: собирает все callback_data, которые бот
отправляет, и все шаблоны, которые он обрабатывает, затем требует полного
совпадения. Раньше так ловились кнопки вроде «Процессы», обработчик которой
был пустым.
"""
import ast
from pathlib import Path

import bot

PACKAGE = Path(bot.__file__).resolve().parent / "pcbot"
SOURCES = sorted(PACKAGE.rglob("*.py"))


def _parse() -> ast.Module:
    """Сводит все модули пакета в одно дерево — проверки ходят по нему целиком."""
    body = []
    for path in SOURCES:
        body.extend(ast.parse(path.read_text(encoding="utf-8")).body)
    return ast.Module(body=body, type_ignores=[])


def _is_data_attr(node: ast.AST) -> bool:
    """Похоже ли выражение на F.data (магический фильтр поля data)."""
    return isinstance(node, ast.Attribute) and node.attr == "data"


def _string_constants(node: ast.AST) -> list[str]:
    """Достаёт строковые константы из литерала-коллекции."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        return [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    return []


def handler_patterns(tree: ast.Module) -> list[tuple[str, str]]:
    """Шаблоны фильтров F.data: ('eq', 'closeall') и ('sw', 'kill:')."""
    patterns: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            for expression in ast.walk(decorator):
                # F.data == "menu"
                if isinstance(expression, ast.Compare) and _is_data_attr(expression.left):
                    for op, comparator in zip(expression.ops, expression.comparators, strict=False):
                        if not isinstance(op, (ast.Eq, ast.In)):
                            continue
                        for value in _string_constants(comparator):
                            patterns.append(("eq", value))
                    continue
                if not isinstance(expression, ast.Call):
                    continue
                func = expression.func
                if not isinstance(func, ast.Attribute) or len(expression.args) != 1:
                    continue
                # F.data.startswith("kill:")
                if func.attr == "startswith" and _is_data_attr(func.value):
                    for value in _string_constants(expression.args[0]):
                        patterns.append(("sw", value))
                # F.data.in_({"a", "b"})
                elif func.attr == "in_" and _is_data_attr(func.value):
                    for value in _string_constants(expression.args[0]):
                        patterns.append(("eq", value))
    return patterns


def callback_prefixes(tree: ast.Module) -> set[str]:
    """Все callback_data, которые бот кладёт в кнопки.

    Для f-строк берётся текст до первого подставляемого значения:
    'kill:{pid}' -> 'kill:'.
    """
    values: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "b"):
            continue
        for keyword in node.keywords:
            if keyword.arg != "callback_data":
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values.add(value.value)
            elif isinstance(value, ast.JoinedStr):
                prefix = ""
                for part in value.values:
                    if isinstance(part, ast.Constant) and isinstance(part.value, str):
                        prefix += part.value
                    else:
                        break
                if prefix:
                    values.add(prefix)
    return values


def is_covered(value: str, patterns: list[tuple[str, str]]) -> bool:
    for operator, pattern in patterns:
        if operator == "eq" and value == pattern:
            return True
        if operator == "sw" and value.startswith(pattern):
            return True
    return False


def test_every_callback_has_a_handler():
    tree = _parse()
    patterns = handler_patterns(tree)
    assert patterns, "Не найдено ни одного фильтра F.data — тест сломан"
    missing = sorted(
        value for value in callback_prefixes(tree) if not is_covered(value, patterns)
    )
    assert not missing, f"Кнопки без обработчика: {missing}"


def test_callback_prefixes_are_not_empty():
    tree = _parse()
    values = callback_prefixes(tree)
    assert len(values) > 20, f"Слишком мало найдено callback_data: {len(values)}"


def test_all_menu_buttons_are_signed():
    """Каждая кнопка во всех меню должна нести подпись."""
    menus = {
        "main": bot.main_menu(),
        "sound": bot.sound_menu(),
        "apps": bot.apps_menu(),
        "timer": bot.timer_menu(),
        "clip": bot.clip_menu(),
        "sites": bot.sites_menu(),
        "tg": bot.tg_menu(),
    }
    for name, markup in menus.items():
        for row in markup.inline_keyboard:
            for button in row:
                assert button.callback_data, f"{name}: кнопка без callback_data"
                assert "|" in button.callback_data, f"{name}: {button.callback_data} не подписан"
                assert bot.unsign_data(button.callback_data) is not None, (
                    f"{name}: подпись {button.callback_data} не проверяется"
                )
