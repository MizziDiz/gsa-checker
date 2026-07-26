"""Тесты intake API: список стран и валидация заявки (без сети/шары — tmp-бакеты)."""

import intake
from lib import buckets


def _cfg(tmp_path):
    """Временная папка бакетов с реальным именем файла для 'Poland'."""
    fname = buckets.bucket_for_country(buckets.resolve_country("Poland"))
    (tmp_path / fname).write_text("http://a.pl\n", encoding="utf-8")
    return {"buckets_dir": str(tmp_path)}


def test_valid_countries(tmp_path):
    assert "Poland" in intake.valid_countries(_cfg(tmp_path))


def test_validate_ok(tmp_path):
    clean, errors = intake.validate_task(_cfg(tmp_path), {
        "url": "https://money-site.com/", "country": "Poland",
        "anchors": ["a1", "a2", "a3", "a4"], "links_per_day": 10})
    assert not errors
    assert clean["links_per_day"] == 10
    assert clean["bucket"].endswith(".txt")
    assert len(clean["anchors"]) == 4


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
