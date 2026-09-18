#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""xrumer_autopilot.py — долив целей в базу XRumer, аналог GSA `--autopilot --apply`.

Схема повторяет основные проекты GSA (см. docs/xrumer-mcp.md и разбор gsa_checker
cmd_autopilot), но под модель XRumer «одна база на копию»:

  общий пул A-Parser  →  атомарный захват батча  →  дедуп URL  →
  дозапись в локальную базу-мастер  →  выгрузка базы в C:\\Links ноды по SMB

КООРДИНАЦИЯ С GSA (общий пул). Батч забирается ФИЗИЧЕСКИМ переносом из
`pool_dir` в `used_dir` — ровно как node-side автопилот GSA. Перенос атомарен:
батч, уже перенесённый GSA (по SMB) или XRumer (локально), второй участник не
увидит. Отдельный общий журнал не нужен — перенос сам и есть координация.

МОДЕЛЬ «ОДНА БОЛЬШАЯ БАЗА». Автопилот владеет ОДНИМ файлом-базой на ноду
(`base`, напр. Autopilot.txt в C:\\Links). Локальная копия-мастер лежит на шаре
(`local_base_dir/<node>/<base>`): дозапись туда дёшева, XRumer-нода получает
целый файл заливкой. Оператор эту базу руками не правит (её ведёт автопилот);
кураторские базы (Posting/Trusted/Profiles) не трогаются. Рост ограничен
`max_base_lines` (обрезка старых строк с головы). XRumer сам дедупит базу при
загрузке, поэтому здесь дедуп — в пределах прогона (как `seen` у GSA).

БЕЗОПАСНОСТЬ. По умолчанию dry-run: НИЧЕГО не переносит, не пишет, не заливает —
только показывает, какие батчи забрал бы и сколько новых URL добавил бы. Реальный
долив — только с `--apply`. Заливка базы поверх файла ноды — единственная
перезапись; она не трогает других баз и не касается открытого в XRumer проекта.

Запуск (с шары, где лежит пул):
  python xrumer_autopilot.py --node gsa-03            # dry-run
  python xrumer_autopilot.py --node gsa-03 --apply    # боевой долив
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib import xrumer as xr  # noqa: E402

CONFIG_PATH = ROOT / "data" / "gsa_checker.config.json"
JOURNAL_PATH = ROOT / "data" / "xrumer_autopilot.jsonl"
URL_RE = re.compile(rb"^https?://", re.IGNORECASE)

DEFAULTS = {
    "pool_dir": "/srv/share/Aparser results",
    "used_dir": "/srv/share/Aparser results used",
    "batch_glob": "*.txt",
    "batch_limit_mb": 120.0,
    "base": "Autopilot.txt",
    "local_base_dir": "/srv/share/xrumer/bases",
    "max_base_lines": 1_000_000,
}


def load_cfg() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def ap_settings(cfg: dict) -> dict:
    """Слияние DEFAULTS ← cfg['xrumer']['autopilot']."""
    xr_sec = cfg.get("xrumer") if isinstance(cfg.get("xrumer"), dict) else {}
    ap = xr_sec.get("autopilot") if isinstance(xr_sec.get("autopilot"), dict) else {}
    out = dict(DEFAULTS)
    for k, v in ap.items():
        if k in out and v is not None:
            out[k] = v
    return out


def journal(entry: dict) -> None:
    try:
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def select_batches(pool: Path, glob: str, limit_mb: float) -> list[Path]:
    """Батчи пула, новейшие первыми, накопление до limit_mb (как у GSA)."""
    files = [p for p in pool.glob(glob) if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    chosen, acc = [], 0.0
    for p in files:
        chosen.append(p)
        acc += p.stat().st_size / (1024 * 1024)
        if acc >= limit_mb:
            break
    return chosen


def collect_urls(paths: list[Path], seen: set[bytes]) -> tuple[list[bytes], int]:
    """URL из батчей (только http/https), дедуп в пределах прогона. (urls, skipped)."""
    urls, skipped = [], 0
    for p in paths:
        try:
            data = p.read_bytes()
        except OSError:
            continue
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            if not URL_RE.match(line):
                skipped += 1
                continue
            if line in seen:
                continue
            seen.add(line)
            urls.append(line)
    return urls, skipped


def claim(batch: Path, used_dir: Path) -> bool:
    """Атомарный захват: перенос в used_dir. False = батч уже забрал кто-то (GSA)."""
    used_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(batch), str(used_dir / batch.name))
        return True
    except (FileNotFoundError, OSError):
        return False


