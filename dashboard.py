#!/usr/bin/env python3
"""Генератор статического дашборда статистики GSA для Cloudflare Pages.

Читает те же данные, что и `--report`:
  - бакеты verified (buckets_dir/*.txt)  -> тоталы по странам/регионам
  - отчёты gsa_report_*_YYYY-MM-DD.txt   -> прибавка за неделю + тренд + статус серверов
  - kpi_targets из конфига               -> выполнение KPI (по ПРИБАВКЕ за неделю)

Отдаёт self-contained site/index.html (без внешних зависимостей) — готово к
публикации на Cloudflare Pages. Палитра — валидированный референс dataviz-скилла.

  python3 dashboard.py            # -> ./site/index.html
  python3 dashboard.py --out DIR  # другой каталог публикации
"""
import argparse, html, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from lib import buckets as B  # noqa: E402

CONFIG_PATH = ROOT / "data" / "gsa_checker.config.json"
DEFAULTS_PATH = ROOT / "dashboard.defaults.json"  # несекретный fallback (без токенов)
LINE_RE = re.compile(r"^(.*?)\s+(\d+)\s+\(\+(\d*)\)\s*$")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
NOT_STATED_LABEL = "Не указано"

# label -> bucket file (из SUMMARY_ORDER) + Not Stated
LABEL_TO_FILE = {label: fname for fname, label in B.SUMMARY_ORDER}
LABEL_TO_FILE[NOT_STATED_LABEL] = B.NOT_STATED_FILE


def load_config() -> dict:
    """Реальный конфиг (с секретами) имеет приоритет; если его нет — несекретный
    fallback. Дашборду токены не нужны, поэтому он не должен падать без конфига."""
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if DEFAULTS_PATH.exists():
        sys.stderr.write(f"[dashboard] {CONFIG_PATH.name} нет — использую "
                         f"{DEFAULTS_PATH.name} (несекретный fallback)\n")
        return json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"нет ни {CONFIG_PATH} ни {DEFAULTS_PATH}")


def bucket_totals(buckets_dir: Path) -> dict:
    """file -> число непустых строк (накопленный verified)."""
    out = {}
    for fname, _label in B.SUMMARY_ORDER:
        out[fname] = B.count_nonempty_lines(buckets_dir / fname)
    out[B.NOT_STATED_FILE] = B.count_nonempty_lines(buckets_dir / B.NOT_STATED_FILE)
    return out


def parse_report(path: Path) -> dict:
    """Разбирает один gsa_report_*.txt -> {date, added_total, itogo, servers,
    per_file:{file:(total,added)}}."""
    m = DATE_RE.search(path.name)
    date = m.group(1) if m else "?"
    res = {"date": date, "path": path, "added_total": 0, "itogo": 0,
           "servers": "", "per_file": {}}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Добавлено новых URL"):
            n = re.search(r"(\d+)", line)
            res["added_total"] = int(n.group(1)) if n else 0
            continue
        if line.startswith("ИТОГО"):
            n = re.search(r"(\d+)", line)
            res["itogo"] = int(n.group(1)) if n else 0
            continue
        if line.startswith("Серверы:"):
            res["servers"] = line.split(":", 1)[1].strip()
            continue
        if ":" in line and "(" not in line:
            continue  # прочие счётчики-заголовки
        mm = LINE_RE.match(line)
        if not mm:
            continue
        label, total, added = mm.group(1), int(mm.group(2)), int(mm.group(3) or 0)
        fname = LABEL_TO_FILE.get(label)
        if fname:
            res["per_file"][fname] = (total, added)
    return res


def parse_all_reports(report_dir: Path) -> list:
    reps = [parse_report(p) for p in sorted(report_dir.glob("gsa_report_*.txt"))]
    reps.sort(key=lambda r: r["date"])
    return reps


def compute_kpi(kpi_targets: list, latest: dict) -> list:
    added_by_file = {f: a for f, (t, a) in (latest or {}).get("per_file", {}).items()}
    rows = []
    for kt in kpi_targets:
        added = sum(added_by_file.get(f, 0) for f in kt["buckets"])
        target = kt["target"]
        pct = (added / target) if target else 0.0
        if added >= target:
            status = "good"
        elif pct >= 0.5:
            status = "warning"
        else:
            status = "critical"
        rows.append({"label": kt["label"], "target": target, "added": added,
                     "buckets": kt["buckets"], "pct": pct,
                     "deficit": max(target - added, 0), "status": status})
    return rows


# ---------- рендер ----------

