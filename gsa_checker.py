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
import re
import sys
import time
from collections import Counter
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


CCTLD_COUNTRY = {
    "ru": "Russia", "ua": "Ukraine", "by": "Belarus", "kz": "Kazakhstan",
    "pl": "Poland", "de": "Germany", "fr": "France", "es": "Spain", "it": "Italy",
    "nl": "Netherlands", "be": "Belgium", "uk": "UK", "gb": "UK", "co": "Colombia",
    "br": "Brazil", "mx": "Mexico", "ar": "Argentina", "cl": "Chile", "pe": "Peru",
    "us": "USA", "ca": "Canada", "au": "Australia", "in": "India", "id": "Indonesia",
    "cn": "China", "jp": "Japan", "kr": "Korea", "tr": "Turkey", "ir": "Iran",
    "vn": "Vietnam", "th": "Thailand", "my": "Malaysia", "ph": "Philippines",
    "pt": "Portugal", "cz": "Czechia", "sk": "Slovakia", "ro": "Romania",
    "hu": "Hungary", "gr": "Greece", "se": "Sweden", "no": "Norway", "fi": "Finland",
    "dk": "Denmark", "at": "Austria", "ch": "Switzerland", "za": "South Africa",
}
GTLDS = {"com", "net", "org", "info", "biz", "xyz", "online", "site", "shop",
         "dev", "app", "io", "co", "top", "club", "pro", "me", "tv", "cc"}
_HOST_RE = re.compile(r"https?://([^/:]+)", re.I)


def _country_of(url: str, geo: dict | None) -> tuple[str, str]:
    """(страна, источник) для verified-ссылки. Сначала ccTLD (как в GSA); для gTLD —
    если задан geo, добираем по IP-GeoIP. Источник: 'tld' | 'ip'."""
    m = _HOST_RE.search(url or "")
    if not m:
        return "", "—"
    host = m.group(1)
    tld = host.rsplit(".", 1)[-1].lower()
    if tld not in GTLDS:
        return CCTLD_COUNTRY.get(tld, tld.upper()), "tld"
    if geo:                                          # gTLD → пробуем GeoIP по IP
        from lib import geoip
        code = geoip.country_iso(host, geo["db"], geo["cache"], geo["timeout"])
        if code:
            return CCTLD_COUNTRY.get(code.lower(), code.upper()), "ip"
    return "gTLD", "tld"


def _parse_success(raw: bytes):
    """Строка .success (поля через 0xFF) → (url, date, engine, type, anchor, target)."""
    f = [p.decode("utf-8", "replace") for p in raw.split(b"\xff")]
    while len(f) < 5:
        f.append("")
    return f[0], f[1], f[2], f[3], f[4], (f[-1] if len(f) > 5 else "")


def _find_col(header: list, name: str) -> int:
    clean = [(h or "").strip().lstrip("﻿").lower() for h in header]
    name = name.strip().lower()
    return clean.index(name) if name in clean else -1


