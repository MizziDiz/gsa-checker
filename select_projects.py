#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""select_projects.py — выбор проектов по номерам.

Печатает проекты (файлы .prj в папке) НУМЕРОВАННЫМ списком, принимает номера через запятую
с пробелом (напр. «1, 3, 5») и выводит имена выбранных проектов — тех, что надо отправить.

Запуск:
    python select_projects.py [ПАПКА] ["1, 3, 5"]

  • ПАПКА   — где лежат .prj (по умолчанию текущая папка);
  • "1,3,5" — номера сразу аргументом. Если не задать — спросит интерактивно.

Примеры:
    python select_projects.py                       # проекты из текущей папки, спросит номера
    python select_projects.py C:\\GSA\\projects        # из указанной папки, спросит номера
    python select_projects.py C:\\GSA\\projects "1, 4, 7"   # без вопросов, сразу выбор

Выбранные имена печатаются по одному в строке и одной строкой через запятую, а также
пишутся в файл selected_projects.txt рядом со скриптом — для дальнейшего использования.
"""

import sys
from pathlib import Path

# на русской Windows вывод в файл/пайп — cp1251; принудительно UTF-8, чтобы не падать
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def list_projects(folder: Path):
    """Имена проектов = имена файлов .prj без расширения, по алфавиту."""
    return sorted(p.stem for p in folder.glob("*.prj"))


def parse_numbers(raw: str, count: int):
    """«1, 3, 5» → список выбранных индексов (1-based). Неверные номера — в предупреждения."""
    chosen, bad = [], []
    for tok in raw.replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.isdigit() and 1 <= int(tok) <= count:
            n = int(tok)
            if n not in chosen:
                chosen.append(n)
        else:
            bad.append(tok)
    return chosen, bad


def main():
    args = sys.argv[1:]
    folder = Path(args[0]) if args else Path.cwd()
    if not folder.is_dir():
        sys.exit(f"Папка не найдена: {folder}")

    projects = list_projects(folder)
    if not projects:
        sys.exit(f"В {folder} нет проектов (.prj).")

    print(f"Проекты в {folder} ({len(projects)}):")
    for i, name in enumerate(projects, 1):
        print(f"  {i}. {name}")

    raw = args[1] if len(args) > 1 else input(
        "\nНомера проектов, которые надо отправить (через запятую с пробелом, напр. 1, 3, 5): ")
    chosen, bad = parse_numbers(raw, len(projects))
    for b in bad:
        print(f"⚠ пропущен неверный номер: {b!r}", file=sys.stderr)
    if not chosen:
        sys.exit("Ничего не выбрано.")

    names = [projects[n - 1] for n in chosen]
    print(f"\nВыбрано {len(names)} — отправляем:")
    for name in names:
        print(name)
    print("\nОдной строкой: " + ", ".join(names))

    out = Path(__file__).resolve().parent / "selected_projects.txt"
    out.write_text("\n".join(names) + "\n", encoding="utf-8")
    print(f"\n✓ список сохранён: {out}")


if __name__ == "__main__":
    main()
