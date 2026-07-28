#!/usr/bin/env python3
"""Генератор дашборда статистики (обезличенного) для Cloudflare Pages.

Читает те же данные, что и `--report`:
  - бакеты (buckets_dir/*.txt)          -> накопленный объём по регионам
  - отчёты gsa_report_*_YYYY-MM-DD.txt  -> прирост за неделю + статус источников
  - kpi_targets из конфига              -> выполнение плана (по приросту за неделю)

Отдаёт site/index.html — интерактивный дашборд (сортировка, сворачивание, выделение
строк, фильтр, комментарии). Комментарии/пароль на бою обслуживает _worker.js + KV;
без бэкенда комментарии живут в localStorage (превью). Числа считаются вживую.

  python3 dashboard.py            # -> ./site/index.html
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from lib import buckets as B  # noqa: E402
from lib import iso2 as _iso2  # noqa: E402

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


def to_linux(p) -> str:
    r"""UNC-путь Windows (\\host\шара\...) -> /srv/share/... — генератор всегда на Linux."""
    s = str(p)
    m = re.match(r"^\\\\[^\\]+\\(?:шара|share)\\(.*)$", s)
    return "/srv/share/" + m.group(1).replace("\\", "/") if m else s


def resolve_dir(cfg_val, default_val, need_glob) -> Path:
    """Путь из конфига (после UNC→Linux), а если по нему нет нужных файлов —
    фолбэк на несекретный default. Устойчиво к Windows-конфигу на Linux."""
    for cand in (to_linux(cfg_val) if cfg_val else None, default_val):
        if not cand:
            continue
        p = Path(cand)
        if p.is_dir() and any(p.glob(need_glob)):
            return p
    return Path(default_val)


def bucket_totals(buckets_dir: Path) -> dict:
    """file -> число непустых строк по ВСЕМ файлам (вкл. страны-члены сплита)."""
    return {fname: B.count_nonempty_lines(buckets_dir / fname)
            for fname in B.all_bucket_files()}


_NAME_TO_CODE = {name: code for code, name in _iso2.ISO2_TO_NAME.items()
                 if len(code) == 2 and code.isalpha()}


def _flag(country_name: str) -> str:
    code = _NAME_TO_CODE.get(country_name, "")
    if len(code) == 2:
        return "".join(chr(0x1F1E6 + ord(c) - 97) for c in code)
    return "🏳"


def parse_report(path: Path) -> dict:
    """Разбирает один отчёт -> {date, added_total, itogo, servers, per_file:{file:(total,added)}}."""
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


def weekly_deltas(totals: dict, latest: dict) -> dict:
    """Прирост за неделю = текущий объём − объём на начало недели.
    Начало недели = (total − added) из отчёта (отчёт недельный, его total уже
    несёт базу до дозаливки). Совпадает с ручным расчётом оператора (Σ = +2000)."""
    per = (latest or {}).get("per_file", {})
    out = {}
    for f, cur in totals.items():
        if f in per:
            t, a = per[f]
            out[f] = cur - (t - a)
        else:
            out[f] = 0
    return out


def compute_kpi(kpi_targets: list, deltas: dict) -> list:
    rows = []
    for kt in kpi_targets:
        added = sum(deltas.get(f, 0) for f in kt["buckets"])
        target = kt["target"]
        rows.append({"label": kt["label"], "target": target, "added": added,
                     "buckets": kt["buckets"], "deficit": max(target - added, 0)})
    return rows


def generic_servers(s: str) -> str:
    """Скрывает имена серверов (gsa-01 → «Источник 1»), сохраняя статус/свежесть."""
    n = [0]

    def repl(_m):
        n[0] += 1
        return f"Источник {n[0]}"

    return re.sub(r"gsa-\w+", repl, s)


def split_label(label: str):
    """'🇺🇸 США' -> ('🇺🇸', 'США')."""
    parts = label.split(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else ("", label)


def esc(s):
    return html.escape(str(s))


# ---------- дизайн (палитра оператора) ----------

CSS = r"""
:root{
  --bg:#eef1f3; --surface:#ffffff; --surface-2:#f6f8f9;
  --ink:#141a21; --muted:#5c6a76; --faint:#8a97a2;
  --border:#e0e5ea; --hair:#eaeef1;
  --accent:#0e9f6e; --accent-soft:#d8f0e5;
  --good:#12946a; --good-bg:#e2f2ea;
  --warn:#b9791a; --warn-bg:#f6ecd9;
  --crit:#cc4258; --crit-bg:#f7dfe3;
  --bar:#0e9f6e; --bar-track:#e6ebee;
  --shadow:0 1px 2px rgba(20,26,33,.05),0 8px 24px rgba(20,26,33,.05);
}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])){
  --bg:#0c1014; --surface:#141a20; --surface-2:#10151a;
  --ink:#e9eef3; --muted:#93a1ad; --faint:#66727d;
  --border:#232c35; --hair:#1c242c;
  --accent:#2ed69b; --accent-soft:#123528;
  --good:#38c99a; --good-bg:#12281f; --warn:#d69a3f; --warn-bg:#2b2312;
  --crit:#e56a7e; --crit-bg:#2c1418; --bar:#2ed69b; --bar-track:#20282f;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
}}
:root[data-theme=dark]{
  --bg:#0c1014; --surface:#141a20; --surface-2:#10151a;
  --ink:#e9eef3; --muted:#93a1ad; --faint:#66727d;
  --border:#232c35; --hair:#1c242c;
  --accent:#2ed69b; --accent-soft:#123528;
  --good:#38c99a; --good-bg:#12281f; --warn:#d69a3f; --warn-bg:#2b2312;
  --crit:#e56a7e; --crit-bg:#2c1418; --bar:#2ed69b; --bar-track:#20282f;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 10px 30px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
body{margin:0; background:var(--bg); color:var(--ink);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  line-height:1.5; -webkit-font-smoothing:antialiased;}
.num{font-variant-numeric:tabular-nums; font-feature-settings:"tnum" 1;}
.wrap{max-width:960px; margin:0 auto; padding:28px 20px 56px;}
header{position:relative;}
.eyebrow{font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:var(--accent); font-weight:700; margin:0 0 8px;}
h1{font-size:clamp(24px,4.4vw,34px); line-height:1.1; margin:0 0 6px; text-wrap:balance; letter-spacing:-.01em;}
.sub{color:var(--muted); font-size:14px; margin:0;}
.servers{display:flex; flex-wrap:wrap; gap:6px 14px; margin-top:12px; font-size:12.5px; color:var(--muted);}
.servers b{color:var(--ink); font-weight:600;}
.dot{color:var(--good); font-weight:700;}
.topnav{position:absolute; top:0; right:0; display:flex; gap:8px; align-items:center;}
.theme-btn,.navlink{background:var(--surface); border:1px solid var(--border); color:var(--muted);
  border-radius:9px; padding:6px 11px; cursor:pointer; font-size:12.5px; text-decoration:none;}
.theme-btn:hover,.navlink:hover{color:var(--ink);}
.navlink.pri{background:var(--accent); color:#fff; border-color:var(--accent); font-weight:650;}
.wsel{background:var(--surface); border:1px solid var(--border); color:var(--ink); border-radius:8px;
  padding:2px 7px; font:inherit; font-size:13px; font-weight:600; cursor:pointer;}
.wbtn{background:var(--surface); border:1px solid var(--border); color:var(--accent); border-radius:8px;
  padding:2px 8px; font:inherit; font-size:12px; font-weight:650; cursor:pointer;}
.wbtn.danger{color:var(--crit);}
.modal{position:fixed; inset:0; background:rgba(10,14,20,.55); display:grid; place-items:center; z-index:100; padding:16px;}
.modal[hidden]{display:none;}
.modal-card{background:var(--surface); border:1px solid var(--border); border-radius:14px; box-shadow:var(--shadow);
  padding:20px 22px; width:min(560px,96vw); max-height:90vh; overflow:auto;}
.modal-card h3{margin:0 0 12px; font-size:16px;}
.wk-lbl{display:block; font-size:12.5px; color:var(--muted); margin-bottom:10px;}
.wk-lbl input{margin-left:8px; background:var(--surface-2); border:1px solid var(--border); border-radius:8px;
  color:var(--ink); font:inherit; padding:5px 8px;}
#wkText{width:100%; min-height:220px; background:var(--surface-2); border:1px solid var(--border); border-radius:10px;
  color:var(--ink); font:inherit; font-size:12.5px; padding:10px 12px; resize:vertical; white-space:pre;}
.wk-err{color:var(--crit); font-size:12.5px; min-height:16px; margin-top:6px;}
.modal-actions{display:flex; gap:10px; margin-top:12px;}
.modal-actions button{border:0; border-radius:9px; padding:9px 18px; font-weight:650; font-size:13px; cursor:pointer;}
.modal-actions button.pri{background:var(--accent); color:#fff;}
.modal-actions button.ghost{background:transparent; color:var(--muted); border:1px solid var(--border);}
.cmp-bar{display:flex; flex-wrap:wrap; align-items:center; gap:6px 16px; margin:16px 0 -4px; font-size:12.5px; color:var(--muted);}
.cmp-bar label{display:inline-flex; align-items:center; gap:5px; cursor:pointer;}
.cmp-bar input{accent-color:var(--accent); cursor:pointer;}
.cmp-parts{display:inline-flex; align-items:center; gap:6px 12px; flex-wrap:wrap;}
.cmp-bar.off .cmp-parts{opacity:.4; pointer-events:none;}
.cmp{font-size:11.5px; font-weight:600; white-space:nowrap; font-variant-numeric:tabular-nums;}
.cmp.up{color:var(--good);} .cmp.down{color:var(--crit);} .cmp.flat{color:var(--faint);}
.cmp-line{margin-top:3px; font-size:11.5px;}
th.cmp-col,td.cmp-col{text-align:right; white-space:nowrap;}
.theme-btn:focus-visible,.seg button:focus-visible,th.s:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}

.hero{display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin:22px 0;}
@media(max-width:640px){.hero{grid-template-columns:1fr;}}
.tile{background:var(--surface); border:1px solid var(--border); border-radius:14px;
  padding:16px 16px 15px; box-shadow:var(--shadow); overflow:hidden;}
.tile .lbl{font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--faint); font-weight:600;}
.tile .big{font-size:34px; font-weight:750; line-height:1.05; margin-top:6px; letter-spacing:-.02em;}
.tile .cap{font-size:12.5px; color:var(--muted); margin-top:2px;}
.tile.accent{background:linear-gradient(160deg,var(--accent-soft),var(--surface)); border-color:var(--accent-soft);}
.tile.accent .big{color:var(--accent);}
.tile .big span{font-size:18px; color:var(--muted); font-weight:600;}

