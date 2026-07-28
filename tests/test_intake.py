"""Тесты intake API: регионы (split1404 + кастомные) и валидация заявки (tmp-бакеты)."""

import intake


def _cfg(tmp_path):
    """Временная папка бакетов с реальными именами файлов регионов."""
    for f in ("Poland.txt", "africa.txt", "Other-Africa.txt",
              "Europe-Other.txt", "Asia-other.txt"):
        (tmp_path / f).write_text("http://a.example/\n", encoding="utf-8")
    return {"buckets_dir": str(tmp_path)}


def test_valid_countries(tmp_path):
    vc = intake.valid_countries(_cfg(tmp_path))
    assert "Польша" in vc
    assert {"Африка", "Европа", "Азия"} <= set(vc)          # кастомные
    assert "Южная Африка" not in vc                          # заменена «Африкой»
    assert "Другие страны Африки" not in vc
    assert "Другие страны Европы" not in vc and "Другие страны Азии" not in vc


def test_region_single(tmp_path):
    clean, errors = intake.validate_task(_cfg(tmp_path), {
        "url": "https://money-site.com/", "country": "Польша",
        "anchors": ["a1", "a2"], "links_per_day": 10})
    assert not errors
    assert len(clean["buckets"]) == 1 and clean["buckets"][0].endswith("Poland.txt")


def test_region_merge_africa(tmp_path):
    clean, errors = intake.validate_task(_cfg(tmp_path), {
        "url": "https://x/", "country": "Африка", "anchors": ["a"], "links_per_day": 5})
    assert not errors and len(clean["buckets"]) == 2
    ends = {b.rsplit("/", 1)[-1] for b in clean["buckets"]}
    assert ends == {"africa.txt", "Other-Africa.txt"}


def test_region_rename(tmp_path):
    cfg = _cfg(tmp_path)
    for name, fn in (("Европа", "Europe-Other.txt"), ("Азия", "Asia-other.txt")):
        clean, errors = intake.validate_task(cfg, {
            "url": "https://x/", "country": name, "anchors": ["a"], "links_per_day": 5})
        assert not errors and clean["buckets"][0].endswith(fn)


def test_english_fallback(tmp_path):
    clean, errors = intake.validate_task(_cfg(tmp_path), {
        "url": "https://x/", "country": "Poland", "anchors": ["a"], "links_per_day": 5})
    assert not errors and clean["buckets"][0].endswith("Poland.txt")


def test_english_aliases_and_stem(tmp_path):
    cfg = _cfg(tmp_path)
    al = intake.english_aliases(cfg)
    assert "Poland" in al and "africa" in al             # стемы файлов-бакетов
    # английский стем резолвится (в один файл, без объединения «Африки»)
    clean, errors = intake.validate_task(cfg, {
        "url": "https://x/", "country": "africa", "anchors": ["a"], "links_per_day": 5})
    assert not errors and len(clean["buckets"]) == 1 and clean["buckets"][0].endswith("africa.txt")


def test_rejects_bad(tmp_path):
    cfg = _cfg(tmp_path)
    assert intake.validate_task(cfg, {"url": "ftp://x", "country": "Польша",
                                      "anchors": ["a"], "links_per_day": 5})[1]
    assert intake.validate_task(cfg, {"url": "https://x/", "country": "НетТакой",
                                      "anchors": ["a"], "links_per_day": 5})[1]
    assert intake.validate_task(cfg, {"url": "https://x/", "country": "Польша",
                                      "anchors": [], "links_per_day": 5})[1]
    assert intake.validate_task(cfg, {"url": "https://x/", "country": "Польша",
                                      "anchors": ["a"], "links_per_day": 0})[1]


def test_safe_label():
    assert intake._safe_label("https://Money-Site.com/path") == "Money-Site.com"
    # кириллица в хосте (IDN) не должна попасть в ASCII-имя проекта
    assert intake._safe_label("https://сайт.рф/x").isascii()


def test_path_slug():
    # разные страницы одного домена дают разный слаг → разные имена .prj
    assert intake._path_slug("https://x.com/minimum-deposit") == "minimum-deposit"
    assert intake._path_slug("https://x.com/a/b-c") == "a-b-c"
    assert intake._path_slug("https://x.com/") == ""          # корень
    assert intake._path_slug("https://x.com") == ""
    assert intake._path_slug("https://x.com/Логин").isascii()  # не-ASCII вычищается


def test_ascii_country_from_bucket():
    # русское имя региона в имя проекта не идёт — берём ASCII-стем бакета
    assert intake._ascii_country({"buckets": ["/x/Malaysia.txt"]}) == "Malaysia"
    assert intake._ascii_country({"buckets": ["/x/africa.txt", "/x/Other-Africa.txt"]}) == "africa"
    assert intake._ascii_country({"buckets": []}) == "geo"
    assert intake._ascii_country({"buckets": ["/x/Malaysia.txt"]}).isascii()


def test_build_report():
    recs = [
        {"status": "queued", "url": "https://a/", "country": "Польша", "task_id": "t_1"},
        {"status": "error", "url": "https://b/", "country": "Азия", "code": 500, "error": "boom"},
        {"status": "invalid", "url": "https://c/", "country": "", "code": 400, "error": "country"},
    ]
    msg = intake.build_report(recs, mention="@ruslan")
    assert "3 проект(ов)" in msg and "✅ 1" in msg
    assert "✅ https://a/ → Польша  (t_1)" in msg
    assert "❌ https://b/" in msg and "500" in msg
    assert "@ruslan" in msg and "refresh" in msg.lower()
    # без успехов — без тега
    assert "@ruslan" not in intake.build_report([recs[1]], mention="@ruslan")


def test_authed_non_ascii_header():
    """Не-ASCII в Authorization не должен ронять поток (compare_digest со str падал)."""
    def _authed(token, header):
        h = type("H", (), {"token": token,
                           "headers": {"Authorization": header}})()
        return intake.Handler._authed(h)
    assert _authed("secret-token", "Bearer тест-кириллица") is False   # раньше — TypeError
    assert _authed("secret-token", "Bearer secret-token") is True
    assert _authed("secret-token", "Bearer wrong") is False
    assert _authed("", "Bearer secret-token") is False
