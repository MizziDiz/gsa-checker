#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""select_projects.py — ручная выборочная отправка .success на шару.

Показывает проекты сервера НУМЕРОВАННЫМ списком, принимает номера через запятую с пробелом
(напр. «1, 3, 5») и копирует .success ВЫБРАННЫХ проектов на шару в том же формате, что
`gsa_checker.py --collect-success` (`success_share_dir/<server_name>/`), — чтобы
центральный `--report` на шаре включил их в мерж. Ручной аналог автосбора: не все проекты,
а руками выбранные.

Запуск (на сервере с установленным gsa-checker и его конфигом):
    python select_projects.py                 # покажет проекты, спросит номера
    python select_projects.py "1, 3, 5"        # отправить выбранные
    python select_projects.py --invert "2"     # отправить ВСЕ, КРОМЕ проекта 2
    python select_projects.py --dry-run "1, 3, 5"   # показать, что отправит, без копирования

Режимы:
  • обычный — отправляются ВЫБРАННЫЕ проекты в success_share_dir/<server_name>/;
  • --invert — отправляются ВСЕ, КРОМЕ выбранных, в ОТДЕЛЬНУЮ папку invert_share_dir/
    <server_name>/ (по умолчанию — соседняя «<success_share_dir>_except»), чтобы не
    смешивать с обычным сбором и не попасть в еженедельный мерж автоматически.

Пути и имя сервера берутся из data/gsa_checker.config.json (gsa_projects_dir,
success_share_dir, server_name, verified_glob) — те же, что у gsa_checker.
Папка назначения перед копированием чистится от старых .success: там останется ровно
текущий набор.
"""

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "data" / "gsa_checker.config.json"

# на русской Windows вывод в файл/пайп — cp1251; принудительно UTF-8, чтобы не падать
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_cfg() -> dict:
    if not CONFIG.exists():
        sys.exit(f"Нет конфига {CONFIG}\nНужен data/gsa_checker.config.json с "
                 "gsa_projects_dir, success_share_dir, server_name (как у gsa_checker).")
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        sys.exit(f"Ошибка в конфиге {CONFIG}: {e}")


def as_list(v):
    return v if isinstance(v, list) else [v]


def verified_suffixes(cfg) -> list:
    """verified_glob (напр. ["*.success"]) → расширения [".success"]."""
    out = [g.lstrip("*") for g in as_list(cfg.get("verified_glob", ["*.success"]))
           if g.lstrip("*").startswith(".")]
    return out or [".success"]


def parse_numbers(raw: str, count: int):
    """«1, 3, 5» → (выбранные 1-based номера, неверные токены)."""
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
    dry = "--dry-run" in args
    invert = "--invert" in args
    args = [a for a in args if a not in ("--dry-run", "--invert")]

    cfg = load_cfg()
    proj_dir = Path(cfg.get("gsa_projects_dir", ""))
    if not proj_dir.is_dir():
        sys.exit(f"Папка проектов не найдена: {proj_dir} (gsa_projects_dir в конфиге)")
    share = cfg.get("success_share_dir", "")
    name = str(cfg.get("server_name", "")).strip()
    if not share or not name:
        sys.exit("В конфиге нужны success_share_dir и server_name.")

    projects = sorted(p.stem for p in proj_dir.glob("*.prj"))
    if not projects:
        sys.exit(f"В {proj_dir} нет проектов (.prj).")

    print(f"Проекты на сервере {name} ({len(projects)}):")
    for i, pn in enumerate(projects, 1):
        print(f"  {i}. {pn}")

    prompt = ("\nНомера проектов, которые ИСКЛЮЧИТЬ (отправим все остальные): " if invert
              else "\nНомера проектов для отправки (через запятую с пробелом, напр. 1, 3, 5): ")
    raw = args[0] if args else input(prompt)
    picked, bad = parse_numbers(raw, len(projects))
    for b in bad:
        print(f"⚠ пропущен неверный номер: {b!r}", file=sys.stderr)
    if not picked:
        sys.exit("Ничего не выбрано.")

    # --invert: отправляем ВСЕ, кроме выбранных; иначе — только выбранные
    if invert:
        send_nums = [i for i in range(1, len(projects) + 1) if i not in picked]
        excl = ", ".join(projects[i - 1] for i in picked)
        print(f"\nИНВЕРСИЯ: отправляем все {len(send_nums)}, кроме: {excl}")
    else:
        send_nums = picked

    sufs = verified_suffixes(cfg)
    to_copy = []
    for n in send_nums:
        pn = projects[n - 1]
        found = [proj_dir / (pn + s) for s in sufs if (proj_dir / (pn + s)).exists()]
        if not found:
            print(f"⚠ у «{pn}» нет verified-файла ({'/'.join(sufs)}) — пропуск",
                  file=sys.stderr)
            continue
        to_copy.extend(found)
    if not to_copy:
        sys.exit("Ни у одного из проектов нет .success — отправлять нечего.")

    # обычный режим → success_share_dir; инверсия → отдельная папка (не в еженедельный мерж)
    if invert:
        inv = cfg.get("invert_share_dir")
        if not inv:
            sp = Path(share)
            inv = str(sp.parent / (sp.name + "_except"))
        dest = Path(inv) / name
    else:
        dest = Path(share) / name
    total = sum(f.stat().st_size for f in to_copy)
    print(f"\n{'[dry] ' if dry else ''}На шару → {dest}\n"
          f"{len(to_copy)} файл(ов), {total/1e6:.1f} МБ:")
    for f in to_copy:
        print(f"  {f.name}")
    if dry:
        print("\n[dry-run: ничего не скопировано]")
        return

    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.success"):        # на шаре останется ровно выбранный набор
        try:
            old.unlink()
        except OSError:
            pass
    for f in to_copy:
        shutil.copy2(f, dest / f.name)
    (dest / "_collected.txt").write_text(
        time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    tail = ("Это ОТДЕЛЬНАЯ папка — в еженедельный мерж автоматически НЕ попадёт; "
            "мержить вручную при необходимости." if invert else
            "Дальше их подхватит центральный --report на шаре (мерж + Telegram).")
    print(f"\n✓ отправлено: {dest}\n{tail}")


if __name__ == "__main__":
    main()
