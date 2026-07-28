"""--gsa-log: сводка по debug-папке GSA и тейл текстового лога."""

import types

import gsa_checker


def _run(monkeypatch, capsys, appdata, **kw):
    monkeypatch.setenv("APPDATA", str(appdata))
    args = types.SimpleNamespace(lines=kw.get("lines"), mail=kw.get("mail", False))
    gsa_checker.cmd_gsa_log({}, args)
    return capsys.readouterr().out


def _debug_dir(tmp_path):
    d = tmp_path / "GSA Search Engine Ranker" / "debug"
    d.mkdir(parents=True)
    return d


def test_summarises_html_dumps(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    (d / "site-a.ru_AB12.html").write_text("<html>Please solve the captcha</html>")
    (d / "site-a.ru_CD34.html").write_text("<html>403 Forbidden</html>")
    (d / "site-b.com_EF56.html").write_bytes(b"")

    out = _run(monkeypatch, capsys, tmp_path)

    assert "файлов: 3" in out
    assert "уникальных доменов: 2" in out
    assert "2  site-a.ru" in out
    assert "капча" in out and "доступ запрещён" in out
    assert "пустых (0 байт" in out


def test_text_log_tailed_and_dumps_not_counted_as_domains(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    (d / "site-a.ru_AB12.html").write_text("<html>captcha</html>")
    (d / "ser.log").write_text("line one\nline two\nline three\n")

    out = _run(monkeypatch, capsys, tmp_path, lines=2)

    assert "уникальных доменов: 1" in out          # ser.log не домен
    assert "GSA-лог ser.log" in out
    assert "line three" in out and "line one" not in out


def test_mail_filter_selects_only_mail_lines(tmp_path, monkeypatch, capsys):
    d = _debug_dir(tmp_path)
    (d / "ser.log").write_text("submit ok\nPOP3 login failed\nnothing here\n")

    out = _run(monkeypatch, capsys, tmp_path, mail=True)

    assert "POP3 login failed" in out
    assert "submit ok" not in out


def test_no_logs_at_all_explains_where_to_enable(tmp_path, monkeypatch, capsys):
    _debug_dir(tmp_path)

    out = _run(monkeypatch, capsys, tmp_path)

    assert "Log to file" in out


def test_dump_summary_printed_after_log_tail(tmp_path, monkeypatch, capsys):
    """Панель показывает только хвост вывода задания — сводка должна быть в конце."""
    d = _debug_dir(tmp_path)
    (d / "site-a.ru_AB12.html").write_text("<html>captcha</html>")
    (d / "ser.log").write_text("line one\nline two\n")

    out = _run(monkeypatch, capsys, tmp_path)

    assert out.index("GSA-лог ser.log") < out.index("debug-папка")


def test_changes_log_is_not_treated_as_a_log(tmp_path, monkeypatch, capsys):
    """changes.log — релиз-ноутс GSA, не лог работы."""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    (exe_dir / "changes.log").write_text("1.20 - new: first public release\n")
    monkeypatch.setenv("APPDATA", str(tmp_path / "nope"))
    import types as _t
    gsa_checker.cmd_gsa_log({"gsa_exe_path": str(exe_dir / "ser.exe")},
                            _t.SimpleNamespace(lines=None, mail=False))
    out = capsys.readouterr().out

    assert "first public release" not in out
    assert "Log to file" in out
