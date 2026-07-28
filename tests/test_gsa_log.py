"""--gsa-log: чтение debug-папки GSA SER (HTML-дампы проблемных целей)."""

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


def test_reads_appdata_debug_folder(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    (d / "site-a.ru_AB12.html").write_text("<html>Please solve the captcha</html>")
    (d / "site-a.ru_CD34.html").write_text("<html>403 Forbidden</html>")
    (d / "site-b.com_EF56.html").write_bytes(b"")

    out = _run(monkeypatch, capsys, tmp_path)

    assert str(d) in out
    assert "файлов: 3" in out
    assert "уникальных доменов: 2" in out
    assert "2  site-a.ru" in out
    assert "капча" in out and "доступ запрещён" in out
    assert "пустых (0 байт" in out
    assert "site-b.com_EF56.html" in out          # листинг новейших


def test_config_dir_wins_over_appdata(tmp_path, monkeypatch, capsys):
    _debug_dir(tmp_path)                          # стандартный путь тоже существует
    custom = tmp_path / "custom-debug"
    custom.mkdir()
    (custom / "site-x.io_9911.html").write_text("<html>login required</html>")

    out = _run(monkeypatch, capsys, tmp_path, cfg={"gsa_debug_dir": str(custom)})

    assert str(custom) in out
    assert "site-x.io_9911.html" in out


def test_lines_limits_listing(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    for i in range(5):
        (d / f"site-{i}.ru_AA{i}.html").write_text("<html>ok</html>")

    out = _run(monkeypatch, capsys, tmp_path, lines=2)

    assert len([ln for ln in out.splitlines() if ln.strip().endswith(".html")]) == 2


def test_mail_filter_selects_dumps_with_mail_traces(tmp_path, monkeypatch, capsys):
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
