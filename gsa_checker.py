#!/usr/bin/env python3
"""
gsa_checker.py — автоматизация и мониторинг GSA Search Engine Ranker.

У GSA SER нет HTTP API (в отличие от A-Parser), поэтому интерфейс — гибридный:
  • чтение статистики/остатка и массовое создание проектов — через ФАЙЛЫ проектов
    (папка projects: <проект>.prj + data-файлы кэша целей/результатов);
  • живые правки настроек запущенного GSA — через UI-автоматизацию (модуль появится
    отдельно, lib/ui.py).

Этот файл пока реализует РЕЖИМ ОСТАТКА — сколько целей ещё не обработано в каждом
проекте (аналог «total − done» в A-Parser). Остаток = число строк в файлах кэша
целей проекта (шаблон target_cache_glob в конфиге).

Режимы:
  python gsa_checker.py --remaining       # таблица остатка целей по проектам + итог
  python gsa_checker.py --remaining --json # то же в JSON (для интеграций)
  python gsa_checker.py --check           # диагностика: что видит скрипт (пути, файлы)

Конфиг: data/gsa_checker.config.json (шаблон — config.example.json).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "gsa_checker.config.json"
STATE_PATH = DATA_DIR / "gsa_checker.state.json"


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_state(state: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    except OSError as exc:
        print(f"⚠ не сохранён {STATE_PATH.name}: {exc}", file=sys.stderr)


# ── конфиг ──────────────────────────────────────────────────────────────────
def load_config() -> dict:
    if not CONFIG_PATH.exists():
        example = ROOT / "config.example.json"
        sys.exit(
            f"Нет конфига {CONFIG_PATH}.\n"
            f"Скопируйте шаблон:  cp {example} {CONFIG_PATH}\n"
            f"и впишите gsa_projects_dir."
        )
    # utf-8-sig: терпим BOM (Блокнот сохраняет с ним) — иначе json.load падает
    text = CONFIG_PATH.read_text(encoding="utf-8-sig")
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        lines = text.splitlines()
        bad = lines[e.lineno - 1] if 0 < e.lineno <= len(lines) else ""
        pointer = " " * (max(e.colno - 1, 0)) + "^"
        sys.exit(
            f"Ошибка в конфиге {CONFIG_PATH.name}: {e.msg}\n"
            f"  строка {e.lineno}, колонка {e.colno}:\n"
            f"    {bad}\n"
            f"    {pointer}\n"
            "Частые причины: пропущена запятая в конце предыдущей строки; "
            "одиночный '\\' в пути (нужно '\\\\'); '//'-комментарий (в JSON нельзя); "
            "лишняя запятая перед '}'."
        )


def as_list(value) -> list[str]:
    """Шаблоны в конфиге могут быть строкой или списком — нормализуем в список."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


# ── подсчёт строк (быстро, без загрузки файла в память) ─────────────────────
def count_lines(path: Path) -> int:
    """Число непустых строк в файле кэша целей. Читает бинарно чанками.

    Последняя строка без \\n тоже считается. Пустые строки (только \\n или
    \\r\\n подряд) не учитываются — GSA иногда оставляет хвостовой перевод.
    """
    total = 0
    trailing_nonempty = False
    with path.open("rb") as fh:
        prev_newline = True  # начало файла — как будто после перевода строки
        while True:
            chunk = fh.read(1 << 20)  # 1 МБ
            if not chunk:
                break
            for byte in chunk:
                if byte == 0x0A:  # \n
                    prev_newline = True
                    trailing_nonempty = False
                elif byte == 0x0D:  # \r — игнорируем как разделитель
                    continue
                else:
                    if prev_newline:
                        total += 1
                        prev_newline = False
                    trailing_nonempty = True
    return total


