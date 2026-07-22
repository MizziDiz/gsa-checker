#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lib/spin.py — рандомизация спинтакса БЕЗ потери спина.

Перемешивает порядок вариантов внутри каждого блока `{a|b|c}` (рекурсивно, с учётом
вложенности), оставляя валидный спинтаксис. Так текстовое поле проекта становится
уникальным на диске (дублированные проекты перестают быть клонами), но GSA по-прежнему
спинит его при постинге. Макросы `%spinfile-…%`, `%random-…%`, `\\n` и прочий текст вне
`{…}` не трогаются. При несбалансированных скобках поле возвращается как есть (без порчи).
"""

from __future__ import annotations

import random


def _match(s: str, i: int) -> int:
    """Индекс `}` , парного к `{` в позиции i; -1 если не найден."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _split_top(inner: str) -> list:
    """Разбить содержимое блока по `|` ВЕРХНЕГО уровня (вложенные `{…}` не трогаем)."""
    parts, depth, start = [], 0, 0
    for k, c in enumerate(inner):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == "|" and depth == 0:
            parts.append(inner[start:k])
            start = k + 1
    parts.append(inner[start:])
    return parts


def _process(s: str) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        if s[i] == "{":
            j = _match(s, i)
            if j == -1:
                out.append(s[i:])          # несбалансированно — остаток как есть
                break
            opts = [_process(o) for o in _split_top(s[i + 1:j])]
            if len(opts) > 1:
                random.shuffle(opts)
            out.append("{" + "|".join(opts) + "}")
            i = j + 1
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def has_spin(value: str) -> bool:
    """Есть ли что шаффлить: сбалансированные скобки и хотя бы один `|` внутри `{…}`."""
    if not value or "{" not in value or "|" not in value:
        return False
    return value.count("{") == value.count("}")


def shuffle_spintax(value: str) -> str:
    """Перемешать порядок вариантов во всех спин-блоках. Небезопасные (несбалансированные)
    значения возвращаются без изменений."""
    if not has_spin(value):
        return value
    result = _process(value)
    # страховка: не должны менять множество символов/скобочный баланс
    if result.count("{") != value.count("{") or result.count("}") != value.count("}"):
        return value
    return result