.panel{background:var(--surface); border:1px solid var(--border); border-radius:14px;
  box-shadow:var(--shadow); margin:18px 0; overflow:hidden;}
.panel-head{display:flex; align-items:center; justify-content:space-between; gap:12px; padding:15px 18px;}
.panel.collapsible .panel-head{cursor:pointer; user-select:none;}
.panel-head h2{font-size:15px; margin:0; letter-spacing:-.01em; display:flex; align-items:center; gap:8px;}
.panel-head .hint{font-size:12px; color:var(--faint);}
.chev{transition:transform .2s; color:var(--faint); font-size:12px;}
.panel.collapsed .chev{transform:rotate(-90deg);}
.panel-body{padding:0 18px 18px;}
.panel.collapsed .panel-body{display:none;}

.kpi-top{display:flex; align-items:flex-end; justify-content:space-between; gap:10px; flex-wrap:wrap;}
.kpi-big{font-size:30px; font-weight:750; letter-spacing:-.02em;}
.kpi-big span{font-size:15px; color:var(--muted); font-weight:600;}
.kpi-pct{font-size:13px; font-weight:700; color:var(--good); background:var(--good-bg); padding:3px 9px; border-radius:999px;}
.track{height:12px; background:var(--bar-track); border-radius:999px; margin:12px 0 6px; overflow:hidden; position:relative;}
.fill{height:100%; background:var(--bar); border-radius:999px; width:0; transition:width .9s cubic-bezier(.2,.7,.2,1);}
.plan-tick{position:absolute; top:-3px; bottom:-3px; width:2px; background:var(--ink); opacity:.55;}
.kpi-legend{display:flex; justify-content:space-between; font-size:11.5px; color:var(--muted);}
.kpi-extra{display:flex; gap:16px; flex-wrap:wrap; margin-top:12px; font-size:13px; color:var(--muted);}
.kpi-extra b{color:var(--ink);}

.grp-grid{display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:8px;}
.grp{background:var(--surface-2); border:1px solid var(--hair); border-radius:9px; padding:9px 11px; display:flex; flex-direction:column; gap:3px;}
.grp .g{display:flex; align-items:center; gap:6px; font-size:12px; font-weight:550; color:var(--muted);}
.grp .g .flag{font-size:13px; width:16px; text-align:center;}
.grp .r{display:flex; align-items:baseline; justify-content:space-between;}
.grp .fact{font-size:18px; font-weight:750;}
.grp.done .fact{color:var(--good);} .grp.miss .fact{color:var(--crit);}
.grp .st{font-size:11px; font-weight:700;} .grp.done .st{color:var(--good);} .grp.miss .st{color:var(--crit);}
.grp .tgt{font-size:11.5px; color:var(--faint);}

.seg{display:inline-flex; border:1px solid var(--border); border-radius:8px; overflow:hidden;}
.kpimode-wrap{display:inline-flex; align-items:center; gap:6px; font-size:12px; color:var(--muted);}
.exp{display:inline-block; width:14px; cursor:pointer; color:var(--muted); font-size:11px; text-align:center; user-select:none;}
.exp:hover{color:var(--ink);} .exp-none{display:inline-block; width:14px;}
tr.grpline .gname{font-weight:650;}
tr.memrow{background:var(--surface-2);}
tr.memrow td{padding-top:4px; padding-bottom:4px;}
.geo.mem{padding-left:22px;} .geo.mem .gname{color:var(--muted); font-size:12.5px;}
.seg button{background:var(--surface); border:0; padding:5px 11px; font-size:12px; color:var(--muted); cursor:pointer;}
.seg button.on{background:var(--accent-soft); color:var(--accent); font-weight:700;}

.tbl-scroll{overflow-x:auto;}
table{width:100%; border-collapse:collapse; font-size:13.5px; min-width:520px;}
thead th{text-align:right; font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--faint); font-weight:600; padding:10px 10px 9px; border-bottom:1px solid var(--border); white-space:nowrap;}
thead th.l{text-align:left;}
thead th.s{cursor:pointer;} thead th.s:hover{color:var(--ink);}
thead th .ind{opacity:.5; font-weight:700;} thead th.act{color:var(--ink);} thead th.act .ind{opacity:1; color:var(--accent);}
tbody td{padding:8px 10px; border-bottom:1px solid var(--hair); vertical-align:middle;}
tbody tr{cursor:pointer;}
tbody tr:last-child td{border-bottom:none;}
tbody tr:hover{background:var(--surface-2);}
tbody tr.sel{background:var(--accent-soft);}
tbody tr.sel td:first-child{box-shadow:inset 3px 0 0 var(--accent);}
.geo{display:flex; align-items:center; gap:9px; min-width:190px;}
.flag{font-size:17px; width:22px; text-align:center; flex:none;}
.gname{font-weight:550;}
.chip{font-size:9.5px; letter-spacing:.05em; text-transform:uppercase; font-weight:700; padding:2px 6px; border-radius:5px; margin-left:2px; vertical-align:middle;}
.chip.tgt{color:var(--accent); background:var(--accent-soft);}
.chip.non{color:var(--faint); background:var(--surface-2); border:1px solid var(--hair);}
td.n{text-align:right; white-space:nowrap;}
.delta{font-weight:700;} .delta.pos{color:var(--good);} .delta.zero{color:var(--faint);}
.dbar-wrap{min-width:96px;}
.dbar{display:inline-block; height:7px; border-radius:3px; background:var(--bar); vertical-align:middle; opacity:.85;}

