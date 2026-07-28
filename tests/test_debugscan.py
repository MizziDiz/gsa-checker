"""Разбор debug-папки GSA SER на статистику (lib/debugscan)."""

from lib import debugscan


def test_split_name_strips_only_gsa_hex_suffix():
    assert debugscan.split_name("forum.ru_AB12CD.html") == "forum.ru"
    assert debugscan.split_name("my_site.ru_9F0A.html") == "my_site.ru"
    assert debugscan.split_name("some_name.html") == "some_name"   # хвост не hex
    assert debugscan.split_name("plain.html") == "plain"


def test_scan_counts_everything(tmp_path):
    (tmp_path / "forum.ru_AB12.html").write_text(
        "<html lang='ru'><title>Вход</title>wp-content g-recaptcha please login</html>")
    (tmp_path / "forum.ru_CD34.html").write_text(
        "<html lang='ru'><title>Вход</title>wp-content register now</html>")
    (tmp_path / "shop.de_EF56.html").write_text(
        "<html lang='de'><title>Fehler</title>403 Forbidden cloudflare</html>")
    (tmp_path / "dead.io_0011.html").write_bytes(b"")

    st = debugscan.scan(tmp_path)

    assert st["files"] == 4
    assert st["bodies_read"] == 3          # пустой не читается
    assert st["empty"] == 1
    assert st["unique_hosts"] == 3
    assert dict(st["hosts"])["forum.ru"] == 2
    assert dict(st["tlds"])["ru"] == 2
    assert dict(st["engines"])["WordPress"] == 2
    assert dict(st["captchas"])["reCAPTCHA"] == 1
    assert dict(st["langs"])["ru"] == 2
    assert dict(st["signs"])["требуется вход"] == 1
    assert dict(st["signs"])["пусто (нет ответа)"] == 1
    assert dict(st["titles"])["Вход"] == 2
    assert st["host_repeat"] == {"1 дамп": 2, "2–4 дампа": 1}


def test_scan_dedupes_identical_bodies(tmp_path):
    body = "<html>same body</html>"
    for name in ("a.ru_1111.html", "b.ru_2222.html", "c.ru_3333.html"):
        (tmp_path / name).write_text(body)
    (tmp_path / "d.ru_4444.html").write_text("<html>other</html>")

    st = debugscan.scan(tmp_path)

    assert st["unique_bodies"] == 2


def test_scan_survives_unreadable_file(tmp_path, monkeypatch):
    (tmp_path / "ok.ru_1111.html").write_text("<html>fine</html>")
    bad = tmp_path / "bad.ru_2222.html"
    bad.write_text("<html>x</html>")

    real_open = type(bad).open

    def flaky(self, *a, **kw):
        if self.name.startswith("bad"):
            raise OSError("locked")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(type(bad), "open", flaky)
    st = debugscan.scan(tmp_path)

    assert st["read_errors"] == 1
    assert st["bodies_read"] == 1
