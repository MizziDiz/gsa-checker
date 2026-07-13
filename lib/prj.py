#!/usr/bin/env python3
"""
lib/prj.py — разбор и редактирование файлов проектов GSA SER (.prj).

Формат .prj — INI-подобный, UTF-8, значения однострочные (переводы строк внутри
значений экранированы как литеральные \\n). Секции проекта:
  [data_value]      — контент (URL, Keywords, спин-поля)
  [Options]         — ~100+ настроек key=value
  [engines]         — ИмяДвижка=1/0 (куда постить)
  [email accounts]  — N=email␦provider.ini␦… (разделитель 0x7F)

Редактор построчный и round-trip-безопасный: трогаем только запрошенные строки,
всё остальное (порядок, пробелы, незнакомые строки, концы строк, BOM) сохраняем
как есть — чтобы не побить спин-синтаксис {a|b} и 0x7F в аккаунтах.

Ключевой класс — Prj: load() / set_value() / get_value() / save().
"""

from __future__ import annotations

import re
from pathlib import Path

SECTION_RE = re.compile(r"^\[(?P<name>.+)\]\s*$")

# известные секции GSA — строку [..] без '=' считаем заголовком секции;
# внутри секции каждая содержательная строка имеет вид Key=Value
KNOWN_SECTIONS = {"data_value", "options", "engines", "email accounts"}


class Prj:
    def __init__(self, lines: list[str], newline: str, bom: bool):
        self.lines = lines          # строки БЕЗ символов конца строки
        self.newline = newline      # "\n" или "\r\n" — как в оригинале
        self.bom = bom              # был ли UTF-8 BOM

    # ── загрузка/сохранение ─────────────────────────────────────────────────
    @classmethod
    def load(cls, path: Path) -> "Prj":
        raw = path.read_bytes()
        bom = raw.startswith(b"\xef\xbb\xbf")
        body = raw[3:] if bom else raw
        # surrogateescape: валидный UTF-8 декодируется нормально, а сырые байты
        # (напр. разделитель 0xFF в [email accounts]) сохраняются без потерь
        text = body.decode("utf-8", errors="surrogateescape")
        newline = "\r\n" if "\r\n" in text else "\n"
        # splitlines по любым переводам, ending восстановим при сохранении
        lines = text.split("\r\n") if newline == "\r\n" else text.split("\n")
        # split даёт хвостовой "" если файл кончался переводом — запомним и уберём
        trailing = lines and lines[-1] == ""
        if trailing:
            lines.pop()
        obj = cls(lines, newline, bom)
        obj._trailing_newline = trailing
        return obj

    def to_text(self) -> str:
        text = self.newline.join(self.lines)
        if getattr(self, "_trailing_newline", True):
            text += self.newline
        return text

    def to_bytes(self) -> bytes:
        body = self.to_text().encode("utf-8", errors="surrogateescape")
        return (b"\xef\xbb\xbf" + body) if self.bom else body

    def save(self, path: Path) -> None:
        path.write_bytes(self.to_bytes())

    # ── навигация по секциям ────────────────────────────────────────────────
    def _section_bounds(self, section: str) -> tuple[int, int] | None:
        """(idx заголовка, idx конца секции exclusive) или None. Без учёта регистра."""
        want = section.strip().lower()
        start = None
        for i, line in enumerate(self.lines):
            m = SECTION_RE.match(line)
            if not m:
                continue
            name = m.group("name").strip().lower()
            if start is None and name == want:
                start = i
                continue
            if start is not None:
                return (start, i)   # следующая секция = конец текущей
        if start is not None:
            return (start, len(self.lines))
        return None

    @staticmethod
    def _key_of(line: str) -> str | None:
        """Ключ строки Key=Value (до первого '='); None если не key=value."""
        if line.startswith("[") or "=" not in line:
            return None
        return line.split("=", 1)[0].strip()

    # ── чтение/запись значений ──────────────────────────────────────────────
    def get_value(self, section: str, key: str) -> str | None:
        bounds = self._section_bounds(section)
        if not bounds:
            return None
        start, end = bounds
        want = key.strip().lower()
        for i in range(start + 1, end):
            k = self._key_of(self.lines[i])
            if k is not None and k.lower() == want:
                return self.lines[i].split("=", 1)[1]
        return None

    def set_value(self, section: str, key: str, value: str) -> str | None:
        """Ставит Key=Value в секции. Возвращает старое значение (или None, если
        ключа/секции не было — тогда добавляет). Секцию создаёт при отсутствии."""
        bounds = self._section_bounds(section)
        if not bounds:
            # новой секции — в конец файла
            if self.lines and self.lines[-1].strip() != "":
                self.lines.append("")
            self.lines.append(f"[{section}]")
            self.lines.append(f"{key}={value}")
            return None
        start, end = bounds
        want = key.strip().lower()
        for i in range(start + 1, end):
            k = self._key_of(self.lines[i])
            if k is not None and k.lower() == want:
                old = self.lines[i].split("=", 1)[1]
                # сохраняем исходное написание ключа
                orig_key = self.lines[i].split("=", 1)[0]
                self.lines[i] = f"{orig_key}={value}"
                return old
        # ключа нет — добавляем в конец секции (после последней непустой строки)
        insert_at = end
        while insert_at - 1 > start and self.lines[insert_at - 1].strip() == "":
            insert_at -= 1
        self.lines.insert(insert_at, f"{key}={value}")
        return None

    def list_keys(self, section: str) -> list[str]:
        bounds = self._section_bounds(section)
        if not bounds:
            return []
        start, end = bounds
        keys = []
        for i in range(start + 1, end):
            k = self._key_of(self.lines[i])
            if k is not None:
                keys.append(k)
        return keys

    def sections(self) -> list[str]:
        out = []
        for line in self.lines:
            m = SECTION_RE.match(line)
            if m:
                out.append(m.group("name").strip())
        return out


# ── парсинг спецификации правок --set "Секция:ключ=значение" ────────────────
def parse_set_spec(spec: str) -> tuple[str, str, str]:
    """'Options:use random url=1' → ('Options', 'use random url', '1').
    Секция — до первого ':', ключ — до первого '=', остальное — значение
    (в значении '=' и ':' допустимы, т.к. режем по первому)."""
    if ":" not in spec:
        raise ValueError(f"нет секции (нужно 'Секция:ключ=значение'): {spec}")
    section, rest = spec.split(":", 1)
    if "=" not in rest:
        raise ValueError(f"нет '=' в '{spec}' (нужно 'Секция:ключ=значение')")
    key, value = rest.split("=", 1)
    section, key = section.strip(), key.strip()
    if not section or not key:
        raise ValueError(f"пустая секция или ключ в: {spec}")
    return section, key, value