/* Docs-стиль: значки на элементах, всплывашка выделения, поповер треда */
[data-anchor]{position:relative;}
.cmt-badge{position:absolute; top:4px; right:4px; z-index:5; background:var(--warn-bg); color:var(--warn);
  border:1px solid var(--warn); border-radius:20px; font-size:10.5px; font-weight:700; line-height:1;
  padding:2px 6px; cursor:pointer; user-select:none; display:inline-flex; gap:3px; align-items:center;}
.cmt-badge.inline{position:static; margin-left:6px; vertical-align:middle;}
tbody tr:hover .cmt-badge.inline{background:var(--warn); color:#fff;}
.cmt-sel{position:absolute; z-index:50; background:var(--ink); color:var(--surface); border-radius:8px;
  font-size:12px; font-weight:650; padding:6px 10px; cursor:pointer; box-shadow:var(--shadow); white-space:nowrap;}
.cmt-pop{position:absolute; z-index:60; width:300px; max-width:92vw; background:var(--surface);
  border:1px solid var(--border); border-radius:12px; box-shadow:var(--shadow); padding:12px;}
.cmt-pop .anchor{font-size:11px; color:var(--faint); margin-bottom:8px; font-weight:600;}
.cmt-pop .quote{font-size:11.5px; color:var(--muted); border-left:2px solid var(--accent); padding-left:8px;
  margin-bottom:8px; white-space:pre-wrap; max-height:52px; overflow:auto;}
.cmt-thread{max-height:210px; overflow:auto; margin-bottom:8px;}
.cmt-input{width:100%; background:var(--surface-2); border:1px solid var(--border); border-radius:9px;
  color:var(--ink); font:inherit; font-size:13px; padding:8px 10px; resize:vertical; min-height:44px;}
.cmt-name{width:100%; margin-bottom:6px; background:var(--surface-2); border:1px solid var(--border);
  border-radius:9px; color:var(--ink); font:inherit; font-size:12.5px; padding:6px 10px;}
.cmt-actions{display:flex; gap:8px; margin-top:8px; align-items:center;}
.cmt-actions button{background:var(--accent); color:#fff; border:0; border-radius:8px; padding:7px 14px;
  font-weight:650; font-size:12.5px; cursor:pointer;}
.cmt-actions button.ghost{background:transparent; color:var(--muted); border:1px solid var(--border);}
.cmt{border-top:1px solid var(--hair); padding:9px 0; font-size:13px;}
.cmt:first-child{border-top:0;}
.cmt .meta{font-size:11px; color:var(--faint); margin-bottom:2px; display:flex; justify-content:space-between; gap:8px;}
.cmt .meta b{color:var(--muted);}
.cmt .del{cursor:pointer; color:var(--crit); opacity:.7;}
.cmt .body{white-space:pre-wrap;}
.cmt-empty{color:var(--faint); font-size:13px; padding:6px 0;}
.cmt-mode{font-size:11px; color:var(--faint); margin-top:8px;}

.foot{margin-top:24px; display:grid; gap:10px;}
.note{background:var(--surface); border:1px solid var(--border); border-left:3px solid var(--accent);
  border-radius:10px; padding:11px 14px; font-size:12.5px; color:var(--muted);}
.note b{color:var(--ink);}
.meta-line{font-size:11.5px; color:var(--faint); margin-top:6px; text-align:center;}
"""

APP_JS = r"""
const D = window.DATA;
const $ = s => document.querySelector(s);
const fnum = n => n.toLocaleString("ru-RU").replace(/ /g," ").replace(/,/g," ");
const store = { get:(k,d)=>{try{return JSON.parse(localStorage.getItem(k))??d}catch(e){return d}},
                set:(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}} };

/* ---- тема ---- */
(function(){ const k="dash-theme", s=store.get(k,null);
  if(s) document.documentElement.setAttribute("data-theme",s);
  $("#tt").addEventListener("click",()=>{ const cur=document.documentElement.getAttribute("data-theme");
    const mq=matchMedia("(prefers-color-scheme:dark)").matches;
    const next = cur ? (cur==="dark"?"light":"dark") : (mq?"light":"dark");
    document.documentElement.setAttribute("data-theme",next); store.set(k,next); }); })();

/* ---- сворачивание секций ---- */
document.querySelectorAll(".panel.collapsible .panel-head").forEach(h=>{
  h.addEventListener("click",()=>h.parentElement.classList.toggle("collapsed"));
});

/* ---- недели: состояние + рендер (переключаемо, недели из KV) ---- */
let weeks = (D.weeks||[]).slice();
let wi = D.current|0, W = weeks[wi] || {totals:{},geo:[],groups:[],servers:"—",label:"—"};
let isMaster = false;
const CFG = D.cfg || {regions:[], kpi:[], notStated:"Not Stated.txt"};
const bakedDates = new Set((D.weeks||[]).map(w=>w.date));
const labelToFile = {}; CFG.regions.forEach(r=>{ labelToFile[r[0]+" "+r[1]] = r[2]; });
function splitLabel(l){ const i=l.indexOf(" "); return i<0?["",l]:[l.slice(0,i), l.slice(i+1)]; }
/* режимы KPI для недель без baked-modes (вручную добавленные): считаем из гео */
const NAME2FILE={}; (CFG.regions||[]).forEach(r=>{ NAME2FILE[r[1]]=r[2]; });
NAME2FILE["Южная Африка"]=NAME2FILE["Африка"]||"africa.txt";   // старый ярлык в ручных неделях
const GM = CFG.groupMembers || {};
function computeModes(w){
  if(!w || w.modes || !CFG.modesCfg) return;
  const fd={}; (w.geo||[]).forEach(g=>{ const fn=NAME2FILE[g[1]]; if(fn) fd[fn]=(fd[fn]||0)+(g[3]||0); });
  const gadd=bks=>bks.reduce((s,b)=>s+(fd[b]||0)+(GM[b]||[]).reduce((ss,m)=>ss+(fd[m]||0),0),0);
  const modes={}, fbm={};
  for(const mk in CFG.modesCfg){ const files=new Set();
    const groups=CFG.modesCfg[mk].map(([label,target,buckets])=>{ buckets.forEach(b=>files.add(b));
      const sl=splitLabel(label); return [sl[0],sl[1],gadd(buckets),target]; });
    const gl=groups.filter(g=>g[3]>0);
    modes[mk]={groups, added:gl.reduce((s,g)=>s+g[2],0), target:gl.reduce((s,g)=>s+g[3],0),
      closed:gl.filter(g=>g[2]>=g[3]).length, total:gl.length, targeted:files.size, files:[...files]};
    fbm[mk]=files; }
  w.modes=modes;
  w.geo=(w.geo||[]).map(g=>{ const fn=NAME2FILE[g[1]];
    return [g[0],g[1],g[2],g[3], fn?Object.fromEntries(Object.keys(modes).map(mk=>[mk,fbm[mk].has(fn)])):(g[4]||false)]; });
}
function weekLabel(dstr){ try{ const d=new Date(dstr+"T00:00:00"), s=new Date(d-7*864e5);
  const f=x=>String(x.getDate()).padStart(2,"0")+"."+String(x.getMonth()+1).padStart(2,"0");
  return f(s)+"–"+f(d); }catch(e){ return dstr; } }

