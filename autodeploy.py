#!/usr/bin/env python3
"""Автодеплой: добор по KPI-недобору → заливка в базу (с бэкапом) → сборка+пуш сайта.

Цепочка (после недельного --report):
  1) план недобора = target − Σ(report.added по бакетам группы) по каждой KPI-группе;
  2) добор из merged.csv через select-v1 (мягкое правило vanity-.co), группа целиком —
     набираем need = недобор + случайное превышение (overshoot) из объединённого пула
     её бакетов, дедуп против всей базы и внутри выборки;
  3) --apply: бэкап out_country_buckets → дозапись URL в бакеты (дедуп) → dashboard.py →
     deploy_pages.py. Идемпотентность: флаг по дате отчёта (второй раз не добирает).

По умолчанию СУХОЙ режим: показывает план и что было бы залито/пушнуто, базу не трогает.

  python3 autodeploy.py                 # сухой прогон
  python3 autodeploy.py --apply         # реально: залить добор + собрать + задеплоить
  python3 autodeploy.py --seed 123      # воспроизводимый добор
  python3 autodeploy.py --no-dobor --apply   # только пересобрать+задеплоить (без добора)
"""
import argparse, importlib.util, os, random, subprocess, sys, tarfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import dashboard as DB  # noqa: E402

SPLIT_DIR = Path("/srv/share/Split")
MERGED_CSV = SPLIT_DIR / "merged.csv"
SELV1_PATH = SPLIT_DIR / "select-v1.py"
STATE_DIR = ROOT / "data" / "dobor_state"
OVERSHOOT = (2, 5)


def _resolve_dir(primary, fallback, _glob=None) -> Path:
    """Путь директории: primary (из конфига) если задан и существует, иначе fallback."""
    for cand in (primary, fallback):
        if cand and Path(cand).is_dir():
            return Path(cand)
    return Path(fallback)


def file_to_selv1(fname: str) -> str:
    """Poland.txt -> poland ; china-mix.txt -> china_mix ; Mexic.txt -> mexic."""
    return fname[:-4].lower().replace("-", "_") if fname.endswith(".txt") else fname.lower()


def load_selv1():
    spec = importlib.util.spec_from_file_location("selv1", str(SELV1_PATH))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build_plan(cfg, latest):
    """[(label, [files], deficit)] по группам с недобором из ОРГАНИЧЕСКОГО отчёта."""
    per_added = {f: a for f, (t, a) in (latest or {}).get("per_file", {}).items()}
    plan = []
    for kt in cfg.get("kpi_targets", []):
        organic = sum(per_added.get(f, 0) for f in kt["buckets"])
        deficit = max(kt["target"] - organic, 0)
        if deficit > 0:
            plan.append((kt["label"], kt["buckets"], deficit))
    return plan


def base_url_set(buckets_dir: Path, selv1):
    urls = set()
    for p in buckets_dir.glob("*.txt"):
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            k = selv1.norm_url(line.strip())
            if k:
                urls.add(k)
    return urls


def pick_dobor(plan, buckets_dir, selv1, seed):
    rng = random.Random(seed)
    header, by_bucket, _ = selv1.read_rows_by_bucket(str(MERGED_CSV))
    i_url = selv1.find_col(header, selv1.URL_COLUMN)
    base = base_url_set(buckets_dir, selv1)
    seen = set()
    picks = {}       # file -> [raw_url, ...]
    report = []
    for label, files, deficit in plan:
        over = rng.randint(*OVERSHOOT)
        need = deficit + over
        pool = [(f, row) for f in files for row in by_bucket.get(file_to_selv1(f), [])]
        rng.shuffle(pool)
        got = 0
        for f, row in pool:
            if got >= need:
                break
            raw = row[i_url]
            k = selv1.norm_url(raw)
            if not k or k in seen or k in base:
                continue
            seen.add(k)
            picks.setdefault(f, []).append(raw)
            got += 1
        report.append({"label": label, "files": files, "deficit": deficit, "over": over,
                       "need": need, "available": len(pool), "picked": got,
                       "shortage": max(0, need - got)})
    return picks, report


def apply_picks(picks, buckets_dir):
    added = {}
    for fname, urls in picks.items():
        p = buckets_dir / fname
        with p.open("a", encoding="utf-8") as f:
            for u in urls:
                f.write(u + "\n")
        added[fname] = len(urls)
    return added