def cmd_geocheck(cfg: dict, args) -> None:
    """Сверка стран: колонка Country из GSA-выгрузки ПРОТИВ нашего GeoIP по тому же IP.
    Read-only (базу не трогает). Показывает % совпадения бакетов, добор GeoIP по
    «Not Stated» и топ расхождений — чтобы решить, можно ли доверять пути без UI."""
    import csv
    from collections import Counter
    from lib import buckets, geoip

    src = Path(args.csv or cfg.get("report_input", ""))
    if not src.exists():
        sys.exit(f"CSV не найдена: {src} (--csv ПУТЬ)")
    if src.is_dir():
        cands = sorted(src.rglob("*.csv"))
        if not cands:
            sys.exit(f"В {src} нет *.csv")
        src = cands[-1]          # самая свежая по имени
    geo_db = cfg.get("geoip_db", "")
    if not geoip.available(geo_db):
        sys.exit(f"GeoIP недоступен (geoip_db={geo_db!r}). Нужен .mmdb + pip install maxminddb.")
    gp = DATA_DIR / "geoip_cache.json"
    cache = geoip.load_cache(gp)

    agree = disagree = recovered = geo_miss = both_none = 0
    mism = Counter()
    with src.open(encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192); fh.seek(0)
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delim = ","
        reader = csv.reader(fh, delimiter=delim)
        header = next(reader, None) or []
        ic = _find_col(header, "Country")
        ip = _find_col(header, "IP")
        if ic < 0 or ip < 0:
            sys.exit(f"Нужны колонки Country и IP. Шапка: {header}")
        for row in reader:
            if len(row) <= max(ic, ip):
                continue
            gb = buckets.bucket_for_country(buckets.resolve_country(row[ic]))
            name = geoip.country_name_by_ip(row[ip], geo_db, cache)
            eb = buckets.bucket_for_country(buckets.resolve_country(name)) if name else None
            gsa_ns = gb == buckets.NOT_STATED_FILE
            geo_ns = eb is None or eb == buckets.NOT_STATED_FILE
            if not gsa_ns and not geo_ns:
                if gb == eb:
                    agree += 1
                else:
                    disagree += 1
                    mism[f"{gb} → {eb}"] += 1
            elif gsa_ns and not geo_ns:
                recovered += 1
            elif not gsa_ns and geo_ns:
                geo_miss += 1
            else:
                both_none += 1
    geoip.save_cache(gp, cache)

    determined = agree + disagree
    pct = (agree / determined * 100) if determined else 0.0
    print(f"GeoIP-сверка: {src.name}")
    print(f"Обе стороны определили страну: {determined:,} | "
          f"СОГЛАСИЕ бакетов: {agree:,} ({pct:.1f}%), расхождений: {disagree:,}")
    print(f"GSA «Not Stated», GeoIP определил (добор): {recovered:,}")
    print(f"GSA определил, GeoIP нет (нет IP/записи): {geo_miss:,}")
    print(f"Обе не определили: {both_none:,}")
    if mism:
        print("── топ расхождений (бакет GSA → бакет GeoIP) ──")
        for pair, n in mism.most_common(15):
            print(f"  {pair}: {n:,}")


def _iter_verified(cfg: dict, args):
    """Генератор строк verified: (url, имя_страны, ip). Два источника:
      • по умолчанию — файлы `.success` проектов (диск, БЕЗ UI): страна = код ISO2 из
        предпоследнего поля (её проставил сам GSA: ccTLD, а для gTLD — по IP), IP — в
        последнем поле; поля разделены байтом 0xFF;
      • если задан --csv — GSA verified CSV (колонки URL/Country/IP)."""
    import csv
    from lib import iso2
    if args.csv:
        src = Path(args.csv)
        if not src.exists():
            sys.exit(f"CSV не найдена: {src}")
        files = sorted(src.rglob("*.csv")) if src.is_dir() else [src]
        if not files:
            sys.exit(f"В {src} нет *.csv")
        print(f"Источник: GSA verified CSV — {len(files)} файл(ов)")
        for f in files:
            with f.open(encoding="utf-8-sig", newline="") as fh:
                sample = fh.read(8192)
                fh.seek(0)
                try:
                    delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
                except csv.Error:
                    delim = ","
                reader = csv.reader(fh, delimiter=delim)
                header = next(reader, None)
                if not header:
                    print(f"⚠ {f.name}: пустой файл — пропуск", file=sys.stderr)
                    continue
                ic = _find_col(header, "Country")
                iu = _find_col(header, "URL")
                ip = _find_col(header, "IP")
                if ic < 0 or iu < 0:
                    print(f"⚠ {f.name}: нет колонок Country/URL. Шапка: {header}",
                          file=sys.stderr)
                    continue
                for row in reader:
                    if len(row) <= max(ic, iu):
                        continue
                    ipv = row[ip] if 0 <= ip < len(row) else ""
                    yield row[iu], row[ic], ipv
        return

    # источник по умолчанию — .success с диска
    proj = Path(cfg.get("gsa_projects_dir", ""))
    if not proj.is_dir():
        sys.exit(f"Папка проектов не найдена: {proj} (gsa_projects_dir в конфиге)")
    globs = cfg.get("verified_glob", ["*.success"])
    files = sorted({p for g in globs for p in proj.glob(g)})
    if not files:
        sys.exit(f"В {proj} нет verified-файлов ({globs}). Проверь gsa_projects_dir/"
                 f"verified_glob (--check покажет реальные расширения).")
    print(f"Источник: .success с диска — {len(files)} проект(ов)")
    for f in files:
        try:
            data = f.read_bytes()
        except OSError as e:
            print(f"⚠ {f.name}: {e}", file=sys.stderr)
            continue
        for line in data.split(b"\n"):
            if not line.strip():
                continue
            fl = line.split(b"\xff")
            if len(fl) < 3:
                continue
            url = fl[0].decode("utf-8", "replace").strip()
            code = fl[-2].decode("utf-8", "replace").strip()
            ipv = fl[-1].decode("utf-8", "replace").strip()
            yield url, iso2.name_for(code), ipv