/* ---- сравнение с прошлой неделей (вкл/выкл + что сравнивать; по умолчанию всё) ---- */
let cmp = Object.assign({on:true,hero:true,kpi:true,groups:true,geo:true}, store.get("cmp",{}));
let cmpPrev=null, prevGeoMap={}, prevGrpMap={};
function dlt(cur,prev){ const d=cur-prev; return {d,cls:d>0?"up":d<0?"down":"flat",ar:d>0?"▲":d<0?"▼":"→"}; }
function cmpHtml(cur,prev){ if(prev==null) return ""; const x=dlt(cur,prev);
  return `<span class="cmp ${x.cls}">пред. ${fnum(prev)} · ${x.ar}${x.d>0?"+":x.d<0?"−":""}${fnum(Math.abs(x.d))}</span>`; }

/* ---- режимы KPI (Старый / KPI GSA=«Новый») — только для текущей недели ---- */
const MLBL={old:"Старый",gsa:"Новый"};
let kmode=store.get("kpimode","gsa");
const expanded=new Set();                       // раскрытые группы (страны-члены)
function curMode(){ return (W&&W.modes)?W.modes[kmode]:null; }
function KT(){ const m=curMode(),T=W.totals; return m?Object.assign({},T,
  {kpi_added:m.added,kpi_target:m.target,groups_closed:m.closed,groups_total:m.total,targets:m.targeted}):T; }
function KG(){ const m=curMode(); return m?m.groups:W.groups; }
const TGTf=r=>{ const t=r[4]; return (t&&typeof t==="object")?!!t[kmode]:!!t; };
function renderModeSeg(){ const s=$("#kpimodeSeg"); if(!s)return; const has=!!(W&&W.modes);
  const wrap=s.closest(".kpimode-wrap")||s; wrap.hidden=!has;
  if(has) s.innerHTML=Object.keys(W.modes).map(k=>`<button data-m="${k}" class="${k===kmode?'on':''}">${MLBL[k]||k}</button>`).join(""); }
document.addEventListener("click",e=>{ const b=e.target.closest("#kpimodeSeg button"); if(!b)return;
  kmode=b.dataset.m; store.set("kpimode",kmode); renderModeSeg(); renderHero(); renderKpiPanel(); renderGroups(); renderGeo(); });

function renderHero(){ const T=KT(), P=cmpPrev, on=cmp.on&&cmp.hero&&P;
  const c=(cur,prev)=>on?`<div class="cmp-line">${cmpHtml(cur,prev)}</div>`:"";
  $("#hero").innerHTML = `
  <div class="tile accent" data-anchor="Прирост за неделю"><div class="lbl">Прирост за неделю</div>
    <div class="big num">+${fnum(T.added)}</div><div class="cap">новых записей по всем регионам</div>${c(T.added,P&&P.totals.added)}</div>
  <div class="tile" data-anchor="Выполнение плана"><div class="lbl">Выполнение плана</div>
    <div class="big num">${fnum(T.kpi_added)}<span> / ${fnum(T.kpi_target)}</span></div>
    <div class="cap">${T.kpi_added>=T.kpi_target?("план перевыполнен на "+Math.round((T.kpi_added/T.kpi_target-1)*100)+"%"):(Math.round(T.kpi_added/T.kpi_target*100)+"% плана")}</div>${c(T.kpi_added,P&&P.totals.kpi_added)}</div>
  <div class="tile" data-anchor="Групп в плане"><div class="lbl">Групп в плане</div>
    <div class="big num">${T.groups_closed}<span> / ${T.groups_total}</span> ${T.groups_closed===T.groups_total?'<span style="color:var(--good);font-size:26px">✓</span>':''}</div>
    <div class="cap">${T.groups_closed===T.groups_total?"все группы закрыты":(T.groups_total-T.groups_closed)+" не закрыто"}</div>${c(T.groups_closed,P&&P.totals.groups_closed)}</div>`;
}
function renderKpiPanel(){ const T=KT(), P=cmpPrev;
  const mx=Math.max(T.kpi_added,T.kpi_target,1), fillPct=Math.min(T.kpi_added/mx*100,100), planPct=Math.min(T.kpi_target/mx*100,100);
  $("#kpiPanel").innerHTML = `
  <div class="kpi-top"><div class="kpi-big num">${fnum(T.kpi_added)} <span>/ ${fnum(T.kpi_target)} план</span></div>
    <div class="kpi-pct">${Math.round(T.kpi_added/T.kpi_target*100)}% · ${T.kpi_added>=T.kpi_target?"+":""}${fnum(T.kpi_added-T.kpi_target)}</div></div>
  <div class="track"><div class="fill" id="kfill"></div><div class="plan-tick" style="left:${planPct.toFixed(0)}%"></div></div>
  <div class="kpi-legend"><span>0</span><span>план ${fnum(T.kpi_target)}</span><span class="num">факт ${fnum(T.kpi_added)}</span></div>
  <div class="kpi-extra"><span>Групп закрыто: <b class="num">${T.groups_closed} / ${T.groups_total}</b></span>
    <span>Целевых регионов: <b class="num">${T.targets}</b></span>
    <span>${T.kpi_added>=T.kpi_target?"Сверх плана":"Недобор"}: <b class="num">${T.kpi_added>=T.kpi_target?"+":""}${fnum(T.kpi_added-T.kpi_target)}</b></span>${(cmp.on&&cmp.kpi&&P)?`<span>Пред. неделя: ${cmpHtml(T.kpi_added,P.totals.kpi_added)}</span>`:""}</div>`;
  requestAnimationFrame(()=>{ const f=$("#kfill"); if(!f)return;
    if(matchMedia("(prefers-reduced-motion: reduce)").matches) f.style.transition="none";
    setTimeout(()=>{ f.style.width=fillPct.toFixed(1)+"%"; },60); });
}
function renderGroups(){
  $("#groups").innerHTML = KG().map(g=>{ const [flag,name,fact,tgt]=g, notgt=tgt<=0, done=fact>=tgt;
    return `<div class="grp ${notgt?'':(done?'done':'miss')}" data-anchor="Группа: ${name}"><div class="g"><span class="flag">${flag}</span>${name}</div>
      <div class="r"><span class="fact num">+${fact}</span><span class="st">${notgt?'—':(done?'✓':fact+'/'+tgt)}</span></div>
      <div class="tgt num">${notgt?'без цели':('план '+tgt+' · '+(done?('+'+(fact-tgt)+' сверх'):('−'+(tgt-fact)+' недобор')))}</div>${(cmp.on&&cmp.groups&&cmpPrev&&(name in prevGrpMap))?`<div class="cmp ${dlt(fact,prevGrpMap[name]).cls}" style="font-size:10.5px;margin-top:1px">пред. +${prevGrpMap[name]} ${dlt(fact,prevGrpMap[name]).ar}</div>`:""}</div>`;
  }).join("");
}