def backup_buckets(buckets_dir: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = SPLIT_DIR / f"out_country_buckets.bak_{ts}.tar.gz"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(buckets_dir, arcname=buckets_dir.name)
    return out


def run(cmd):
    print(f"  $ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        raise SystemExit(f"шаг упал: {' '.join(cmd)} (код {r.returncode})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="реально залить+собрать+задеплоить")
    ap.add_argument("--no-dobor", action="store_true", help="пропустить добор (только сборка+деплой)")
    ap.add_argument("--no-deploy", action="store_true", help="залить добор, но НЕ пересобирать/деплоить сайт")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--overshoot", default=None, help="min,max (по умолчанию 2,5)")
    args = ap.parse_args()
    if args.overshoot:
        global OVERSHOOT
        OVERSHOOT = tuple(int(x) for x in args.overshoot.split(","))

    cfg = DB.load_config()
    defaults = __import__("json").loads(DB.DEFAULTS_PATH.read_text(encoding="utf-8")) if DB.DEFAULTS_PATH.exists() else {}
    buckets_dir = _resolve_dir(cfg.get("buckets_dir"),
                               defaults.get("buckets_dir", "/srv/share/Split/out_country_buckets"), "*.txt")
    report_dir = _resolve_dir(cfg.get("report_out_dir"),
                              defaults.get("report_out_dir", "/srv/share/Split/reports"), "gsa_report_*.txt")
    reports = DB.parse_all_reports(report_dir)
    if not reports:
        # «Отчёта нет» и «неделя ничего не дала» — разные факты. Раньше оба вели
        # к organic=0 по каждой группе, дефицит становился полной целью KPI, и
        # с --apply план впрыскивался в живые бакеты целиком.
        print(f"ОТКАЗ: в {report_dir} нет ни одного gsa_report_*.txt. "
              f"Это не нулевая неделя, а отсутствие измерения — план не строится.",
              file=sys.stderr)
        sys.exit(2)
    latest = reports[-1]
    rdate = latest["date"]
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    flag = STATE_DIR / f"dobor_{rdate}.done"

    seed = args.seed if args.seed is not None else random.randrange(1, 10**9)
    print(f"=== autodeploy · отчёт {rdate} · seed={seed} · overshoot={OVERSHOOT} "
          f"· режим={'APPLY' if args.apply else 'DRY-RUN'} ===")
    print(f"букеты: {buckets_dir}")

    picks, report = {}, []
    if args.no_dobor:
        print("добор пропущен (--no-dobor).")
    elif flag.exists():
        print(f"добор для отчёта {rdate} уже применён ({flag.name}) — пропускаю (идемпотентность).")
    else:
        plan = build_plan(cfg, latest)
        if not plan:
            print("недобора по KPI нет — добор не нужен.")
        else:
            picks, report = pick_dobor(plan, buckets_dir, load_selv1(), seed)
            print(f"\n{'группа':30}{'недобор':>8}{'+ранд':>7}{'need':>6}{'в доноре':>9}{'взято':>7}{'нехв.':>7}")
            for r in report:
                print(f"{r['label'][:28]:30}{r['deficit']:>8}{r['over']:>7}{r['need']:>6}"
                      f"{r['available']:>9}{r['picked']:>7}{r['shortage']:>7}")
            tot_pick = sum(r["picked"] for r in report)
            tot_short = sum(r["shortage"] for r in report)
            print(f"\nИТОГО добор: {tot_pick} строк"
                  + (f"  ⚠ нехватка донора: {tot_short}" if tot_short else "  (все группы закрыты)"))
            print("залил бы в:", {f: len(u) for f, u in picks.items()})

    if not args.apply:
        print("\nСУХОЙ режим — базу не трогал, деплой не запускал. Для реального прогона: --apply")
        return

    # --- APPLY ---
    if picks:
        bak = backup_buckets(buckets_dir)
        print(f"\nбэкап: {bak.name} ({bak.stat().st_size//1024} КБ)")
        added = apply_picks(picks, buckets_dir)
        print("залито:", added, "= всего", sum(added.values()))
        flag.write_text(f"{datetime.now().isoformat()} seed={seed} picked={sum(added.values())}\n")
    if args.no_deploy:
        print("\nГОТОВО: добор применён (если был). Сайт НЕ трогал (--no-deploy).")
        return
    print("\nсборка сайта + деплой:")
    run([sys.executable, "dashboard.py"])
    run([sys.executable, "deploy_pages.py"])
    print("\nГОТОВО: добор применён (если был), сайт пересобран и задеплоен.")


if __name__ == "__main__":
    main()
