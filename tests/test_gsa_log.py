"""--gsa-log: полный разбор debug-папки GSA SER + запись подробного отчёта."""

import json
import types

import gsa_checker


def _debug_dir(tmp_path):
    d = tmp_path / "GSA Search Engine Ranker" / "debug"
    d.mkdir(parents=True)
    return d


def _run(monkeypatch, capsys, appdata, cfg=None, **kw):
    monkeypatch.setenv("APPDATA", str(appdata))
    args = types.SimpleNamespace(lines=kw.get("lines"), mail=kw.get("mail", False))
    gsa_checker.cmd_gsa_log(cfg or {}, args)
    return capsys.readouterr().out


def _fill(d):
    (d / "forum.ru_AB12.html").write_text(
        "<html lang='ru'><title>Вход</title>wp-content please login</html>")
    (d / "forum.ru_CD34.html").write_text(
        "<html lang='ru'><title>Вход</title>wp-content register now</html>")
    (d / "shop.de_EF56.html").write_text("<html lang='de'>403 Forbidden cloudflare</html>")
    (d / "dead.io_0011.html").write_bytes(b"")


def test_summarises_whole_folder(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    _fill(d)

    out = _run(monkeypatch, capsys, tmp_path)

    assert str(d) in out
    assert "файлов: 4" in out
    assert "доменов: 3" in out
    assert "WordPress ×2" in out
    assert "требуется вход" in out
    assert "ru ×2" in out                       # зоны/языки


def test_writes_detailed_report_next_to_autopilot_stats(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    _fill(d)
    share = tmp_path / "share"
    share.mkdir()

    out = _run(monkeypatch, capsys, tmp_path,
               cfg={"autopilot_stats_dir": str(share), "server_name": "gsa-03"})

    txt = share / "gsa-03.debug_scan.txt"
    js = share / "gsa-03.debug_scan.json"
    assert txt.exists() and js.exists()
    assert str(txt) in out                      # путь виден в сводке
    body = txt.read_text()
    assert "Топ-30 доменов по числу дампов:" in body
    assert "forum.ru" in body
    assert json.loads(js.read_text())["files"] == 4


def test_unreadable_report_dir_does_not_break_scan(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    _fill(d)

    out = _run(monkeypatch, capsys, tmp_path,
               cfg={"autopilot_stats_dir": str(tmp_path / "missing")})

    assert "файлов: 4" in out                   # папки отчёта нет — скан всё равно прошёл


def test_config_dir_wins_over_appdata(tmp_path, monkeypatch, capsys):
    _debug_dir(tmp_path)                        # стандартный путь тоже существует
    custom = tmp_path / "custom-debug"
    custom.mkdir()
    (custom / "site-x.io_9911.html").write_text("<html>login required</html>")

    out = _run(monkeypatch, capsys, tmp_path, cfg={"gsa_debug_dir": str(custom)})

    assert str(custom) in out


def test_mail_filter_lists_dumps_with_mail_traces(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    (d / "forum.ru_AB12.html").write_text("<html>confirm your e-mail to activate</html>")
    (d / "shop.ru_CD34.html").write_text("<html>nothing relevant here</html>")

    out = _run(monkeypatch, capsys, tmp_path, mail=True)

    assert "forum.ru_AB12.html" in out
    assert "shop.ru_CD34.html" not in out


def test_missing_folder_explains_where_to_look(tmp_path, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, tmp_path / "nope")

    assert "debug-папка GSA не найдена" in out
    assert "gsa_debug_dir" in out


def test_empty_folder_is_reported(tmp_path, monkeypatch, capsys):
    _debug_dir(tmp_path)

    out = _run(monkeypatch, capsys, tmp_path)

    assert "пуста" in out
