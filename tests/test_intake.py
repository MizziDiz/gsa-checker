"""Тесты intake API: список стран и валидация заявки (без сети/шары — tmp-бакеты)."""

import intake
from lib import buckets


def _cfg(tmp_path):
    """Временная папка бакетов с реальным именем файла для 'Poland'."""
    fname = buckets.bucket_for_country(buckets.resolve_country("Poland"))
    (tmp_path / fname).write_text("http://a.pl\n", encoding="utf-8")
    return {"buckets_dir": str(tmp_path)}


def test_valid_countries_russian(tmp_path):
    # /api/countries отдаёт русские названия регионов (как в split1404)
    assert "Польша" in intake.valid_countries(_cfg(tmp_path))


def test_validate_russian_region(tmp_path):
    clean, errors = intake.validate_task(_cfg(tmp_path), {
        "url": "https://money-site.com/", "country": "Польша",
        "anchors": ["a1", "a2", "a3", "a4"], "links_per_day": 10})
    assert not errors
    assert clean["bucket"].endswith("Poland.txt")
    assert len(clean["anchors"]) == 4


def test_validate_english_fallback(tmp_path):
    # английское имя тоже принимается (фолбэк)
    clean, errors = intake.validate_task(_cfg(tmp_path), {
        "url": "https://money-site.com/", "country": "Poland",
        "anchors": ["a1"], "links_per_day": 10})
    assert not errors and clean["bucket"].endswith("Poland.txt")


def test_validate_rejects_bad(tmp_path):
    cfg = _cfg(tmp_path)
    assert intake.validate_task(cfg, {"url": "ftp://x", "country": "Poland",
                                      "anchors": ["a"], "links_per_day": 5})[1]
    assert intake.validate_task(cfg, {"url": "https://x/", "country": "НетТакой",
                                      "anchors": ["a"], "links_per_day": 5})[1]
    assert intake.validate_task(cfg, {"url": "https://x/", "country": "Poland",
                                      "anchors": [], "links_per_day": 5})[1]
    assert intake.validate_task(cfg, {"url": "https://x/", "country": "Poland",
                                      "anchors": ["a"], "links_per_day": 0})[1]


def test_safe_label():
    assert intake._safe_label("https://Money-Site.com/path") == "Money-Site.com"