def append_local_base(base_path: Path, urls: list[bytes], max_lines: int) -> int:
    """Дозапись URL в локальную базу-мастер, обрезка с головы до max_lines. Возвращает итоговый размер (строк)."""
    base_path.parent.mkdir(parents=True, exist_ok=True)
    existing = base_path.read_bytes().splitlines() if base_path.exists() else []
    combined = existing + urls
    if max_lines and len(combined) > max_lines:
        combined = combined[len(combined) - max_lines:]     # держим самые свежие
    base_path.write_bytes(b"\n".join(combined) + (b"\n" if combined else b""))
    return len(combined)


def run(node: str, apply: bool) -> dict:
    cfg = load_cfg()
    ap = ap_settings(cfg)
    _, files, section = xr.from_config(cfg, ROOT)
    if not section:
        raise SystemExit("В конфиге нет секции xrumer (см. config.example.json).")
    if files is None:
        raise SystemExit("Не настроен SMB-слой (xrumer.smb_host/smb_cred) — залить базу некуда.")

    pool = Path(ap["pool_dir"])
    used = Path(ap["used_dir"])
    if not pool.is_dir():
        raise SystemExit(f"Пул не найден: {pool}")
    local_base = Path(ap["local_base_dir"]) / node / ap["base"]

    batches = select_batches(pool, ap["batch_glob"], float(ap["batch_limit_mb"]))
    total_mb = round(sum(p.stat().st_size for p in batches) / (1024 * 1024), 1)

    report = {"node": node, "apply": apply, "pool": str(pool),
              "batches_selected": len(batches), "batches_mb": total_mb,
              "base_node": f"Links\\{ap['base']}", "base_local": str(local_base)}

    if not apply:
        # dry-run: не переносим и не читаем всё, оцениваем по первому батчу
        sample = batches[:1]
        seen: set[bytes] = set()
        urls, skipped = collect_urls(sample, seen)
        report["dry_run"] = True
        report["sample_batch"] = sample[0].name if sample else None
        report["sample_urls"] = len(urls)
        report["note"] = ("dry-run: ничего не перенесено/залито; при --apply забрал бы "
                          f"{len(batches)} батчей (~{total_mb} МБ) и дозаписал бы в базу")
        return report

    # боевой прогон
    seen = set()
    claimed, all_urls, skipped_total = [], [], 0
    for b in batches:
        if not claim(b, used):                    # GSA успел раньше — пропускаем
            continue
        claimed.append(b.name)
        urls, sk = collect_urls([used / b.name], seen)
        all_urls.extend(urls)
        skipped_total += sk
        journal({"action": "batch", "node": node, "batch": b.name})

    base_size = append_local_base(local_base, all_urls, int(ap["max_base_lines"]))
    files.put_bytes(f"Links\\{ap['base']}", local_base.read_bytes(), overwrite=True)

    journal({"action": "append", "node": node, "batches": len(claimed),
             "added": len(all_urls), "base_size": base_size})
    report.update(dry_run=False, batches_claimed=len(claimed), urls_added=len(all_urls),
                  non_url_skipped=skipped_total, base_size_lines=base_size,
                  uploaded=f"Links\\{ap['base']} на {node}")
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="XRumer autopilot: долив целей в базу ноды")
    p.add_argument("--node", default=None, help="имя ноды (по умолчанию xrumer.node из конфига)")
    p.add_argument("--apply", action="store_true", help="боевой долив (без него dry-run)")
    args = p.parse_args()
    cfg = load_cfg()
    node = args.node or (cfg.get("xrumer") or {}).get("node") or "gsa-03"
    print(json.dumps(run(node, args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