/* ---- таблица «прирост по гео»: сортировка + выделение + фильтр ---- */
let sortKey=store.get("gsort","delta"), sortDir=store.get("gdir",-1);
let filt=store.get("gfilt","all");
let sel=new Set(store.get("gsel",[]));
const cols={geo:1,total:2,delta:3};
function renderGeo(){
  const geoCmpOn = cmp.on&&cmp.geo&&!!cmpPrev;
  const gth=$("#geoCmpTh"); if(gth) gth.hidden=!geoCmpOn;
  const MEM=(W&&W.members)||{};
  let rows=W.geo.slice();
  if(filt==="tgt") rows=rows.filter(TGTf);
  const maxD=Math.max(1,...rows.map(r=>r[3]));
  rows.sort((a,b)=>{ let x,y;
    if(sortKey==="geo"){x=a[1].toLowerCase();y=b[1].toLowerCase(); return (x<y?-1:x>y?1:0)*sortDir;}
    x=a[cols[sortKey]]; y=b[cols[sortKey]]; return (x-y)*sortDir; });
  $("#geoRows").innerHTML = rows.map(r=>{ const [flag,name,total,delta]=r, tgt=TGTf(r);
    const bw=delta>0?Math.max(4,Math.round(delta/maxD*100)):0;
    const chip=tgt?'<span class="chip tgt">цель</span>':'<span class="chip non">—</span>';
    const mem=MEM[name], open=expanded.has(name);
    const caret=mem?`<span class="exp" data-exp="${name}">${open?'▾':'▸'}</span>`:'<span class="exp-none"></span>';
    let cmpTd="";
    if(geoCmpOn){ const p=prevGeoMap[name];
      if(p==null){ cmpTd='<td class="n cmp-col"><span class="cmp flat">—</span></td>'; }
      else{ const x=dlt(delta,p); cmpTd=`<td class="n cmp-col"><span class="cmp ${x.cls}">${x.ar}${x.d>0?"+":x.d<0?"−":""}${x.d?fnum(Math.abs(x.d)):"0"}</span></td>`; } }
    let out=`<tr data-g="${name}" data-anchor="Регион: ${name}" class="${sel.has(name)?'sel':''}${mem?' grpline':''}">
      <td><div class="geo">${caret}<span class="flag">${flag}</span><span class="gname">${name}</span>${chip}</div></td>
      <td class="n num">${fnum(total)}</td>
      <td class="n"><span class="delta ${delta>0?'pos':'zero'}">${delta>0?'+'+delta:'—'}</span></td>
      ${cmpTd}
      <td class="dbar-wrap"><span class="dbar" style="width:${bw}%"></span></td></tr>`;
    if(mem) out+=mem.map(m=>{ const [mf,mn,mt,md]=m;
      return `<tr class="memrow"${open?'':' hidden'}><td><div class="geo mem"><span class="flag">${mf}</span><span class="gname">${mn}</span></div></td>
        <td class="n num">${fnum(mt)}</td><td class="n"><span class="delta ${md>0?'pos':'zero'}">${md>0?'+'+md:'—'}</span></td>${geoCmpOn?'<td></td>':''}<td></td></tr>`; }).join("");
    return out; }).join("");
  document.querySelectorAll("#geoTable th.s").forEach(th=>{
    const k=th.dataset.k, on=k===sortKey; th.classList.toggle("act",on);
    th.querySelector(".ind").textContent = on ? (sortDir<0?"▼":"▲") : "";
  });
}
document.querySelectorAll("#geoTable th.s").forEach(th=>{
  const set=()=>{ const k=th.dataset.k;
    if(k===sortKey) sortDir=-sortDir; else {sortKey=k; sortDir=(k==="geo"?1:-1);}
    store.set("gsort",sortKey); store.set("gdir",sortDir); renderGeo(); };
  th.tabIndex=0; th.addEventListener("click",set);
  th.addEventListener("keydown",e=>{ if(e.key==="Enter"||e.key===" "){e.preventDefault();set();} });
});
$("#geoRows").addEventListener("click",e=>{
  const ex=e.target.closest(".exp");
  if(ex){ const g=ex.dataset.exp; if(expanded.has(g))expanded.delete(g); else expanded.add(g); renderGeo(); return; }
  const tr=e.target.closest("tr"); if(!tr||tr.classList.contains("memrow"))return;
  const g=tr.dataset.g; if(sel.has(g))sel.delete(g); else sel.add(g);
  store.set("gsel",[...sel]); tr.classList.toggle("sel"); });
$("#geoFilter").addEventListener("click",e=>e.stopPropagation());  // не сворачивать секцию
document.querySelectorAll("#geoFilter button").forEach(b=>b.addEventListener("click",e=>{
  e.stopPropagation();
  filt=b.dataset.f; store.set("gfilt",filt);
  document.querySelectorAll("#geoFilter button").forEach(x=>x.classList.toggle("on",x===b)); renderGeo(); }));
document.querySelector(`#geoFilter button[data-f="${filt}"]`)?.classList.add("on");

/* ---- панель сравнения с прошлой неделей ---- */
(function(){ const bar=$("#cmpBar"); if(!bar) return;
  function sync(){ $("#cmpOn").checked=cmp.on;
    bar.querySelectorAll("[data-cmp]").forEach(c=>{ c.checked=cmp[c.dataset.cmp]; });
    bar.classList.toggle("off", !cmp.on); }
  sync();
  $("#cmpOn").addEventListener("change",e=>{ cmp.on=e.target.checked; store.set("cmp",cmp); sync(); renderAll(); });
  bar.querySelectorAll("[data-cmp]").forEach(c=>c.addEventListener("change",()=>{
    cmp[c.dataset.cmp]=c.checked; store.set("cmp",cmp); renderAll(); }));
})();

/* ---- переключение недель ---- */
function renderAll(){ W = weeks[wi] || W;
  cmpPrev = (cmp.on && wi>0) ? weeks[wi-1] : null;
  prevGeoMap={}; prevGrpMap={};
  if(cmpPrev){ (cmpPrev.geo||[]).forEach(g=>{prevGeoMap[g[1]]=g[3];}); (cmpPrev.groups||[]).forEach(g=>{prevGrpMap[g[1]]=g[2];}); }
  $("#hSrc").textContent = ((W.servers||"").match(/Источник/g)||[]).length || "—";
  $("#hServers").textContent = W.servers || "—";
  $("#hBase").textContent = fnum((W.totals&&W.totals.base)||0);
  const dw=$("#delWeek"); if(dw) dw.hidden = !(isMaster && W && W.manual);
  renderModeSeg(); renderHero(); renderKpiPanel(); renderGroups(); renderGeo();
  try{ placeBadges(); }catch(e){}
}
function buildSelector(){ const ws=$("#weekSel");
  ws.innerHTML = weeks.map((w,i)=>({i,w})).reverse().map(o=>`<option value="${o.i}">${o.w.label}${o.w.manual?" ✎":""}</option>`).join("");
  ws.value=String(wi); }
$("#weekSel").addEventListener("change",e=>{ wi=+e.target.value; renderAll(); });
function resortWeeks(selDate){ weeks.sort((a,b)=>a.date<b.date?-1:a.date>b.date?1:0);
  wi = selDate ? weeks.findIndex(w=>w.date===selDate) : weeks.length-1; if(wi<0) wi=weeks.length-1;
  buildSelector(); renderAll(); }

/* парсинг вставленного отчёта -> объект недели */
function parseReport(text){ const per={}, RE=/^(.+?)\s+(\d+)\s+\(\+(\d*)\)\s*$/;
  text.split("\n").forEach(line=>{ line=line.trim(); const m=line.match(RE); if(!m) return;
    const label=m[1].trim(), total=+m[2], added=+(m[3]||0);
    const file = label.indexOf("Не указано")===0 ? CFG.notStated : labelToFile[label];
    if(file) per[file]={total,added};
  }); return per; }
function weekFromReport(date, per){
  const geo = CFG.regions.map(r=>{ const p=per[r[2]]||{total:0,added:0}; return [r[0],r[1],p.total,p.added,r[3]]; });
  const groups = CFG.kpi.map(k=>{ const sl=splitLabel(k[0]), fact=k[2].reduce((s,f)=>s+((per[f]||{}).added||0),0);
    return [sl[0],sl[1],fact,k[1]]; });
  const base=geo.reduce((s,g)=>s+g[2],0), added=geo.reduce((s,g)=>s+g[3],0);
  const kpi_added=groups.reduce((s,g)=>s+g[2],0), kpi_target=groups.reduce((s,g)=>s+g[3],0);
  const w={date, label:weekLabel(date), servers:"—", manual:true, geo, groups,
    totals:{base,added,kpi_added,kpi_target,groups_closed:groups.filter(g=>g[2]>=g[3]).length,
      groups_total:groups.length, targets:geo.filter(g=>g[4]).length}};
  computeModes(w); return w; }
