"""Тесты xrumer_autopilot.py — чистая логика без сети, SMB и пула на шаре.

Проверяет отбор батчей по свежести и лимиту, дедуп/фильтрацию URL в пределах
прогона и дозапись базы с обрезкой с головы. Захват батча (перенос) и заливку
по SMB тут не дёргаем.
"""

import importlib


xa = importlib.import_module("xrumer_autopilot")


# ───────────────────────── отбор батчей ─────────────────────────

def _touch(p, size, mtime):
    p.write_bytes(b"x" * size)
    import os
    os.utime(p, (mtime, mtime))


def test_select_batches_newest_first_until_limit(tmp_path):
    # три файла по 1 МБ, разные mtime; лимит 2 МБ → берём два новейших
    mb = 1024 * 1024
    _touch(tmp_path / "old.txt", mb, 1000)
    _touch(tmp_path / "mid.txt", mb, 2000)
    _touch(tmp_path / "new.txt", mb, 3000)
    (tmp_path / "skip.dat").write_bytes(b"y" * mb)      # не под *.txt
    got = xa.select_batches(tmp_path, "*.txt", limit_mb=2)
    assert [p.name for p in got] == ["new.txt", "mid.txt"]


def test_select_batches_all_when_under_limit(tmp_path):
    _touch(tmp_path / "a.txt", 1024, 1000)
    _touch(tmp_path / "b.txt", 1024, 2000)
    got = xa.select_batches(tmp_path, "*.txt", limit_mb=999)
    assert {p.name for p in got} == {"a.txt", "b.txt"}


# ───────────────────────── сбор и дедуп URL ─────────────────────────

def test_collect_urls_filters_and_dedups(tmp_path):
    b1 = tmp_path / "b1.txt"
    b1.write_bytes(b"http://a.com/1\nhttps://b.com/2\nnot-a-url\n\nftp://x\n")
    b2 = tmp_path / "b2.txt"
    b2.write_bytes(b"http://a.com/1\nhttp://c.com/3\n")     # первый URL — дубль
    seen: set[bytes] = set()
    urls, skipped = xa.collect_urls([b1, b2], seen)
    assert urls == [b"http://a.com/1", b"https://b.com/2", b"http://c.com/3"]
    assert skipped == 2                                     # not-a-url и ftp://x


def test_collect_urls_seen_persists_across_calls(tmp_path):
    b = tmp_path / "b.txt"
    b.write_bytes(b"http://a.com/1\n")
    seen: set[bytes] = set()
    xa.collect_urls([b], seen)
    urls, _ = xa.collect_urls([b], seen)                    # тот же URL, тот же seen
    assert urls == []


# ───────────────────────── дозапись базы ─────────────────────────

def test_append_local_base_creates_and_appends(tmp_path):
    base = tmp_path / "node" / "Autopilot.txt"
    n = xa.append_local_base(base, [b"http://a.com/1", b"http://b.com/2"], max_lines=0)
    assert n == 2
    assert base.read_bytes() == b"http://a.com/1\nhttp://b.com/2\n"
    n = xa.append_local_base(base, [b"http://c.com/3"], max_lines=0)
    assert n == 3
    assert base.read_text().splitlines()[-1] == "http://c.com/3"


def test_append_local_base_trims_from_head(tmp_path):
    base = tmp_path / "Autopilot.txt"
    xa.append_local_base(base, [b"u1", b"u2", b"u3"], max_lines=0)
    # добавляем ещё две, кап 3 → остаются самые свежие: u3,u4,u5
    n = xa.append_local_base(base, [b"u4", b"u5"], max_lines=3)
    assert n == 3
    assert base.read_text().split() == ["u3", "u4", "u5"]


def test_append_local_base_empty(tmp_path):
    base = tmp_path / "Autopilot.txt"
    n = xa.append_local_base(base, [], max_lines=1000)
    assert n == 0
    assert base.read_bytes() == b""


# ───────────────────────── слияние настроек ─────────────────────────

def test_ap_settings_merges_over_defaults():
    cfg = {"xrumer": {"autopilot": {"base": "Custom.txt", "batch_limit_mb": 50,
                                    "unknown_key": "ignored", "pool_dir": None}}}
    ap = xa.ap_settings(cfg)
    assert ap["base"] == "Custom.txt"
    assert ap["batch_limit_mb"] == 50
    assert ap["pool_dir"] == xa.DEFAULTS["pool_dir"]        # None не перетирает дефолт
    assert "unknown_key" not in ap


def test_ap_settings_defaults_when_absent():
    assert xa.ap_settings({}) == dict(xa.DEFAULTS)
