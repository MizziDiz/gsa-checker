#!/usr/bin/env python3
"""
lib/statsdb.py — снимки статистики проектов во времени (SQLite) + скорость/ETA/стоп.

Каждый прогон --stats пишет по строке на проект в snapshots(ts, project, …). По
истории считаем:
  • скорость расхода целей (Δremaining/Δt) → ETA до исчерпания .targets;
  • скорость verified (Δverified/Δt);
  • «проект встал» — за окно цели не убывают И verified не растёт, а остаток > 0.

Инкрементально и дёшево: одна таблица, индекс по (project, ts), ретенция по дням.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
  ts        INTEGER NOT NULL,
  project   TEXT    NOT NULL,
  remaining INTEGER,
  verified  INTEGER,
  to_verify INTEGER,
  done      INTEGER,
  PRIMARY KEY(ts, project)
);
CREATE INDEX IF NOT EXISTS idx_snap_proj_ts ON snapshots(project, ts);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def record(con: sqlite3.Connection, rows: list[dict], ts: int | None = None) -> int:
    """Пишет снимок по каждому проекту. rows — из collect_stats()['projects']."""
    ts = int(ts if ts is not None else time.time())
    con.executemany(
        "INSERT OR REPLACE INTO snapshots(ts,project,remaining,verified,to_verify,done)"
        " VALUES(?,?,?,?,?,?)",
        [(ts, r["name"], r["remaining"], r["verified"], r["to_verify"], r["done"])
         for r in rows],
    )
    con.commit()
    return ts


def velocity(con: sqlite3.Connection, project: str, window_sec: float,
             now: float | None = None) -> dict | None:
    """Скорость/ETA/стоп по снимкам проекта за окно window_sec. None — если данных
    в окне < 2 или окно нулевой длины.

    Возвращает: targets_per_hr, verified_per_hr, eta_sec (None если цели не
    убывают), remaining (последний), stalled (bool), samples, span_sec.
    """
    now = now if now is not None else time.time()
    cur = con.execute(
        "SELECT ts,remaining,verified FROM snapshots "
        "WHERE project=? AND ts>=? ORDER BY ts",
        (project, int(now - window_sec)),
    )
    rows = cur.fetchall()
    if len(rows) < 2:
        return None
    (t0, rem0, ver0), (t1, rem1, ver1) = rows[0], rows[-1]
    dt = t1 - t0
    if dt <= 0:
        return None
    d_consumed = rem0 - rem1              # сколько целей ушло
    d_verified = ver1 - ver0
    consume_rate = d_consumed / dt        # целей/сек (может быть <=0)
    eta_sec = (rem1 / consume_rate) if consume_rate > 0 else None
    stalled = d_consumed <= 0 and d_verified <= 0 and rem1 > 0
    return {
        "targets_per_hr": consume_rate * 3600,
        "verified_per_hr": (d_verified / dt) * 3600,
        "eta_sec": eta_sec,
        "remaining": rem1,
        "stalled": stalled,
        "samples": len(rows),
        "span_sec": dt,
    }


def prune(con: sqlite3.Connection, retention_days: float,
          now: float | None = None) -> int:
    if retention_days <= 0:
        return 0
    now = now if now is not None else time.time()
    cutoff = int(now - retention_days * 86400)
    n = con.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,)).rowcount
    con.commit()
    return n


def fmt_eta(eta_sec: float | None) -> str:
    """Человекочитаемый ETA: '3д 4ч', '5ч 12м', '43м' или '—'."""
    if eta_sec is None:
        return "—"
    m = int(eta_sec // 60)
    if m < 60:
        return f"{m}м"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}ч {m}м"
    d, h = divmod(h, 24)
    return f"{d}д {h}ч"