CSS = """
:root{
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e;
  --muted:#898781; --grid:#e1e0d9; --baseline:#c3c2b7; --border:rgba(11,11,11,.10);
  --blue:#2a78d6; --track:#cde2fb;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --good-ink:#006300;
  color-scheme:light;
}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,.10);
  --blue:#3987e5; --track:#184f95; --good-ink:#0ca30c; color-scheme:dark;
}}
:root[data-theme=dark]{
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7;
  --muted:#898781; --grid:#2c2c2a; --baseline:#383835; --border:rgba(255,255,255,.10);
  --blue:#3987e5; --track:#184f95; --good-ink:#0ca30c; color-scheme:dark;
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.45}
.wrap{max-width:1100px;margin:0 auto;padding:24px 20px 64px}
header{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:8px}
h1{font-size:20px;margin:0;font-weight:600}
.sub{color:var(--muted);font-size:13px}
.theme-btn{margin-left:auto;background:var(--surface);border:1px solid var(--border);
  color:var(--ink2);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:13px}
section{background:var(--surface);border:1px solid var(--border);border-radius:12px;
  padding:18px 20px;margin-top:16px}
h2{font-size:14px;margin:0 0 14px;font-weight:600;letter-spacing:.01em}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}
.tile .lbl{color:var(--ink2);font-size:13px;margin-bottom:6px}
.tile .val{font-size:34px;font-weight:600;letter-spacing:-.01em}
.tile.hero .val{font-size:52px}
.tile .val small{font-size:15px;color:var(--muted);font-weight:400}
.tile .delta{font-size:13px;color:var(--good-ink);margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--grid);vertical-align:middle}
th{color:var(--muted);font-weight:500;font-size:12px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.bar-cell{width:46%}
.track{position:relative;height:12px;background:var(--track);border-radius:4px;overflow:hidden}
.track.vol{background:var(--grid)}
td.rank{color:var(--muted);width:34px}
.fill{position:absolute;left:0;top:0;bottom:0;border-radius:4px 0 0 4px;background:var(--blue)}
.fill.good{background:var(--good)} .fill.warning{background:var(--warning)}
.fill.critical{background:var(--critical)}
.pill{font-size:11px;padding:1px 7px;border-radius:20px;border:1px solid var(--border);color:var(--ink2)}
.deficit{color:var(--critical);font-variant-numeric:tabular-nums}
.ok{color:var(--good-ink)}
.crow{display:flex;align-items:center;gap:10px;padding:3px 0}
.crow .cn{width:190px;flex:0 0 190px;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.crow .ct{flex:1;height:14px;background:transparent;position:relative}
.crow .cf{position:absolute;left:0;top:0;bottom:0;background:var(--blue);border-radius:0 4px 4px 0;min-width:2px}
.crow .cv{width:96px;flex:0 0 96px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums;color:var(--ink2)}
.crow .cv b{color:var(--good-ink);font-weight:500}
.foot{color:var(--muted);font-size:12px;margin-top:22px;text-align:center}
.servers{font-size:13px;color:var(--ink2)}
.trend{display:flex;align-items:flex-end;gap:14px;height:120px;padding-top:8px}
.tcol{display:flex;flex-direction:column;align-items:center;gap:6px;flex:0 0 auto}
.tbar{width:34px;background:var(--blue);border-radius:4px 4px 0 0;min-height:2px}
.tcap{font-size:11px;color:var(--ink2);font-variant-numeric:tabular-nums}
.tdate{font-size:11px;color:var(--muted)}
"""

THEME_JS = """
(function(){var k='dash-theme';var s=localStorage.getItem(k);
if(s)document.documentElement.setAttribute('data-theme',s);
document.getElementById('tt').addEventListener('click',function(){
var cur=document.documentElement.getAttribute('data-theme');
var mq=window.matchMedia('(prefers-color-scheme:dark)').matches;
var next=(cur? (cur==='dark'?'light':'dark') : (mq?'light':'dark'));
document.documentElement.setAttribute('data-theme',next);localStorage.setItem(k,next);});})();
"""


def fmt(n): return f"{n:,}".replace(",", " ")


def esc(s): return html.escape(str(s))


def generic_servers(s: str) -> str:
    """Скрывает имена серверов (gsa-01 → «Источник 1»), сохраняя статус/свежесть."""
    n = [0]

    def repl(_m):
        n[0] += 1
        return f"Источник {n[0]}"

    return re.sub(r"gsa-\w+", repl, s)