def cmd_report(cfg: dict, args) -> None:
    """Недельная статистика verified — ЗАМЕНА ручного split1404, БЕЗ UI. Источник по
    умолчанию — файлы `.success` проектов (страна = код GSA на диске); либо GSA-CSV через
    --csv. Раскладывает по странам-бакетам (логика split1404 1:1), инкрементно дописывает
    новые URL в базу out_country_buckets (`buckets_dir`, дедуп per-file/global/in-run),
    добивает оставшиеся «Not Stated» по IP через GeoIP, формирует сводку формата
    debug_summary (страна ВСЕГО (+новых) … ИТОГО), пишет в report_out_dir и шлёт в Telegram."""
    from collections import defaultdict
    from lib import buckets, telegram

    bdir = Path(cfg.get("buckets_dir") or (DATA_DIR / "out_country_buckets"))
    bdir.mkdir(parents=True, exist_ok=True)
    dry = getattr(args, "dry_run", False)

    # текущая база: множества URL для дедупа + счётчики «было»
    per_file, global_set = buckets.read_membership(bdir)
    pre = {fn: buckets.count_nonempty_lines(bdir / fn) for fn, _ in buckets.SUMMARY_ORDER}
    pre[buckets.NOT_STATED_FILE] = buckets.count_nonempty_lines(bdir / buckets.NOT_STATED_FILE)

    # GeoIP по IP для оставшихся «Not Stated» — опционально
    geo = None
    geo_db = cfg.get("geoip_db", "")
    if geo_db:
        from lib import geoip
        if geoip.available(geo_db):
            gp = DATA_DIR / "geoip_cache.json"
            geo = {"db": geo_db, "cache": geoip.load_cache(gp), "path": gp}

    to_append = defaultdict(list)
    added = defaultdict(int)
    planned = defaultdict(set)
    total_rows = nonempty = filled = 0
    skip_target = skip_global = skip_dup = skip_empty = 0

    for url_raw0, country_name, ip_str in _iter_verified(cfg, args):
        total_rows += 1
        url_raw = buckets.norm(url_raw0)
        if not url_raw:
            skip_empty += 1
            continue
        nonempty += 1
        bucket = buckets.bucket_for_country(buckets.resolve_country(country_name))
        if bucket == buckets.NOT_STATED_FILE and geo and ip_str:
            from lib import geoip
            name = geoip.country_name_by_ip(ip_str, geo["db"], geo["cache"])
            if name:
                nb = buckets.bucket_for_country(buckets.resolve_country(name))
                if nb != buckets.NOT_STATED_FILE:
                    bucket, filled = nb, filled + 1
        url_k = buckets.norm_url(url_raw)
        if url_k in per_file.get(bucket, set()):
            skip_target += 1
            continue
        if url_k in global_set:
            skip_global += 1
            continue
        if url_k in planned[bucket]:
            skip_dup += 1
            continue
        to_append[bucket].append(url_raw)
        planned[bucket].add(url_k)
        added[bucket] += 1
        global_set.add(url_k)
        per_file[bucket].add(url_k)

    if geo:
        from lib import geoip
        geoip.save_cache(geo["path"], geo["cache"])

    # дописать новые URL в файлы базы (как split1404 — с гарантией \n перед дозаписью)
    if not dry:
        for fn in buckets.all_bucket_files():
            (bdir / fn).touch(exist_ok=True)
        for bucket_file, urls in to_append.items():
            if not urls:
                continue
            path = bdir / bucket_file
            with path.open("a", encoding="utf-8", newline="") as f:
                try:
                    if path.stat().st_size > 0:
                        with path.open("rb") as fb:
                            fb.seek(-1, 2)
                            if fb.read(1) != b"\n":
                                f.write("\n")
                except OSError:
                    pass
                f.write("\n".join(urls) + "\n")

    total_added = sum(added.values())
    post = dict(pre)
    for fn, n in added.items():
        post[fn] = post.get(fn, 0) + n
    total_lines = sum(post.get(fn, 0) for fn, _ in buckets.SUMMARY_ORDER)
    total_lines += post.get(buckets.NOT_STATED_FILE, 0)

    # сводка формата debug_summary (split1404)
    head = (f"Всего строк verified: {total_rows}\n"
            f"Строк с непустым URL (без дедупликации): {nonempty}\n"
            f"Добавлено новых URL (после дедупликации): {total_added}\n"
            f"Пропущено (уже есть в целевом файле): {skip_target}\n"
            f"Пропущено (уже существует в другом файле): {skip_global}\n"
            f"Пропущено (дубликат в текущем запуске): {skip_dup}\n"
            f"Пропущено (пустой URL): {skip_empty}\n"
            f"GeoIP-добор Not Stated: {filled}\n")
    body_lines = [f"{label} {post.get(fn, 0)} {buckets.fmt_added(added.get(fn, 0))}"
                  for fn, label in buckets.SUMMARY_ORDER]
    ns_line = (f"Не указано {post.get(buckets.NOT_STATED_FILE, 0)} "
               f"{buckets.fmt_added(added.get(buckets.NOT_STATED_FILE, 0))}")
    summary = head + "\n" + "\n".join(body_lines) + f"\n\n{ns_line}\n\nИТОГО {total_lines}\n"

    print(summary + ("\n[dry-run: база не изменена]" if dry else ""))

    out_dir = Path(cfg.get("report_out_dir") or (DATA_DIR / "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    rep = out_dir / f"gsa_report_{cfg.get('server_name', 'gsa')}_{stamp}.txt"
    if not dry:
        rep.write_text(summary, encoding="utf-8")
        print(f"✓ отчёт: {rep}")

    # Telegram — тот же формат (страна ВСЕГО (+новых) … ИТОГО)
    label = telegram.server_label(cfg) if hasattr(telegram, "server_label") else \
        cfg.get("server_name", "gsa")
    tg = (f"📊 <b>GSA verified — недельная сводка</b> ({label})\n"
          f"Добавлено новых: <b>{total_added}</b>\n\n"
          + "\n".join(body_lines) + f"\n\n{ns_line}\n\n<b>ИТОГО {total_lines}</b>")
    telegram.send(cfg, tg)


def cmd_ui_export(cfg: dict, args) -> None:
    """Выгружает verified-CSV из GSA через UI (--ui-export) в папку report_input, чтобы
    его сразу подхватил --report. Если задан и --report — сразу считает статистику по
    свежему файлу (замыкает цикл выгрузка → раскладка по бакетам → Telegram)."""
    import logging
    from lib import ui
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("gsa_checker")

    base = Path(cfg.get("report_input") or cfg.get("report_out_dir") or DATA_DIR)
    if base.suffix.lower() == ".csv":
        base = base.parent
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    out = base / f"Verified_{cfg.get('server_name', 'gsa')}_{stamp}.csv"

    ok = ui.export_verified(cfg, out, log)
    if not ok:
        sys.exit("UI-выгрузка не удалась (см. лог выше). Настройте ui_export_* по --ui-check.")
    print(f"✓ verified-CSV выгружен: {out}")
    if args.report:
        args.csv = str(out)
        cmd_report(cfg, args)


def cmd_export(cfg: dict, args) -> None:
    """Выгрузка verified-результатов (`.success`) в CSV со страной (по ccTLD) на шару.
    Инкрементально: по офсету в state читает только новые строки с прошлого прогона.
    По умолчанию пишет; --dry-run — превью без записи и без сдвига офсета; --full —
    выгрузить весь `.success` (офсет всё равно сдвигается в конец)."""
    import csv
    import time
    from lib import telegram

    projects_dir = Path(cfg.get("gsa_projects_dir", ""))
    if not projects_dir.is_dir():
        sys.exit(f"Папка проектов не найдена: {projects_dir}")
    export_dir = Path(cfg.get("export_dir") or (DATA_DIR / "export"))
    globs = as_list(cfg.get("verified_glob", ["*.success"]))
    dry = getattr(args, "dry_run", False)
    full = getattr(args, "full", False)

    # GeoIP для gTLD-доменов (страна не из ccTLD, а по IP) — опционально
    geo = None
    geo_db = cfg.get("geoip_db", "")
    if geo_db:
        from lib import geoip
        if geoip.available(geo_db):
            geo_cache_path = DATA_DIR / "geoip_cache.json"
            geo = {"db": geo_db, "cache": geoip.load_cache(geo_cache_path),
                   "timeout": float(cfg.get("geoip_timeout", 3) or 3),
                   "path": geo_cache_path}
        else:
            print("⚠ GeoIP отключён: нет базы geoip_db или не установлен maxminddb "
                  "(pip install maxminddb). Страна только по ccTLD.", file=sys.stderr)

    state = load_state()
    offsets = state.setdefault("export_offsets", {})
    rows, per_proj = [], {}
    for prj in sorted(projects_dir.glob("*.prj")):
        base = prj.stem
        for pattern in globs:
            sf = projects_dir / f"{base}{pattern.lstrip('*')}"
            if not sf.is_file():
                continue
            size = sf.stat().st_size
            start = 0 if full else offsets.get(base, 0)
            if start > size:                       # файл усечён/ротирован
                start = 0
            if start >= size:
                offsets[base] = size
                continue
            with sf.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read()
            offsets[base] = size
            for raw in chunk.split(b"\n"):
                raw = raw.rstrip(b"\r")
                if not raw:
                    continue
                url, date, engine, typ, anchor, target = _parse_success(raw)
                if not url:
                    continue
                country, src = _country_of(url, geo)
                rows.append({"project": base, "country": country, "country_src": src,
                             "url": url, "date": date, "engine": engine,
                             "type": typ, "anchor": anchor, "target": target})
                per_proj[base] = per_proj.get(base, 0) + 1

    if geo:                                        # сохранить накопленный GeoIP-кэш
        from lib import geoip
        geoip.save_cache(geo["path"], geo["cache"])
    print(f"Новых verified-ссылок: {len(rows)} из {len(per_proj)} проект(ов)")
    if not rows:
        print("Новых результатов нет.")
        if not dry:
            save_state(state)                      # зафиксировать офсеты (файлы дочитаны)
        return
    if dry:
        for r in rows[:8]:
            print(f"  [{r['country']}/{r['country_src']}] {r['project']}: {r['url'][:66]}")
        print("(dry-run: CSV не пишется, офсеты не двигаются)")
        return

    export_dir.mkdir(parents=True, exist_ok=True)
    fname = (f"gsa_verified_{cfg.get('server_name', 'gsa')}_"
             f"{time.strftime('%Y-%m-%d_%H%M%S')}.csv")
    out = export_dir / fname
    cols = ["project", "country", "country_src", "url", "date",
            "engine", "type", "anchor", "target"]
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    save_state(state)                              # офсеты — только после успешной записи
    top = ", ".join(f"{c}:{n}" for c, n in
                    sorted(Counter(r["country"] for r in rows).items(),
                           key=lambda kv: -kv[1])[:5])
    print(f"✓ выгружено {len(rows):,} ссылок → {out}")
    print(f"  по странам (топ-5): {top}")
    telegram.send(cfg, f"📤 <b>Экспорт verified</b>\n{len(rows):,} новых ссылок "
                       f"из {len(per_proj)} проект(ов)\nтоп стран: {top}\n→ {export_dir}")


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


_URL_RE = re.compile(r"^https?://\S", re.I)


def _is_url(s: str) -> bool:
    """Цель валидна, только если это URL (http/https), а не обычная строка/ключи."""
    return bool(_URL_RE.match(s))


def _collect_urls(path: Path, seen: set, limit: int = 0):
    """URL-строки из файла (только http(s)://), дедуп через seen. Возвращает
    (urls, total, valid): total — непустых строк, valid — из них URL."""
    total = valid = 0
    urls: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            u = line.strip()
            if not u:
                continue
            total += 1
            if not _is_url(u):
                continue
            valid += 1
            if u not in seen:
                seen.add(u)
                urls.append(u)
                if limit and len(urls) >= limit:
                    break
    return urls, total, valid


def _read_targets(src: Path, limit: int) -> list[str]:
    """Цели из файла или папки (*.txt): только URL (http/https), дедуп. Не-URL строки
    пропускаются; файлы без единого URL — предупреждение (вероятно, не тот файл)."""
    files = []
    if src.is_dir():
        files = sorted(p for p in src.rglob("*.txt") if p.is_file())
    elif src.is_file():
        files = [src]
    seen: set[str] = set()
    out: list[str] = []
    skipped, bad_files = 0, []
    for f in files:
        rem = (limit - len(out)) if limit else 0
        if limit and rem <= 0:
            break
        urls, total, valid = _collect_urls(f, seen, rem)
        skipped += (total - valid)
        if total > 0 and valid == 0:
            bad_files.append(f.name)
        out.extend(urls)
    if skipped:
        print(f"⚠ пропущено не-URL строк: {skipped:,}", file=sys.stderr)
    if bad_files:
        print(f"⚠ без единого URL (не список ссылок?): {', '.join(bad_files[:5])}"
              + (" …" if len(bad_files) > 5 else ""), file=sys.stderr)
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


def _load_consumed_batches() -> set:
    """Имена батчей, уже разобранных автопилотом (перенесённых в used)."""
    consumed = set()
    if AUTOPILOT_JOURNAL.exists():
        for line in AUTOPILOT_JOURNAL.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("action") == "batch" and "batch" in rec:
                consumed.add(rec["batch"])
    return consumed


def _email_reminder(cfg: dict, telegram) -> None:
    """Раз в email_reminder_days шлёт в Telegram напоминание обновить почты (сами
    почты не трогаем при работающем GSA)."""
    import time
    state = load_state()
    days = float(cfg.get("email_reminder_days", 30) or 30)
    now = time.time()
    last = state.get("email_reminder_ts")
    if last is None:
        state["email_reminder_ts"] = now
    elif (now - last) >= days * 86400:
        if telegram.send(cfg, "📧 <b>Пора обновить почты</b>\nЗакройте GSA и выполните:\n"
                              "python gsa_checker.py --emails --apply"):
            state["email_reminder_ts"] = now
    save_state(state)


def _distribute(cfg, projects_dir, eligible, selected, ext, apply, total_bytes, telegram) -> None:
    """Собирает цели из выбранных батчей (дедуп) и раздаёт ПОРОВНУ по проектам."""
    seen, targets = set(), []
    skipped, bad = 0, []
    for f in selected:
        urls, total, valid = _collect_urls(f, seen)
        skipped += (total - valid)
        if total > 0 and valid == 0:
            bad.append(f.name)
        targets.extend(urls)
    if skipped:
        print(f"⚠ пропущено не-URL строк: {skipped:,}", file=sys.stderr)
    if bad:
        print(f"⚠ батчи без URL (пропущены как цели): {', '.join(bad[:5])}", file=sys.stderr)
    n = len(eligible)
    names = ", ".join(f.name for f in selected[:5]) + (" …" if len(selected) > 5 else "")
    print(f"Батчи ({len(selected)}, ~{total_bytes/1048576:.0f} МБ): {names}")
    print(f"Целей {len(targets):,} → поровну на {n} проект(ов) (~{len(targets)//n if n else 0:,}/проект)")
    if not targets:
        print("Целей нет — пропуск.")
        return

    print(f"── автопилот: {'ЗАПИСЬ' if apply else 'СУХОЙ ПРОГОН (добавьте --apply)'} ──")
    for i, r in enumerate(eligible):
        chunk = targets[i::n]                       # round-robin — ровная раздача
        if not chunk:
            continue
        dest = projects_dir / f"{r['name']}{ext}"
        if apply:
            _append_targets_file(dest, ("\n".join(chunk) + "\n").encode("utf-8"))
        print(f"  {'+ ' if apply else '(dry) '}{r['name']}: +{len(chunk):,} → "
              f"{dest.name} (было {r['remaining']:,})")

    if not apply:
        return
    # перенос разобранных батчей в used + журнал
    import shutil
    used_dir = Path(cfg.get("autopilot_used_dir")
                    or (selected[0].parent.parent / "Aparser results used"))
    used_dir.mkdir(parents=True, exist_ok=True)
    for f in selected:
        try:
            shutil.move(str(f), str(used_dir / f.name))
        except OSError as e:
            print(f"⚠ не перенесён {f.name}: {e}", file=sys.stderr)
        _append_journal({"action": "batch", "batch": f.name})
    # один рефреш GSA в конце
    try:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from lib import ui
        ui.refresh(cfg, logging.getLogger("gsa_checker"))
    except SystemExit:
        print("⚠ рефреш пропущен (pywinauto не установлен / не Windows).")
    except Exception as e:
        print(f"⚠ рефреш не удался: {e}", file=sys.stderr)
    telegram.send(cfg, f"🤖 <b>Автопилот</b>\n{len(targets):,} целей поровну в {n} "
                       f"проект(ов) (~{len(targets)//n:,}/проект), батчей {len(selected)}. "
                       "Рефреш выполнен.")


def cmd_autopilot(cfg: dict, args) -> None:
    """Server-9 модель: равномерно раздаёт цели из общего пула в АКТИВНЫЕ проекты
    (исключая имена из autopilot_exclude_names — по имени, т.к. `last status` в .prj не
    различает active/inactive). При остатке ниже autopilot_min_targets берёт новейшие
    неиспользованные батчи из пула (до autopilot_batch_limit_mb суммарно), делит их цели
    ПОРОВНУ (каждому свой кусок, данные не стирает), переносит батчи в autopilot_used_dir
    и при --apply делает один --ui-refresh. Раз в месяц — напоминание обновить почты."""
    from lib import telegram

    projects_dir = Path(cfg.get("gsa_projects_dir", ""))
    if not projects_dir.is_dir():
        sys.exit(f"Папка проектов не найдена: {projects_dir}")
    pool = Path(cfg.get("autopilot_pool_dir") or cfg.get("input_share_dir", ""))
    if not pool.is_dir():
        sys.exit(f"Папка-пул батчей не найдена: {pool}")
    threshold = float(cfg.get("autopilot_min_targets",
                              cfg.get("low_targets_threshold", 0)) or 0)
    if threshold <= 0:
        sys.exit("Порог не задан: autopilot_min_targets.")
    ext = cfg.get("autopilot_append_ext", ".new_targets")
    apply = args.apply

    data = collect_stats(cfg)
    for err in data["errors"]:
        print(f"⚠ {err}", file=sys.stderr)

    excl = [e.lower() for e in as_list(cfg.get("autopilot_exclude_names",
                                               ["CC", "TEST", "Common"]))]
    eligible = sorted((r for r in data["projects"]
                       if not any(e in r["name"].lower() for e in excl)),
                      key=lambda r: r["name"])
    print(f"Проектов всего {len(data['projects'])}, кормим {len(eligible)} "
          f"(исключаем содержащие: {', '.join(excl) or '—'})")

    if eligible:
        min_left = min(r["remaining"] for r in eligible)
        if min_left >= threshold:
            print(f"Мин. остаток {min_left:,} ≥ порога {int(threshold):,} — дозаливка не нужна.")
        else:
            consumed = _load_consumed_batches()
            cap = int(float(cfg.get("autopilot_batch_limit_mb", 120) or 120) * 1024 * 1024)
            glob = cfg.get("autopilot_batch_glob", "*.txt")
            files = sorted((c for c in pool.iterdir()
                            if c.is_file() and c.suffix.lower() == ".txt"
                            and fnmatch.fnmatch(c.name, glob) and c.name not in consumed),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            selected, total = [], 0
            for f in files:
                selected.append(f)
                total += f.stat().st_size
                if total >= cap:
                    break
            if not selected:
                print("Свежих неиспользованных батчей нет — нужны новые списки.")
                if apply:
                    telegram.send(cfg, "🟡 <b>Автопилот</b>\nПроектам мало целей, но свежих "
                                       "батчей в пуле нет — нужны новые списки.")
            else:
                _distribute(cfg, projects_dir, eligible, selected, ext, apply, total, telegram)
    else:
        print("Нет подходящих проектов (все исключены).")

    if apply:
        _email_reminder(cfg, telegram)


def cmd_emails(cfg: dict, args) -> None:
    """Обновляет секцию [email accounts] в .prj свежими почтами (уникальный набор на
    проект). Формат нативный для GSA (разделитель 0xFF). Остальное .prj не трогает.

    ⚠ Как и --settings: делать при ЗАКРЫТОМ GSA (иначе перезапишет при выходе)."""
    from lib.prj import Prj
    from lib import emails as em

    count = int(args.count or cfg.get("emails_per_project", 20) or 20)
    provider = cfg.get("email_provider_ini", em.DEFAULT_PROVIDER)
    domains = as_list(cfg.get("email_domains", [])) or em.DEFAULT_DOMAINS

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
    print(f"── emails: {mode} ──  почт/проект: {count}, провайдер: {provider}, "
          f"проектов: {len(prj_files)}")
    if args.apply:
        print("⚠ Убедитесь, что GSA закрыт — иначе он затрёт правки при выходе.")
    print("─" * 60)

    done = 0
    for prj_path in prj_files:
        try:
            prj = Prj.load(prj_path)
        except OSError as exc:
            print(f"  ✖ {prj_path.name}: не прочитан ({exc})")
            continue
        old_n = len(prj.list_keys("email accounts"))
        lines = em.build_account_lines(count, provider, domains, used=set())
        prj.replace_section("email accounts", lines)
        done += 1
        print(f"  {'+ ' if args.apply else '(dry) '}{prj_path.name}: почт {old_n} → {count}")
        if args.apply:
            if not args.no_backup:
                prj_path.with_suffix(".prj.bak").write_bytes(prj_path.read_bytes())
            prj.save(prj_path)

    print("─" * 60)
    verb = "обновлено" if args.apply else "будет обновлено"
    print(f"Итог: {verb} проектов {done} из {len(prj_files)}")
    if not args.apply and done:
        print("Повторите с --apply, чтобы записать (GSA должен быть закрыт).")


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
    ap.add_argument("--report", action="store_true",
                    help="статистика по бакетам стран из GSA verified CSV (+GeoIP по IP)")
    ap.add_argument("--csv", help="путь к GSA verified CSV или папке (для --report)")
    ap.add_argument("--geocheck", action="store_true",
                    help="сверка Country из GSA-CSV против нашего GeoIP по IP (read-only)")
    ap.add_argument("--export", action="store_true",
                    help="выгрузить verified-результаты в CSV (страна по ccTLD) на шару")
    ap.add_argument("--full", action="store_true",
                    help="для --export: весь .success, а не только новое")
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
    ap.add_argument("--ui-check", action="store_true",
                    help="диагностика окна GSA (pywinauto) → data/ui_controls.txt")
    ap.add_argument("--ui-refresh", action="store_true",
                    help="толкнуть GSA подхватить новые цели (pywinauto)")
    ap.add_argument("--ui-export", action="store_true",
                    help="выгрузить verified-CSV из GSA через UI (потом можно --report)")
    ap.add_argument("--emails", action="store_true",
                    help="обновить [email accounts] в .prj свежими почтами")
    ap.add_argument("--count", type=int, default=0,
                    help="почт на проект (--emails; по умолчанию emails_per_project)")
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
    if args.geocheck:
        cmd_geocheck(cfg, args)
        return
    if args.report:
        cmd_report(cfg, args)
        return
    if args.export:
        cmd_export(cfg, args)
        return
    if args.notify:
        cmd_notify(cfg, args)
        return
    if args.autopilot:
        cmd_autopilot(cfg, args)
        return
    if args.create:
        cmd_create(cfg, args)
        return
    if args.ui_check:
        from lib import ui
        ui.ui_check(cfg, DATA_DIR)
        return
    if args.ui_refresh:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        from lib import ui
        ui.refresh(cfg, logging.getLogger("gsa_checker"))
        return
    if args.ui_export:
        cmd_ui_export(cfg, args)
        return
    if args.emails:
        cmd_emails(cfg, args)
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
