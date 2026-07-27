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
