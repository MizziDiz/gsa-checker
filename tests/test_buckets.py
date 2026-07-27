"""Тест маппинга ISO2-код страны → название → страновой бакет.

Проверяет всю цепочку, на которой стоит недельный `--report`:
    iso2.name_for(code) → buckets.resolve_country(name) → buckets.bucket_for_country(...)
"""

import pytest

from lib import buckets, iso2


def bucket_of(code: str) -> str:
    """ISO2-код → имя файла-бакета (как в out_country_buckets)."""
    name = iso2.name_for(code)
    return buckets.bucket_for_country(buckets.resolve_country(name))


# (ISO2-код, ожидаемый файл-бакет)
CASES = [
    ("pl", "Poland.txt"),
    ("us", "USA.txt"),
    ("gb", "UK.txt"),
    ("uk", "UK.txt"),           # ccTLD-вариант Великобритании
    ("ca", "USA.txt"),          # спец-правило: Канада → USA
    ("nz", "australia.txt"),    # спец-правило: Новая Зеландия → Австралия
    ("vn", "vietnam.txt"),
    ("br", "brazil.txt"),
    ("mx", "Mexic.txt"),        # COUNTRY_FILES важнее региона LATAM
    ("ru", "Russia.txt"),
    ("tr", "turkish.txt"),
    ("cn", "china-mix.txt"),
    ("jp", "japanese.txt"),
    ("in", "India.txt"),
    ("id", "Indonesia.txt"),
    ("gr", "Europe-Other.txt"), # Греция → регион «Другие страны Европы»
    ("za", "africa.txt"),       # ЮАР → регион Африка
    ("ua", "sng.txt"),          # Украина → СНГ
    ("eg", "arabic.txt"),       # Египет → арабские
    ("ir", "Asia-other.txt"),   # Иран → прочая Азия
]


@pytest.mark.parametrize("code,expected", CASES)
def test_code_to_bucket(code, expected):
    assert bucket_of(code) == expected


@pytest.mark.parametrize("code", ["", "zz", "xx", "  "])
def test_unknown_or_empty_is_not_stated(code):
    """Пустой/неизвестный код → бакет Not Stated (как в split1404)."""
    assert bucket_of(code) == buckets.NOT_STATED_FILE


def test_iso2_case_insensitive():
    assert iso2.name_for("PL") == iso2.name_for("pl") == "Poland"


def test_every_kpi_bucket_is_reachable():
    """Все файлы-бакеты из SUMMARY_ORDER — валидные цели bucket_for_country."""
    reachable = set(buckets.COUNTRY_FILES.values()) | set(buckets.REGION_FILES.values())
    for fname, _label in buckets.SUMMARY_ORDER:
        assert fname in reachable, f"{fname} недостижим из COUNTRY_FILES/REGION_FILES"


# ====== Фаза B: сплит смешанных групп по ccTLD ======
def test_cctld_of():
    assert buckets.cctld_of("https://shop.nl/x") == "nl"
    assert buckets.cctld_of("https://shop.com/x") == ""      # gTLD
    assert buckets.cctld_of("https://x.ws/") == ""           # vanity
    assert buckets.cctld_of("https://x.li/") == ""           # vanity
    assert buckets.cctld_of("https://x.io/") == ""           # vanity


def test_bucket_for_url_split():
    assert buckets.bucket_for_url("https://x.nl/", "Netherlands") == "Netherlands.txt"   # ccTLD член
    assert buckets.bucket_for_url("https://x.com/", "Netherlands") == "Europe-Other.txt"  # gTLD → остаток
    assert buckets.bucket_for_url("https://x.ar/", "Argentina") == "Argentina.txt"         # свой файл
    assert buckets.bucket_for_url("https://x.tw/", "Taiwan") == "Taiwan.txt"               # china-mix сплит
    assert buckets.bucket_for_url("https://x.kr/", "?") == "Korea.txt"                     # не сплит-группа


def test_group_members_and_total():
    assert "Netherlands.txt" in buckets.GROUP_MEMBERS["Europe-Other.txt"]
    assert "Taiwan.txt" in buckets.GROUP_MEMBERS["china-mix.txt"]
    assert "Cuba.txt" in buckets.GROUP_MEMBERS["latam.txt"]
    # vanity не порождает member-файлов
    assert not any("Liechtenstein" in f for f in buckets.MEMBER_FILES)
    counts = {"Europe-Other.txt": 100, "Netherlands.txt": 20, "Sweden.txt": 5}
    assert buckets.group_total("Europe-Other.txt", counts) == 125
    assert buckets.group_total("USA.txt", {"USA.txt": 7}) == 7   # не-группа = сам файл