def render_html(cfg, totals, reports):
    latest = reports[-1] if reports else None
    kpi = compute_kpi(cfg.get("kpi_targets", []), latest)
    kpi_added = sum(r["added"] for r in kpi)
    kpi_target = sum(r["target"] for r in kpi)
    kpi_pct = (kpi_added / kpi_target) if kpi_target else 0
    itogo = latest["itogo"] if latest else sum(totals.values())
    added_week = latest["added_total"] if latest else 0
    servers = generic_servers(latest["servers"]) if latest else "—"
    week_date = latest["date"] if latest else "—"
    added_by_file = {f: a for f, (t, a) in (latest or {}).get("per_file", {}).items()}

    # KPI meters
    kpi_rows = []
    for r in sorted(kpi, key=lambda x: x["pct"]):
        w = min(r["pct"], 1.0) * 100
        defc = (f'<span class="ok">выполнено</span>' if r["deficit"] == 0
                else f'<span class="deficit">−{r["deficit"]}</span>')
        kpi_rows.append(
            f'<tr><td>{esc(r["label"])}</td>'
            f'<td class="num">{r["added"]} / {r["target"]}</td>'
            f'<td class="bar-cell"><div class="track" title="{r["added"]}/{r["target"]} '
            f'({r["pct"]*100:.0f}%)"><div class="fill {r["status"]}" style="width:{w:.1f}%"></div></div></td>'
            f'<td class="num">{defc}</td></tr>')

    # Топ по объёму базы (таблица, сортировка по объёму, топ-40)
    order = [(f, l) for f, l in B.SUMMARY_ORDER] + [(B.NOT_STATED_FILE, "🏳 Не указано")]
    rows = sorted(order, key=lambda fl: totals.get(fl[0], 0), reverse=True)[:40]
    mx = max((totals.get(f, 0) for f, _ in rows), default=1) or 1
    vol_rows = []
    for i, (f, l) in enumerate(rows, 1):
        v = totals.get(f, 0)
        a = added_by_file.get(f, 0)
        addtxt = f'<b>+{a}</b>' if a else '<span style="color:var(--muted)">—</span>'
        vol_rows.append(
            f'<tr><td class="rank num">{i}</td><td>{esc(l)}</td>'
            f'<td class="bar-cell"><div class="track vol" title="{fmt(v)}">'
            f'<div class="fill" style="width:{v/mx*100:.1f}%"></div></div></td>'
            f'<td class="num">{fmt(v)}</td><td class="num">{addtxt}</td></tr>')

    # Weekly trend (added per report)
    tmax = max((r["added_total"] for r in reports), default=1) or 1
    tcols = []
    for r in reports[-12:]:
        h = max(r["added_total"] / tmax * 100, 2)
        tcols.append(
            f'<div class="tcol"><div class="tcap">+{r["added_total"]}</div>'
            f'<div class="tbar" style="height:{h:.0f}%" title="{r["date"]}: +{r["added_total"]}"></div>'
            f'<div class="tdate">{esc(r["date"][5:])}</div></div>')
    trend_html = ('<div class="trend">' + "".join(tcols) + '</div>') if tcols else \
        '<p class="sub">Отчётов пока нет — появятся после первого <code>--report</code>.</p>'

    gen = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Статистика по регионам</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>Еженедельная статистика по регионам</h1>
    <div class="sub">Данные за неделю: {esc(week_date)} · источники: {esc(servers)}</div>
  </div>
  <button class="theme-btn" id="tt">◐ тема</button>
</header>

<div class="tiles">
  <div class="tile hero"><div class="lbl">Всего в базе</div><div class="val">{fmt(itogo)}</div></div>
  <div class="tile"><div class="lbl">Добавлено за неделю</div><div class="val">{fmt(added_week)}</div>
    <div class="delta">за неделю {esc(week_date)}</div></div>
  <div class="tile"><div class="lbl">Выполнение плана (прирост)</div>
    <div class="val">{kpi_added} <small>/ {kpi_target}</small></div>
    <div class="delta">{kpi_pct*100:.0f}% недельного плана</div></div>
  <div class="tile"><div class="lbl">Источники</div><div class="val" style="font-size:20px;padding-top:10px">
    <span class="servers">{esc(servers)}</span></div></div>
</div>

<section>
  <h2>Лучшие по приросту за неделю — план ({kpi_target})</h2>
  <table>
    <thead><tr><th>Регион</th><th class="num">прирост / план</th>
      <th class="bar-cell">выполнение</th><th class="num">недобор</th></tr></thead>
    <tbody>{''.join(kpi_rows)}</tbody>
  </table>
</section>

<section>
  <h2>Прирост по неделям</h2>
  {trend_html}
</section>

<section>
  <h2>Лучшие по объёму базы — топ-40</h2>
  <table>
    <thead><tr><th class="num">#</th><th>Регион</th><th class="bar-cell">объём</th>
      <th class="num">всего в базе</th><th class="num">+ неделя</th></tr></thead>
    <tbody>{''.join(vol_rows)}</tbody>
  </table>
</section>

<div class="foot">Обновлено {esc(gen)}</div>
</div>
<script>{THEME_JS}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site"),
                    help="каталог публикации (по умолчанию ./site)")
    args = ap.parse_args()
    cfg = load_config()
    buckets_dir = Path(cfg["buckets_dir"])
    report_dir = Path(cfg["report_out_dir"])
    totals = bucket_totals(buckets_dir)
    reports = parse_all_reports(report_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(render_html(cfg, totals, reports), encoding="utf-8")
    print(f"OK: {out_dir/'index.html'}  "
          f"(бакетов: {sum(1 for v in totals.values() if v)}, отчётов: {len(reports)})")


if __name__ == "__main__":
    main()
