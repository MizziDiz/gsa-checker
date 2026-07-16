#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lib/buckets.py — раскладка verified-ссылок по странам-бакетам, ПОРТ 1:1 из
`Split/split1404.py` (эталон оператора). Бакет = ИМЯ ФАЙЛА базы out_country_buckets
(напр. "USA.txt", "latam.txt", "Not Stated.txt"). Колонки GSA-CSV: Country, URL, IP.

Здесь только классификация + порядок сводки + помощники для инкрементной базы
(дедуп/подсчёт). Саму выгрузку и формирование сводки делает cmd_report в gsa_checker.py.
"""

from __future__ import annotations

import os
import re

NOT_STATED_FILE = "Not Stated.txt"


# ====== НОРМАЛИЗАЦИЯ (как в split1404) ======
def norm(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip().replace("﻿", "")
    s = s.strip('"').strip("'")
    return re.sub(r"\s+", " ", s)


def norm_key(s: str) -> str:
    s = norm(s).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_url(u: str) -> str:
    u = norm(u)
    if not u:
        return ""
    u = u.lower()
    return u[:-1] if u.endswith("/") else u


def fmt_added(n: int) -> str:
    return f"(+{n})" if n > 0 else "(+)"


def count_nonempty_lines(path) -> int:
    if not os.path.exists(path):
        return 0
    cnt = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.strip():
                cnt += 1
    return cnt


# ====== ВЫХОДНЫЕ ФАЙЛЫ (страна → файл базы) ======
COUNTRY_FILES = {
    "argentina": "Argentina.txt",
    "australia": "australia.txt",
    "brazil": "brazil.txt",
    "chile": "Chile.txt",
    "colombia": "Colombia.txt",
    "ecuador": "Ecuador.txt",
    "france": "France.txt",
    "germany": "Germany.txt",
    "india": "India.txt",
    "indonesia": "Indonesia.txt",
    "italy": "Italy.txt",
    "malaysia": "Malaysia.txt",
    "mexico": "Mexic.txt",
    "pakistan": "pakistan.txt",
    "peru": "Peru.txt",
    "philippines": "Philippines.txt",
    "poland": "Poland.txt",
    "portugal": "Portugal.txt",
    "russia": "Russia.txt",
    "singapore": "Singapore.txt",
    "spain": "Spain.txt",
    "uruguay": "Uruguay.txt",
    "united kingdom": "UK.txt",
    "uk": "UK.txt",
    "great britain": "UK.txt",
    "britain": "UK.txt",
    "united states": "USA.txt",
    "usa": "USA.txt",
    "vietnam": "vietnam.txt",
    "viet nam": "vietnam.txt",

    # спец-правила
    "canada": "USA.txt",
    "new zealand": "australia.txt",
    "niue": "australia.txt",
    "tuvalu": "australia.txt",
}

REGION_FILES = {
    "africa": "africa.txt",
    "other_africa": "Other-Africa.txt",
    "arabic": "arabic.txt",
    "latam": "latam.txt",
    "europe_other": "Europe-Other.txt",
    "asia_other": "Asia-other.txt",
    "china_mix": "china-mix.txt",
    "japanese": "japanese.txt",
    "korea": "Korea.txt",
    "thai": "thai.txt",
    "turkish": "turkish.txt",
    "sng": "sng.txt",
}

# Порядок и подписи сводки — как в split1404 (debug_summary.txt / Telegram)
SUMMARY_ORDER = [
    ("latam.txt",        "🌎 Латинская Америка"),
    ("USA.txt",          "🇺🇸 США"),
    ("Russia.txt",       "🇷🇺 Россия"),
    ("vietnam.txt",      "🇻🇳 Вьетнам"),
    ("Portugal.txt",     "🇵🇹 Португалия"),
    ("turkish.txt",      "🇹🇷 Турция"),
    ("UK.txt",           "🇬🇧 Великобритания"),
    ("japanese.txt",     "🇯🇵 Япония"),
    ("India.txt",        "🇮🇳 Индия"),
    ("Germany.txt",      "🇩🇪 Германия"),
    ("brazil.txt",       "🇧🇷 Бразилия"),
    ("australia.txt",    "🇦🇺 Австралия"),
    ("africa.txt",       "🇿🇦 Южная Африка"),
    ("arabic.txt",       "🌍 Арабские страны"),
    ("France.txt",       "🇫🇷 Франция"),
    ("Spain.txt",        "🇪🇸 Испания"),
    ("Europe-Other.txt", "🇪🇺 Другие страны Европы"),
    ("Poland.txt",       "🇵🇱 Польша"),
    ("Indonesia.txt",    "🇮🇩 Индонезия"),
    ("Italy.txt",        "🇮🇹 Италия"),
    ("pakistan.txt",     "🇵🇰 Пакистан"),
    ("Malaysia.txt",     "🇲🇾 Малайзия"),
    ("Singapore.txt",    "🇸🇬 Сингапур"),
    ("Philippines.txt",  "🇵🇭 Филиппины"),
    ("Other-Africa.txt", "🌍 Другие страны Африки"),
    ("china-mix.txt",    "🇨🇳 Китай"),
    ("Colombia.txt",     "🇨🇴 Колумбия"),
    ("Chile.txt",        "🇨🇱 Чили"),
    ("Peru.txt",         "🇵🇪 Перу"),
    ("Ecuador.txt",      "🇪🇨 Эквадор"),
    ("Uruguay.txt",      "🇺🇾 Уругвай"),
    ("thai.txt",         "🇹🇭 Таиланд"),
    ("Korea.txt",        "🇰🇷 Корея"),
    ("Mexic.txt",        "🇲🇽 Мексика"),
    ("sng.txt",          "🌐 СНГ"),
    ("Argentina.txt",    "🇦🇷 Аргентина"),
    ("Asia-other.txt",   "🌏 Другие страны Азии"),
]


# ====== СПИСКИ СТРАН / АЛИАСЫ (как в split1404) ======
JAPANESE = {"japan"}
KOREA = {
    "south korea", "republic of korea", "korea republic of", "korea",
    "north korea", "democratic people s republic of korea"
}
THAI = {"thailand"}
TURKISH = {"turkey", "türkiye", "turkiye", "northern cyprus"}
CHINA_MIX = {"china", "people s republic of china", "hong kong", "macau", "macao", "taiwan"}

LATAM = {
    "belize", "bolivia", "costa rica", "cuba",
    "dominican republic", "el salvador", "guatemala", "guyana", "haiti",
    "honduras", "jamaica", "mexico", "nicaragua", "panama", "paraguay",
    "suriname", "trinidad and tobago", "venezuela", "puerto rico"
}

SNG = {
    "armenia", "azerbaijan", "belarus", "kazakhstan", "kyrgyzstan", "moldova",
    "tajikistan", "turkmenistan", "uzbekistan", "ukraine", "georgia"
}

ARABIC = {
    "algeria", "bahrain", "comoros", "djibouti", "egypt", "iraq", "jordan", "kuwait",
    "lebanon", "libya", "mauritania", "morocco", "oman", "palestine", "qatar",
    "saudi arabia", "somalia", "sudan", "syria", "tunisia", "united arab emirates",
    "yemen", "western sahara"
}

AFRICA_ALL = {
    "angola", "benin", "botswana", "burkina faso", "burundi", "cameroon",
    "central african republic", "chad", "congo", "democratic republic of the congo",
    "dr congo", "cote d ivoire", "ivory coast", "equatorial guinea", "eritrea",
    "eswatini", "swaziland", "ethiopia", "gabon", "gambia", "ghana", "guinea",
    "guinea bissau", "kenya", "lesotho", "liberia", "madagascar", "malawi", "mali",
    "mozambique", "namibia", "niger", "nigeria", "rwanda", "senegal", "sierra leone",
    "south africa", "south sudan", "tanzania", "togo", "uganda", "zambia", "zimbabwe",
    "cape verde", "cabo verde"
}

OTHER_AFRICA = {
    "seychelles", "mauritius", "reunion", "réunion", "mayotte",
    "saint helena", "sao tome and principe", "são tomé and príncipe"
}

EUROPE = {
    "albania", "andorra", "austria", "belgium", "bosnia and herzegovina", "bulgaria",
    "croatia", "cyprus", "czechia", "czech republic", "denmark", "estonia", "finland",
    "greece", "hungary", "iceland", "ireland", "latvia", "liechtenstein", "lithuania",
    "luxembourg", "malta", "monaco", "montenegro", "netherlands", "north macedonia",
    "norway", "romania", "san marino", "serbia", "slovakia", "slovenia", "sweden",
    "switzerland", "vatican", "holy see", "bouvet island"
}

ASIA_OTHER = {
    "afghanistan", "bangladesh", "bhutan", "brunei", "cambodia", "iran", "israel",
    "maldives", "mongolia", "myanmar", "nepal", "sri lanka", "timor leste",
    "laos", "british indian ocean territory"
}

ALIASES = {
    "u k": "united kingdom",
    "u.k.": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "u.s.": "united states",
    "u.s.a.": "united states",
    "russian federation": "russia",
    "côte d’ivoire": "cote d ivoire",
    "côte d'ivoire": "cote d ivoire",
    "viet-nam": "viet nam",
    "türkiye": "turkiye",
}

NOT_STATED_TOKENS = {"", "n/a", "na", "none", "null", "unknown", "not stated", "not_stated", "-"}

REGION_RULES = [
    (CHINA_MIX, REGION_FILES["china_mix"]),
    (JAPANESE, REGION_FILES["japanese"]),
    (KOREA, REGION_FILES["korea"]),
    (THAI, REGION_FILES["thai"]),
    (TURKISH, REGION_FILES["turkish"]),
    (ARABIC, REGION_FILES["arabic"]),
    (SNG, REGION_FILES["sng"]),
    (LATAM, REGION_FILES["latam"]),
    (OTHER_AFRICA, REGION_FILES["other_africa"]),
    (AFRICA_ALL, REGION_FILES["africa"]),
    (EUROPE, REGION_FILES["europe_other"]),
    (ASIA_OTHER, REGION_FILES["asia_other"]),
]


def resolve_country(raw_country: str) -> str:
    key = norm_key(raw_country)
    return ALIASES.get(key, key)


def bucket_for_country(country_key: str) -> str:
    if country_key in NOT_STATED_TOKENS:
        return NOT_STATED_FILE
    if country_key in COUNTRY_FILES:
        return COUNTRY_FILES[country_key]
    for countries, target_file in REGION_RULES:
        if country_key in countries:
            return target_file
    return NOT_STATED_FILE


def all_bucket_files() -> set:
    """Все файлы базы, которые должны существовать."""
    return set(COUNTRY_FILES.values()) | set(REGION_FILES.values()) | {NOT_STATED_FILE}


def read_membership(out_dir):
    """Читает базу out_country_buckets: множество URL в каждом файле + общее множество
    (для дедупа per-file и global, как в split1404)."""
    from collections import defaultdict
    per_file = defaultdict(set)
    global_set = set()
    if not os.path.isdir(out_dir):
        return per_file, global_set
    for name in os.listdir(out_dir):
        if not name.lower().endswith(".txt"):
            continue
        path = os.path.join(out_dir, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    u = norm_url(line)
                    if u:
                        per_file[name].add(u)
                        global_set.add(u)
        except FileNotFoundError:
            continue
    return per_file, global_set