# ── сбор остатка по проектам ────────────────────────────────────────────────
def collect_remaining(cfg: dict) -> dict:
    projects_dir = Path(cfg["gsa_projects_dir"])
    globs = as_list(cfg.get("target_cache_glob",
                            ["*.targets", "*.new_targets", "*.new_targets2"]))

    result = {"projects_dir": str(projects_dir), "globs": globs,
              "projects": [], "total_remaining": 0, "errors": []}

    if not projects_dir.is_dir():
        result["errors"].append(f"Папка проектов не найдена: {projects_dir}")
        return result

    # группируем по «базовому» имени файла (без расширения кэша)
    per_project: dict[str, dict] = {}
    for entry in projects_dir.iterdir():
        if not entry.is_file():
            continue
        if not any(fnmatch.fnmatch(entry.name, g) for g in globs):
            continue
        base = entry.stem  # имя без расширения
        try:
            lines = count_lines(entry)
        except OSError as exc:
            result["errors"].append(f"{entry.name}: {exc}")
            continue
        slot = per_project.setdefault(base, {"base": base, "remaining": 0, "files": []})
        slot["remaining"] += lines
        slot["files"].append(entry.name)

    # имя проекта = имя файла (GSA так и делает); из .prj не читаем, чтобы не
    # ловить двойную перекодировку кириллицы
    for base, slot in per_project.items():
        slot["name"] = base
        result["total_remaining"] += slot["remaining"]

    result["projects"] = sorted(per_project.values(),
                                key=lambda s: s["remaining"], reverse=True)
    return result


# ── вывод ───────────────────────────────────────────────────────────────────
def print_table(data: dict) -> None:
    for err in data["errors"]:
        print(f"⚠ {err}", file=sys.stderr)
    projects = data["projects"]
    if not projects:
        print(f"Проектов с файлами кэша целей не найдено в {data['projects_dir']}")
        print(f"(шаблоны: {', '.join(data['globs'])})")
        return
    name_w = max(len(p["name"] or p["base"]) for p in projects)
    name_w = min(max(name_w, 8), 50)
    print(f"{'ПРОЕКТ':<{name_w}}  {'ОСТАТОК ЦЕЛЕЙ':>13}")
    print("─" * (name_w + 15))
    for p in projects:
        label = (p["name"] or p["base"])[:name_w]
        print(f"{label:<{name_w}}  {p['remaining']:>13,}")
    print("─" * (name_w + 15))
    print(f"{'ИТОГО':<{name_w}}  {data['total_remaining']:>13,}")


def augment_velocity(cfg: dict, data: dict, record: bool = True) -> None:
    """Пишет снимок статистики в SQLite и добавляет к каждому проекту r['vel']
    (скорость/ETA/стоп по истории за eta_window_min). Без истории r['vel']=None."""
    if not data.get("projects") or not cfg.get("stats_snapshots", True):
        for r in data.get("projects", []):
            r.setdefault("vel", None)
        return
    from lib import statsdb
    db = Path(cfg.get("stats_db") or (DATA_DIR / "gsa_stats.db"))
    con = statsdb.connect(db)
    now = time.time()
    if record:
        statsdb.record(con, data["projects"], ts=int(now))
    window = float(cfg.get("eta_window_min", 180) or 180) * 60
    for r in data["projects"]:
        r["vel"] = statsdb.velocity(con, r["name"], window, now=now)
    statsdb.prune(con, float(cfg.get("stats_retention_days", 30) or 0), now=now)
    con.close()