async function saveManual(){ const manual=weeks.filter(w=>!bakedDates.has(w.date));
  try{ await fetch("/api/weeks",{method:"PUT",headers:{"content-type":"application/json"},body:JSON.stringify(manual)}); }catch(e){} }

/* модалка добавления недели */
const wkModal=$("#wkModal");
function openWk(){ $("#wkErr").textContent=""; $("#wkText").value=""; $("#wkDate").value=""; wkModal.hidden=false; }
function closeWk(){ wkModal.hidden=true; }
if($("#addWeek")) $("#addWeek").onclick=openWk;
$("#wkCancel").onclick=closeWk;
wkModal.addEventListener("mousedown",e=>{ if(e.target===wkModal) closeWk(); });
$("#wkSave").onclick=async()=>{ const date=$("#wkDate").value, text=$("#wkText").value;
  if(!date){ $("#wkErr").textContent="Укажите дату конца недели"; return; }
  const per=parseReport(text);
  if(Object.keys(per).length<5){ $("#wkErr").textContent="Не распознал отчёт — нужны строки вида «🇺🇸 США 8971 (+273)»"; return; }
  if(bakedDates.has(date)){ $("#wkErr").textContent="Такая неделя уже есть (с сервера)"; return; }
  const w=weekFromReport(date,per); weeks=weeks.filter(x=>x.date!==date); weeks.push(w);
  await saveManual(); closeWk(); resortWeeks(date); };
if($("#delWeek")) $("#delWeek").onclick=async()=>{ if(!W||!W.manual) return;
  if(!confirm("Удалить неделю "+W.label+"?")) return;
  const d=W.date; weeks=weeks.filter(x=>x.date!==d); await saveManual(); resortWeeks(); };

/* инициализация: роль + слияние недель из KV */
buildSelector(); renderAll();
(async()=>{
  try{ const me=await fetch("/api/me").then(r=>r.json()); isMaster=(me.role==="master"); }catch(e){}
  if(isMaster && $("#addWeek")) $("#addWeek").hidden=false;
  if(isMaster && $("#ctlLink")) $("#ctlLink").hidden=false;
  let manual=[]; try{ const r=await fetch("/api/weeks"); if(r.ok) manual=await r.json(); }catch(e){}
  if(Array.isArray(manual) && manual.length){ const byDate={};
    manual.forEach(w=>{ if(!bakedDates.has(w.date)){ computeModes(w); byDate[w.date]=w; } });
    (D.weeks||[]).forEach(w=>byDate[w.date]=w); weeks=Object.values(byDate); }
  resortWeeks();
})();

/* ---- комментарии в стиле Docs (выделение → значок → тред) ---- */
const CKEY="dash-comments"; let cmtMode="local"; let comments=[];
const selPop=$("#selPop"), pop=$("#pop"); let pending=null;
function ce(s){ return String(s).replace(/[&<>"]/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m])); }
function fmtTs(ts){ return new Date(ts).toLocaleString("ru-RU"); }

async function cGet(){ try{const r=await fetch("/api/comments",{headers:{accept:"application/json"}});
  if(r.ok){cmtMode="shared"; return await r.json();}}catch(e){} cmtMode="local"; return store.get(CKEY,[]); }
async function cAdd(c){ c.ts=Date.now();
  if(cmtMode==="shared"){ try{const r=await fetch("/api/comments",{method:"POST",
      headers:{"content-type":"application/json"},body:JSON.stringify(c)}); if(r.ok){return;} }catch(e){} }
  c.id=c.id||("l"+Date.now().toString(36)+Math.random().toString(36).slice(2,6));
  const a=store.get(CKEY,[]); a.push(c); store.set(CKEY,a); }
async function cDel(id){ if(cmtMode==="shared"){ try{const r=await fetch("/api/comments/"+encodeURIComponent(id),
      {method:"DELETE"}); if(r.ok){return;} }catch(e){} }
  store.set(CKEY, store.get(CKEY,[]).filter(c=>c.id!==id)); }
async function reload(){ comments=await cGet(); render(); }

function threadHTML(a){ const items=comments.filter(c=>c.anchor===a).sort((x,y)=>x.ts-y.ts);
  const q=items.find(c=>c.quote);
  return `<div class="anchor">${ce(a)}</div>`+
    (q&&q.quote?`<div class="quote">«${ce(q.quote)}»</div>`:``)+
    `<div class="cmt-thread">`+items.map(c=>`<div class="cmt"><div class="meta">`+
      `<span><b>${ce(c.author||'аноним')}</b> · ${fmtTs(c.ts)}</span>`+
      `<span class="del" data-del="${c.id||''}">удалить</span></div>`+
      `<div class="body">${ce(c.text)}</div></div>`).join("")+`</div>`+
    `<textarea class="cmt-input" id="pReply" placeholder="Ответить…"></textarea>`+
    `<div class="cmt-actions"><button id="pSend" data-a="${ce(a)}">Ответить</button>`+
    `<button class="ghost" id="pClose">Закрыть</button></div>`; }
function composeHTML(a,quote){ return `<div class="anchor">${ce(a)}</div>`+
  (quote?`<div class="quote">«${ce(quote)}»</div>`:``)+
  `<input class="cmt-name" id="pName" placeholder="Имя (необязательно)" maxlength="40">`+
  `<textarea class="cmt-input" id="pText" placeholder="Комментарий…" maxlength="2000"></textarea>`+
  `<div class="cmt-actions"><button id="pAdd">Добавить</button>`+
  `<button class="ghost" id="pClose">Отмена</button></div>`; }

function showPop(html,x,y){ pop.innerHTML=html; pop.hidden=false;
  const w=pop.offsetWidth, vw=document.documentElement.clientWidth;
  let left=Math.min(x, window.scrollX+vw-w-10); left=Math.max(left, window.scrollX+8);
  pop.style.left=left+"px"; pop.style.top=(y+8)+"px";
  const cl=pop.querySelector("#pClose"); if(cl) cl.onclick=hidePop;
  const add=pop.querySelector("#pAdd"); if(add) add.onclick=async()=>{
    const t=pop.querySelector("#pText").value.trim(); if(!t)return;
    await cAdd({anchor:pending.anchor, quote:pending.quote, text:t, author:pop.querySelector("#pName").value.trim()});
    hidePop(); await reload(); };
  const snd=pop.querySelector("#pSend"); if(snd) snd.onclick=async()=>{
    const t=pop.querySelector("#pReply").value.trim(); if(!t)return;
    await cAdd({anchor:snd.dataset.a, text:t, author:""}); hidePop(); await reload(); };
  pop.querySelectorAll("[data-del]").forEach(b=>b.onclick=async e=>{e.stopPropagation();
    await cDel(b.dataset.del); hidePop(); await reload();}); }
function hidePop(){ pop.hidden=true; pop.innerHTML=""; }
function rectXY(r){ return [r.left+window.scrollX, r.bottom+window.scrollY]; }
function openThread(a,el){ const [x,y]=rectXY(el.getBoundingClientRect()); showPop(threadHTML(a),x,y); }
function openCompose(){ const [x,y]=rectXY(selPop.getBoundingClientRect());
  showPop(composeHTML(pending.anchor,pending.quote),x,y);
  const tx=pop.querySelector("#pText"); if(tx) tx.focus(); }

function placeBadges(){ document.querySelectorAll(".cmt-badge").forEach(b=>b.remove());
  const seen={}; comments.forEach(c=>{ seen[c.anchor]=(seen[c.anchor]||0)+1; });
  Object.keys(seen).forEach(a=>{ const el=document.querySelector('[data-anchor="'+a.replace(/"/g,'')+'"]'); if(!el)return;
    const inline=el.tagName==="TR"; const b=document.createElement("span");
    b.className="cmt-badge"+(inline?" inline":""); b.textContent="💬 "+seen[a];
    b.addEventListener("mouseenter",()=>openThread(a,b));
    b.addEventListener("click",e=>{e.stopPropagation(); openThread(a,b);});
    if(inline){ (el.querySelector(".geo")||el.firstElementChild).appendChild(b); } else { el.appendChild(b); } }); }

function render(){ const box=$("#cmtList");
  if(!comments.length) box.innerHTML='<div class="cmt-empty">Заметок пока нет. Выделите любой текст на странице и нажмите «Комментировать».</div>';
  else { const bya={}; comments.forEach(c=>{(bya[c.anchor]=bya[c.anchor]||[]).push(c);});
    box.innerHTML=Object.keys(bya).map(a=>`<div class="cmt"><div class="meta"><span><b>${ce(a)}</b> · ${bya[a].length}</span></div>`+
      `<div class="body">${ce((bya[a].slice(-1)[0]||{}).text||'')}</div></div>`).join(""); }
  $("#cmtMode").textContent = cmtMode==="shared"
    ? "Общие заметки (сохраняются на сервере)."
    : "Превью: заметки пока в этом браузере — на боевом сайте будут общими.";
  placeBadges(); }

/* выделение текста → всплывашка «Комментировать» */
function hideSel(){ selPop.hidden=true; pending=null; }
document.addEventListener("mouseup",e=>{
  if(e.target.closest(".cmt-pop")||e.target.closest(".cmt-sel")) return;
  setTimeout(()=>{ const s=window.getSelection(); const t=s&&s.toString().trim();
    if(!t||!s.rangeCount){ hideSel(); return; }
    let node=s.anchorNode; node=node&&(node.nodeType===3?node.parentElement:node);
    const host=node&&node.closest&&node.closest("[data-anchor]");
    if(!host){ hideSel(); return; }
    pending={anchor:host.getAttribute("data-anchor"), quote:t.slice(0,300)};
    const r=s.getRangeAt(0).getBoundingClientRect();
    selPop.hidden=false; selPop.style.left=(r.left+window.scrollX)+"px"; selPop.style.top=(r.top+window.scrollY-36)+"px";
  },10); });
selPop.addEventListener("mousedown",e=>e.preventDefault());
selPop.addEventListener("click",()=>{ if(pending) openCompose(); selPop.hidden=true; });
document.addEventListener("mousedown",e=>{ if(!e.target.closest(".cmt-pop")&&!e.target.closest(".cmt-badge")&&!e.target.closest(".cmt-sel")) hidePop(); });
document.addEventListener("keydown",e=>{ if(e.key==="Escape"){hidePop(); hideSel();} });
reload();
"""


def _week_label(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{(d - timedelta(days=7)).strftime('%d.%m')}–{d.strftime('%d.%m')}"
    except Exception:
        return date_str


def _grp_add(bks, deltas):
    """Прирост по бакетам с учётом стран-членов сплита."""
    return sum(deltas.get(b, 0) + sum(deltas.get(m, 0) for m in B.GROUP_MEMBERS.get(b, ()))
               for b in bks)


def _mode_data(kt_list, deltas):
    """Данные одного режима KPI (добрано только по целевым, агрегируя членов)."""
    rows = [[*split_label(kt["label"]), _grp_add(kt["buckets"], deltas), kt["target"]] for kt in kt_list]
    goaled = [r for r in rows if r[3] > 0]
    files = sorted({f for kt in kt_list for f in kt["buckets"]})
    return {"groups": rows, "added": sum(r[2] for r in goaled), "target": sum(r[3] for r in goaled),
            "closed": sum(1 for r in goaled if r[2] >= r[3]), "total": len(goaled),
            "targeted": len(files), "files": files}


def _build_week(rep, wtotals, wdeltas, kpi_targets, kpi_files,
                is_current=False, member_added=None, modes_cfg=None):
    member_added = member_added or {}
    files_by_mode = ({k: {f for kt in v for f in kt["buckets"]} for k, v in modes_cfg.items()}
                     if modes_cfg else None)
    geo = []
    members_map = {}
    for fname, label in list(B.SUMMARY_ORDER) + [(B.NOT_STATED_FILE, "🏳 Не указано")]:
        flag, name = split_label(label)
        if is_current and fname in B.GROUP_MEMBERS:          # смешанная группа → агрегируем
            total = B.group_total(fname, wtotals)
            delta = _grp_add([fname], wdeltas)
            mem = []
            for mf in B.GROUP_MEMBERS[fname]:
                mt = wtotals.get(mf, 0)
                if mt:
                    cn = mf[:-4]
                    mem.append([_flag(cn), cn, mt, int(member_added.get(mf, 0))])
            res = wtotals.get(fname, 0)
            if res:
                mem.append(["🌐", "прочие (gTLD/vanity)", res, int(member_added.get(fname, 0))])
            mem.sort(key=lambda x: -x[2])
            members_map[name] = mem
        else:
            total, delta = wtotals.get(fname, 0), wdeltas.get(fname, 0)
        tgt = ({k: (fname in fs) for k, fs in files_by_mode.items()}
               if files_by_mode else (fname in kpi_files))
        geo.append([flag, name, total, delta, tgt])
    kpi = compute_kpi(kpi_targets, wdeltas)
    groups = [[*split_label(r["label"]), r["added"], r["target"]] for r in kpi]
    week = {
        "date": rep["date"], "label": _week_label(rep["date"]),
        "servers": generic_servers(rep["servers"]) or "—",
        "geo": geo, "groups": groups,
        "totals": {
            "base": sum(wtotals.values()), "added": sum(wdeltas.values()),
            "kpi_added": sum(r["added"] for r in kpi), "kpi_target": sum(r["target"] for r in kpi),
            "groups_closed": sum(1 for r in kpi if r["added"] >= r["target"]),
            "groups_total": len(kpi), "targets": sum(1 for g in geo if g[4]),
        },
    }
    if modes_cfg:                                             # режимы KPI (для всех недель из отчётов)
        week["modes"] = {k: _mode_data(v, wdeltas) for k, v in modes_cfg.items()}
    if is_current:                                            # раскрытие на страны — только текущая (живые члены)
        week["members"] = members_map
    return week


def render_html(cfg, totals, reports, kpi_modes=None, member_added=None):
    kpi_targets = cfg.get("kpi_targets", [])
    kpi_files = {f for kt in kpi_targets for f in kt["buckets"]}
    modes_cfg = kpi_modes or {}
    member_added = member_added or {}

    weeks = []
    for i, rep in enumerate(reports):
        is_cur = i == len(reports) - 1
        if is_cur:                 # последняя неделя — по ЖИВЫМ бакетам; прирост из сайдкара (органика+добор)
            wtotals = totals
            wdeltas = member_added if member_added else weekly_deltas(totals, rep)
            weeks.append(_build_week(rep, wtotals, wdeltas, kpi_targets, kpi_files,
                                     is_current=True, member_added=member_added, modes_cfg=modes_cfg))
        else:                      # исторические — из самого отчёта (totals+added как есть) + режимы KPI
            wtotals = {f: t for f, (t, a) in rep["per_file"].items()}
            wdeltas = {f: a for f, (t, a) in rep["per_file"].items()}
            weeks.append(_build_week(rep, wtotals, wdeltas, kpi_targets, kpi_files,
                                     modes_cfg=modes_cfg))
    if not weeks:  # нет отчётов — одна неделя по живым бакетам без прироста
        weeks.append(_build_week({"date": "—", "per_file": {}, "servers": ""},
                                 totals, {f: 0 for f in totals}, kpi_targets, kpi_files,
                                 is_current=True, member_added=member_added, modes_cfg=modes_cfg))

    regions_cfg = []
    for fname, label in list(B.SUMMARY_ORDER) + [(B.NOT_STATED_FILE, "🏳 Не указано")]:
        flag, name = split_label(label)
        regions_cfg.append([flag, name, fname, fname in kpi_files])
    cfg_js = {"regions": regions_cfg, "notStated": B.NOT_STATED_FILE,
              "kpi": [[kt["label"], kt["target"], kt["buckets"]] for kt in kpi_targets],
              # определения режимов + карта стран-членов — чтобы клиент считал режимы
              # для вручную добавленных недель (у них нет baked-modes)
              "modesCfg": {k: [[kt["label"], kt["target"], kt["buckets"]] for kt in v]
                           for k, v in modes_cfg.items()},
              "groupMembers": {g: sorted(ms) for g, ms in B.GROUP_MEMBERS.items()}}

    gen = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    cur = len(weeks) - 1
    data = {"weeks": weeks, "current": cur, "gen": gen, "cfg": cfg_js}
    data_json = json.dumps(data, ensure_ascii=False)
    kpi_target = weeks[cur]["totals"]["kpi_target"]

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Еженедельная сводка по регионам</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header data-anchor="Заголовок">
    <div class="topnav">
      <a class="navlink pri" href="/hypotheses">Гипотезы →</a>
      <a class="navlink" href="/control" id="ctlLink" hidden>🕹 Контроль</a>
      <button class="theme-btn" id="tt">◐ тема</button>
      <a class="navlink" href="/logout">Выход</a>
    </div>
    <p class="eyebrow">Еженедельная сводка · регионы</p>
    <h1>Недельный прирост базы</h1>
    <p class="sub">Неделя: <select class="wsel" id="weekSel"></select>
      <button class="wbtn" id="addWeek" hidden>＋ неделя</button>
      <button class="wbtn danger" id="delWeek" hidden title="удалить эту неделю">✕</button>
      · источников: <span id="hSrc">—</span></p>
    <div class="servers"><span><span class="dot">●</span> <span id="hServers">—</span></span>
      <span>Всего в базе: <b class="num" id="hBase">—</b></span>
      <span class="kpimode-wrap">KPI: <span class="seg kpimode" id="kpimodeSeg" hidden></span></span></div>
  </header>

  <div class="cmp-bar" id="cmpBar">
    <label><input type="checkbox" id="cmpOn" checked> Сравнить с пред. неделей</label>
    <span class="cmp-parts">сравнивать:
      <label><input type="checkbox" data-cmp="hero" checked> плитки</label>
      <label><input type="checkbox" data-cmp="kpi" checked> план</label>
      <label><input type="checkbox" data-cmp="groups" checked> группы</label>
      <label><input type="checkbox" data-cmp="geo" checked> регионы</label>
    </span>
  </div>

  <section class="hero" id="hero"></section>

  <section class="panel collapsible">
    <div class="panel-head"><h2><span class="chev">▾</span> План недели</h2><span class="hint">прирост / план</span></div>
    <div class="panel-body" id="kpiPanel" data-anchor="План недели"></div>
  </section>

  <section class="panel collapsible">
    <div class="panel-head"><h2><span class="chev">▾</span> План по группам</h2><span class="hint">факт / план за неделю</span></div>
    <div class="panel-body"><div class="grp-grid" id="groups"></div></div>
  </section>

  <section class="panel collapsible" id="geoTable">
    <div class="panel-head"><h2><span class="chev">▾</span> Прирост по регионам</h2>
      <span class="seg" id="geoFilter"><button data-f="all">Все</button><button data-f="tgt">Только цель</button></span></div>
    <div class="panel-body"><div class="tbl-scroll"><table>
      <thead><tr>
        <th class="l s" data-k="geo">Регион <span class="ind"></span></th>
        <th class="s" data-k="total">Накоплено <span class="ind"></span></th>
        <th class="s" data-k="delta">Прирост <span class="ind"></span></th>
        <th class="n cmp-col" id="geoCmpTh" hidden>Δ нед.</th>
        <th class="l">Динамика</th>
      </tr></thead>
      <tbody id="geoRows"></tbody>
    </table></div>
    <p class="cmt-mode">Клик по заголовку — сортировка · клик по строке — выделение.</p></div>
  </section>

  <section class="panel collapsible">
    <div class="panel-head"><h2><span class="chev">▾</span> Заметки</h2><span class="hint">выделите текст → «Комментировать»</span></div>
    <div class="panel-body">
      <div id="cmtList"></div>
      <div class="cmt-mode" id="cmtMode"></div>
    </div>
  </section>

  <div class="foot">
    <div class="note"><b>Целевые / нецелевые.</b> План считается по {len(kpi_targets)} группам целевых регионов
      (план {kpi_target}). Крупные нецелевые регионы в план не входят, хотя пополняют базу.</div>
    <div class="meta-line">Обновлено {esc(gen)}</div>
  </div>
</div>
<div class="modal" id="wkModal" hidden>
  <div class="modal-card">
    <h3>Добавить неделю</h3>
    <label class="wk-lbl">Дата конца недели <input type="date" id="wkDate"></label>
    <textarea id="wkText" placeholder="Вставьте отчёт: строки вида «🇺🇸 США 8971 (+273)», «Не указано 7630 (+96)» …"></textarea>
    <div class="wk-err" id="wkErr"></div>
    <div class="modal-actions"><button class="pri" id="wkSave">Добавить</button><button class="ghost" id="wkCancel">Отмена</button></div>
  </div>
</div>
<div id="selPop" class="cmt-sel" hidden>💬 Комментировать</div>
<div id="pop" class="cmt-pop" hidden></div>
<script>window.DATA = {data_json};</script>
<script>{APP_JS}</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site"))
    args = ap.parse_args()
    cfg = load_config()
    defaults = json.loads(DEFAULTS_PATH.read_text(encoding="utf-8")) if DEFAULTS_PATH.exists() else {}
    buckets_dir = resolve_dir(cfg.get("buckets_dir"),
                              defaults.get("buckets_dir", "/srv/share/Split/out_country_buckets"), "*.txt")
    report_dir = resolve_dir(cfg.get("report_out_dir"),
                             defaults.get("report_out_dir", "/srv/share/Split/reports"), "gsa_report_*.txt")
    sys.stderr.write(f"[dashboard] buckets={buckets_dir}  reports={report_dir}\n")
    totals = bucket_totals(buckets_dir)
    reports = parse_all_reports(report_dir)
    # режимы KPI (старый/KPI GSA) + сайдкар прироста по странам-членам
    kpi_modes = None
    km = buckets_dir.parent / "kpi_modes.json"
    if km.exists():
        try:
            allm = json.loads(km.read_text(encoding="utf-8"))
            kpi_modes = {"old": allm["old"], "gsa": allm["gsa"]}   # только Старый + KPI GSA(«Новый»)
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    member_added = {}
    if reports:
        sc = Path(reports[-1]["path"]).with_suffix(".detail.json")
        if sc.exists():
            try:
                member_added = json.loads(sc.read_text(encoding="utf-8")).get("added", {})
            except (OSError, json.JSONDecodeError):
                pass
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(
        render_html(cfg, totals, reports, kpi_modes, member_added), encoding="utf-8")
    print(f"OK: {out_dir/'index.html'}  (регионов: {len(totals)}, отчётов: {len(reports)}, "
          f"сумма базы: {sum(totals.values())})")


if __name__ == "__main__":
    main()