def cmd_notify(cfg: dict, args) -> None:
    """Уведомления в Telegram по статистике проектов:
      • остаток целей < low_targets_threshold → «⏳ мало целей» (кулдаун cooldown_hours);
      • цели кончились (0) → «🛑 цели кончились»;
      • остаток снова вырос выше порога → «✅ цели пополнились» (снятие тревоги);
      • раз в heartbeat_hours при успешном прогоне → «🟢 всё ок» со сводкой.
    Дедуп/кулдаун — в data/gsa_checker.state.json. С --dry-run ничего не шлёт, только печатает.
    """
    import time
    from lib import telegram

    data = collect_stats(cfg)
    augment_velocity(cfg, data)
    for err in data["errors"]:
        print(f"⚠ {err}", file=sys.stderr)
    if not data["projects"] and not data["errors"]:
        print("Проектов не найдено — нечего проверять.")
        return

    threshold = float(cfg.get("low_targets_threshold", 0) or 0)
    cooldown_s = float(cfg.get("cooldown_hours", 8) or 0) * 3600
    heartbeat_s = float(cfg.get("heartbeat_hours", 6) or 0) * 3600
    now = time.time()
    dry = getattr(args, "dry_run", False)

    state = load_state()
    low = state.setdefault("low_alert", {})   # {project: ts последней тревоги}

    def emit(text: str) -> bool:
        if dry:
            print(f"[dry-run] отправил бы:\n{text}\n")
            return True
        return telegram.send(cfg, text)

    # 1) тревоги/восстановления по остатку
    if threshold > 0:
        for r in data["projects"]:
            name, remaining = r["name"], r["remaining"]
            if remaining < threshold:
                last = low.get(name)
                if last is None or (now - last) >= cooldown_s:
                    if remaining <= 0:
                        msg = f"🛑 <b>Цели кончились</b>\n{name}: 0 (verified {r['verified']:,})"
                    else:
                        msg = (f"⏳ <b>Мало целей</b>\n{name}: осталось {remaining:,} "
                               f"(порог {int(threshold):,}, verified {r['verified']:,})")
                        v = r.get("vel")
                        if v and v["eta_sec"] is not None:
                            from lib.statsdb import fmt_eta
                            msg += f"\nETA до нуля: {fmt_eta(v['eta_sec'])}"
                    if emit(msg) and not dry:
                        low[name] = now
            elif name in low:
                # остаток восстановился выше порога — снимаем тревогу
                if emit(f"✅ <b>Цели пополнились</b>\n{name}: {remaining:,}") and not dry:
                    del low[name]
    else:
        print("low_targets_threshold=0 — тревоги по остатку отключены.")

    # 1b) «проект встал» — по истории снимков (нужна накопленная time-series)
    stall = state.setdefault("stall_alert", {})
    for r in data["projects"]:
        name, v = r["name"], r.get("vel")
        if v and v["stalled"]:
            last = stall.get(name)
            if last is None or (now - last) >= cooldown_s:
                mins = int(v["span_sec"] // 60)
                msg = (f"🟠 <b>Проект встал</b>\n{name}: остаток {r['remaining']:,} "
                       f"не убывает и нет новых verified за ~{mins} мин")
                if emit(msg) and not dry:
                    stall[name] = now
        elif name in stall and v and not v["stalled"]:
            if emit(f"✅ <b>Проект снова работает</b>\n{name}") and not dry:
                del stall[name]

    # 2) heartbeat
    t = data["totals"]
    summary = (f"проектов {len(data['projects'])}, суммарный остаток {t['remaining']:,}, "
               f"verified {t['verified']:,}, на проверку {t['to_verify']:,}")
    if heartbeat_s > 0:
        last_hb = state.get("heartbeat_ts")
        if last_hb is None:
            state["heartbeat_ts"] = now         # первый прогон только ставит отметку
        elif (now - last_hb) >= heartbeat_s:
            if emit(f"🟢 <b>GSA: всё ок</b>\n{summary}") and not dry:
                state["heartbeat_ts"] = now

    if not dry:
        save_state(state)
    print(f"notify: {summary}")


def _count_for(projects_dir: Path, base: str, globs: list[str]) -> int:
    """Строки во всех файлах проекта <base>, чьё имя подходит под globs."""
    total = 0
    for pattern in globs:
        suffix = pattern.lstrip("*")          # "*.targets" → ".targets"
        f = projects_dir / f"{base}{suffix}"
        if f.is_file():
            try:
                total += count_lines(f)
            except OSError:
                pass
    return total


def collect_stats(cfg: dict) -> dict:
    """Снимок статистики по каждому проекту (по числу строк в data-файлах).
    Проект = <имя>.prj в папке; счётчики берём у файлов с тем же именем."""
    projects_dir = Path(cfg.get("gsa_projects_dir", ""))
    metrics = {
        "remaining": as_list(cfg.get("target_cache_glob", ["*.targets"])),
        "verified":  as_list(cfg.get("verified_glob", ["*.success"])),
        "to_verify": as_list(cfg.get("to_verify_glob", ["*.verify"])),
        "done":      as_list(cfg.get("done_glob", ["*.urls_done"])),
    }
    out = {"projects_dir": str(projects_dir), "projects": [],
           "totals": {k: 0 for k in metrics}, "errors": []}
    if not projects_dir.is_dir():
        out["errors"].append(f"Папка проектов не найдена: {projects_dir}")
        return out
    for prj in sorted(projects_dir.glob("*.prj")):
        base = prj.stem
        row = {"name": base}
        for metric, globs in metrics.items():
            n = _count_for(projects_dir, base, globs)
            row[metric] = n
            out["totals"][metric] += n
        out["projects"].append(row)
    out["projects"].sort(key=lambda r: r["remaining"], reverse=True)
    return out


def print_stats(data: dict) -> None:
    for err in data["errors"]:
        print(f"⚠ {err}", file=sys.stderr)
    rows = data["projects"]
    if not rows:
        print(f"Проектов (.prj) не найдено в {data['projects_dir']}")
        return
    from lib.statsdb import fmt_eta
    name_w = min(max((len(r["name"]) for r in rows), default=8), 32)
    has_vel = any(r.get("vel") for r in rows)
    hdr = (f"{'ПРОЕКТ':<{name_w}}  {'ОСТАТОК':>10}  {'VERIFIED':>9}  "
           f"{'НА ПРОВ.':>9}  {'ОБРАБОТ.':>10}")
    if has_vel:
        hdr += f"  {'ЦЕЛЬ/Ч':>8}  {'ETA':>9}"
    print(hdr)
    print("─" * len(hdr))
    for r in rows:
        line = (f"{r['name'][:name_w]:<{name_w}}  {r['remaining']:>10,}  "
                f"{r['verified']:>9,}  {r['to_verify']:>9,}  {r['done']:>10,}")
        if has_vel:
            v = r.get("vel")
            if v:
                rate = f"{v['targets_per_hr']:,.0f}"
                eta = "СТОП" if v["stalled"] else fmt_eta(v["eta_sec"])
            else:
                rate, eta = "—", "—"
            line += f"  {rate:>8}  {eta:>9}"
        print(line)
    t = data["totals"]
    print("─" * len(hdr))
    tail = (f"{'ИТОГО':<{name_w}}  {t['remaining']:>10,}  {t['verified']:>9,}  "
            f"{t['to_verify']:>9,}  {t['done']:>10,}")
    print(tail)


def _read_targets(src: Path, limit: int) -> list[str]:
    """Список целевых URL из файла или папки (*.txt), дедуп с сохранением порядка."""
    files = []
    if src.is_dir():
        files = sorted(p for p in src.rglob("*.txt") if p.is_file())
    elif src.is_file():
        files = [src]
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        with f.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                u = line.strip()
                if u and u not in seen:
                    seen.add(u)
                    out.append(u)
                    if limit and len(out) >= limit:
                        return out
    return out


def cmd_create(cfg: dict, args) -> None:
    """Собирает готовый к импорту проект GSA: <name>.prj из шаблона (URL/Keywords) +
    <name>.targets из батча целей. Не трогает живой GSA — пишет в отдельную папку."""
    from lib.prj import Prj

    if not args.name:
        sys.exit("Нужно --name (имя проекта).")
    template = Path(args.template or cfg.get("gsa_template_prj", ""))
    if not template.is_file():
        sys.exit(f"Шаблон .prj не найден: {template}\n"
                 "Укажите --template ПУТЬ или gsa_template_prj в конфиге.")
    out_dir = Path(args.out or cfg.get("create_out_dir", "") or (DATA_DIR / "created"))
    out_dir.mkdir(parents=True, exist_ok=True)

    prj_out = out_dir / f"{args.name}.prj"
    tgt_out = out_dir / f"{args.name}.targets"
    if (prj_out.exists() or tgt_out.exists()) and not args.force:
        sys.exit(f"Уже есть {prj_out.name}/{tgt_out.name} в {out_dir}. "
                 "Добавьте --force для перезаписи.")

    # .prj из шаблона
    prj = Prj.load(template)
    changed = []
    if args.url:
        prj.set_value("data_value", "URL", args.url)
        changed.append("URL")
    if args.keywords:
        prj.set_value("data_value", "Keywords", args.keywords)
        changed.append("Keywords")

    # .targets из батча
    targets: list[str] = []
    if args.targets:
        src = Path(args.targets)
        if not src.exists():
            sys.exit(f"Источник целей не найден: {src}")
        targets = _read_targets(src, int(args.limit or 0))

    if args.dry_run:
        print(f"[dry-run] проект {args.name}")
        print(f"  шаблон : {template}")
        print(f"  .prj   : {prj_out}  (правки: {', '.join(changed) or 'нет'})")
        print(f"  .targets: {tgt_out}  ({len(targets):,} целей)")
        return

    prj.save(prj_out)
    tgt_out.write_text("\n".join(targets) + ("\n" if targets else ""), encoding="utf-8")
    print(f"✓ создан проект {args.name} в {out_dir}")
    print(f"  {prj_out.name}  (правки: {', '.join(changed) or 'нет'})")
    print(f"  {tgt_out.name}  ({len(targets):,} целей)")
    print("Импортируйте папку/файлы в GSA (или скопируйте в gsa_projects_dir).")


AUTOPILOT_JOURNAL = DATA_DIR / "gsa_autopilot.jsonl"


def _load_applied() -> set:
    """Множество (батч, проект), уже дозалитых — для идемпотентности."""
    applied = set()
    if AUTOPILOT_JOURNAL.exists():
        for line in AUTOPILOT_JOURNAL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("action") == "append" and "batch" in rec and "project" in rec:
                applied.add((rec["batch"], rec["project"]))
    return applied


def _append_journal(rec: dict) -> None:
    rec = {"ts": int(time.time()), **rec}
    AUTOPILOT_JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with AUTOPILOT_JOURNAL.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _append_targets_file(dest: Path, body: bytes) -> None:
    """Дозаписывает цели в конец файла, не стирая существующее. Ставит перевод
    строки, если файл не кончался им (чтобы не склеить URL)."""
    prefix = b""
    if dest.exists() and dest.stat().st_size > 0:
        with dest.open("rb") as f:
            f.seek(-1, 2)
            if f.read(1) not in (b"\n", b"\r"):
                prefix = b"\n"
    with dest.open("ab") as f:
        f.write(prefix + body)


def cmd_autopilot(cfg: dict, args) -> None:
    """Общий пул: при малом остатке берёт новейший неиспользованный батч из пула и
    ДОПИСЫВАЕТ его цели в проекты ниже порога (данные не стирает). Идемпотентно по
    журналу (батч→проект). По умолчанию сухой прогон; запись — с --apply."""
    from lib import telegram

    projects_dir = Path(cfg.get("gsa_projects_dir", ""))
    if not projects_dir.is_dir():
        sys.exit(f"Папка проектов не найдена: {projects_dir}")
    pool = Path(cfg.get("autopilot_pool_dir") or cfg.get("input_share_dir", ""))
    if not pool.is_dir():
        sys.exit(f"Папка-пул батчей не найдена: {pool} "
                 "(задайте autopilot_pool_dir или input_share_dir).")
    threshold = float(cfg.get("autopilot_min_targets",
                              cfg.get("low_targets_threshold", 0)) or 0)
    if threshold <= 0:
        sys.exit("Порог не задан: autopilot_min_targets или low_targets_threshold.")
    ext = cfg.get("autopilot_append_ext", ".new_targets")
    limit = int(cfg.get("autopilot_batch_limit", 0) or 0)
    max_proj = int(cfg.get("autopilot_max_projects", 0) or 0)
    apply = args.apply

    data = collect_stats(cfg)
    for err in data["errors"]:
        print(f"⚠ {err}", file=sys.stderr)
    low = sorted((r for r in data["projects"] if r["remaining"] < threshold),
                 key=lambda r: r["remaining"])
    if max_proj:
        low = low[:max_proj]
    print(f"Порог {int(threshold):,}: проектов ниже — {len(low)}")
    if not low:
        print("Все проекты выше порога — дозаливка не нужна.")
        return

    applied = _load_applied()
    # новейший батч, который получил ещё не каждый low-проект.
    # Файлы фильтруем по autopilot_batch_glob (в пуле бывают служебные .txt).
    batch_glob = cfg.get("autopilot_batch_glob", "*.txt")
    children = [c for c in pool.iterdir()
                if c.is_dir() or (c.suffix.lower() == ".txt"
                                  and fnmatch.fnmatch(c.name, batch_glob))]
    children.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    batch = next((c for c in children
                  if any((c.name, r["name"]) not in applied for r in low)), None)
    if batch is None:
        msg = ("🟡 <b>Автопилот</b>\nПроектам мало целей, но свежих неиспользованных "
               "батчей в пуле нет — нужны новые списки.")
        print("Свежих неиспользованных батчей нет — нужны новые списки.")
        if apply:
            telegram.send(cfg, msg)
        return

    targets = _read_targets(batch, limit)
    print(f"Батч: {batch.name}  ({len(targets):,} целей)")
    if not targets:
        print("Батч пустой — пропуск.")
        return
    body = ("\n".join(targets) + "\n").encode("utf-8")

    done = []
    for r in low:
        if (batch.name, r["name"]) in applied:
            continue
        dest = projects_dir / f"{r['name']}{ext}"
        if apply:
            _append_targets_file(dest, body)
            _append_journal({"action": "append", "batch": batch.name,
                             "project": r["name"], "count": len(targets)})
        done.append(r["name"])
        tag = "+ " if apply else "(dry) "
        print(f"  {tag}{r['name']}: +{len(targets):,} → {dest.name} (было {r['remaining']:,})")

    mode = "ЗАПИСЬ" if apply else "СУХОЙ ПРОГОН (добавьте --apply)"
    print(f"[{mode}] дозалито проектов: {len(done)} из батча {batch.name}")
    if apply and done:
        telegram.send(cfg, f"🤖 <b>Автопилот</b>\nБатч {batch.name}: +{len(targets):,} "
                           f"целей в {len(done)} проект(ов).\n⚠ обновите интерфейс GSA, "
                           "чтобы он подхватил новые цели.")


def cmd_settings(cfg: dict, args) -> None:
    """Массовая правка настроек в .prj: [Options], [engines] и т.п.

    По умолчанию — сухой прогон (показывает, что изменится). Запись — только с
    --apply, с бэкапом <проект>.prj.bak (если не --no-backup).

    ⚠ Делать при ЗАКРЫТОМ GSA: он держит проекты в памяти и перезапишет .prj при
    выходе, затерев правки на диске.
    """
    from lib.prj import Prj, parse_set_spec

    # собираем спецификации правок из --set и --set-file
    raw_specs: list[str] = list(args.set or [])
    if args.set_file:
        for line in Path(args.set_file).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                raw_specs.append(line)
    if not raw_specs:
        sys.exit("Не задано ни одной правки. Пример: --set \"Options:use random url=1\"")
    try:
        specs = [parse_set_spec(s) for s in raw_specs]
    except ValueError as exc:
        sys.exit(f"Ошибка в --set: {exc}")

    target_dir = Path(args.dir or cfg.get("gsa_projects_dir", ""))
    if not target_dir.is_dir():
        sys.exit(f"Папка с .prj не найдена: {target_dir}")

    prj_files = sorted(target_dir.glob("*.prj"))
    if args.only:
        prj_files = [p for p in prj_files if args.only in p.name]
    if not prj_files:
        print(f"В {target_dir} нет .prj (фильтр --only={args.only!r})")
        return

    mode = "ЗАПИСЬ" if args.apply else "СУХОЙ ПРОГОН (без записи; добавьте --apply)"
    print(f"── settings: {mode} ──  правок: {len(specs)}, проектов: {len(prj_files)}")
    for section, key, value in specs:
        print(f"   [{section}] {key} = {value}")
    if args.apply:
        print("⚠ Убедитесь, что GSA закрыт — иначе он затрёт правки при выходе.")
    print("─" * 60)

    changed_files = 0
    for prj_path in prj_files:
        try:
            prj = Prj.load(prj_path)
        except OSError as exc:
            print(f"  ✖ {prj_path.name}: не прочитан ({exc})")
            continue
        file_changes = []
        for section, key, value in specs:
            old = prj.set_value(section, key, value)
            if old != value:
                shown = "(добавлен)" if old is None else old
                file_changes.append((section, key, shown, value))
        if not file_changes:
            continue
        changed_files += 1
        print(f"  {prj_path.name}")
        for section, key, old, new in file_changes:
            print(f"     [{section}] {key}: {old} → {new}")
        if args.apply:
            if not args.no_backup:
                prj_path.with_suffix(".prj.bak").write_bytes(prj_path.read_bytes())
            prj.save(prj_path)

    print("─" * 60)
    verb = "изменено" if args.apply else "будет изменено"
    print(f"Итог: {verb} проектов {changed_files} из {len(prj_files)}")
    if not args.apply and changed_files:
        print("Повторите с --apply, чтобы записать (GSA должен быть закрыт).")


def cmd_check(cfg: dict) -> None:
    print(f"projects_dir : {cfg.get('gsa_projects_dir')}")
    pd = Path(cfg.get("gsa_projects_dir", ""))
    print(f"  существует : {pd.is_dir()}")
    print(f"globs        : {as_list(cfg.get('target_cache_glob'))}")
    if pd.is_dir():
        by_ext: dict[str, int] = {}
        for e in pd.iterdir():
            if e.is_file():
                by_ext[e.suffix] = by_ext.get(e.suffix, 0) + 1
        print("расширения в папке проектов (файлов):")
        for ext, n in sorted(by_ext.items(), key=lambda kv: -kv[1]):
            print(f"  {ext or '(без расширения)':<14} {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description="GSA SER checker")
    ap.add_argument("--remaining", action="store_true",
                    help="остаток целей по проектам")
    ap.add_argument("--json", action="store_true", help="вывод в JSON")
    ap.add_argument("--check", action="store_true",
                    help="диагностика путей и файлов")
    ap.add_argument("--stats", action="store_true",
                    help="снимок статистики по проектам (остаток/verified/…)")
    ap.add_argument("--notify", action="store_true",
                    help="уведомления в Telegram (мало целей + heartbeat)")
    ap.add_argument("--test-telegram", action="store_true",
                    help="проверка Telegram: шлёт тестовое сообщение")
    ap.add_argument("--dry-run", action="store_true",
                    help="для --notify: печатать сообщения, не отправляя")
    ap.add_argument("--autopilot", action="store_true",
                    help="дозалить свежий батч в проекты ниже порога (общий пул)")
    ap.add_argument("--create", action="store_true",
                    help="собрать проект: .prj из шаблона + .targets из батча")
    ap.add_argument("--name", help="имя проекта (для --create)")
    ap.add_argument("--url", help="продвигаемый URL (--create)")
    ap.add_argument("--keywords", help="ключевые слова через запятую (--create)")
    ap.add_argument("--targets", help="файл или папка с целями (--create)")
    ap.add_argument("--template", help="путь к template.prj (--create)")
    ap.add_argument("--out", help="папка вывода (--create)")
    ap.add_argument("--limit", type=int, default=0, help="макс. целей (--create)")
    ap.add_argument("--force", action="store_true", help="перезаписать (--create)")
    ap.add_argument("--settings", action="store_true",
                    help="массовая правка настроек .prj ([Options]/[engines])")
    ap.add_argument("--set", action="append", default=[], metavar="СЕКЦИЯ:ключ=значение",
                    help="правка (можно несколько раз)")
    ap.add_argument("--set-file", help="файл со списком правок (строки СЕКЦИЯ:ключ=значение)")
    ap.add_argument("--dir", help="папка с .prj (по умолчанию gsa_projects_dir)")
    ap.add_argument("--only", help="только .prj, чьё имя содержит подстроку")
    ap.add_argument("--apply", action="store_true",
                    help="реально записать (иначе сухой прогон)")
    ap.add_argument("--no-backup", action="store_true",
                    help="не делать .prj.bak при --apply")
    args = ap.parse_args()

    cfg = load_config()

    if args.test_telegram:
        from lib import telegram
        raise SystemExit(telegram.test_telegram(cfg))
    if args.notify:
        cmd_notify(cfg, args)
        return
    if args.autopilot:
        cmd_autopilot(cfg, args)
        return
    if args.create:
        cmd_create(cfg, args)
        return
    if args.settings:
        cmd_settings(cfg, args)
        return
    if args.stats:
        data = collect_stats(cfg)
        augment_velocity(cfg, data)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print_stats(data)
        return
    if args.check:
        cmd_check(cfg)
        return
    if args.remaining:
        data = collect_remaining(cfg)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print_table(data)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
