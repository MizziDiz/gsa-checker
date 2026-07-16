#!/usr/bin/env python3
"""lib/buckets.py — раскладка стран по бакетам, логика 1:1 из select-v1.py (Split),
чтобы вывод совпадал с out_country_buckets. Колонки CSV: Country, URL, IP."""

from __future__ import annotations
import re
from typing import List

def norm(s: str) -> str:
    if s is None:
        return ""
    s = str(s).strip().replace("\ufeff", "")
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


# ============================================================
# СТРАНЫ / РЕГИОНЫ
# ============================================================

COUNTRY_BUCKETS = {
    "argentina": "argentina",
    "australia": "australia",
    "brazil": "brazil",
    "chile": "chile",
    "colombia": "colombia",
    "ecuador": "ecuador",
    "france": "france",
    "germany": "germany",
    "india": "india",
    "indonesia": "indonesia",
    "italy": "italy",
    "malaysia": "malaysia",
    "mexico": "mexic",
    "pakistan": "pakistan",
    "peru": "peru",
    "philippines": "philippines",
    "poland": "poland",
    "portugal": "portugal",
    "russia": "russia",
    "singapore": "singapore",
    "spain": "spain",
    "uruguay": "uruguay",
    "united kingdom": "uk",
    "uk": "uk",
    "great britain": "uk",
    "britain": "uk",
    "united states": "usa",
    "usa": "usa",
    "vietnam": "vietnam",
    "viet nam": "vietnam",

    # спец-правила как в старом скрипте
    "canada": "usa",
    "new zealand": "australia",
    "niue": "australia",
    "tuvalu": "australia",
}

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
    (CHINA_MIX, "china_mix"),
    (JAPANESE, "japanese"),
    (KOREA, "korea"),
    (THAI, "thai"),
    (TURKISH, "turkish"),
    (ARABIC, "arabic"),
    (SNG, "sng"),
    (LATAM, "latam"),
    (OTHER_AFRICA, "other_africa"),
    (AFRICA_ALL, "africa"),
    (EUROPE, "europe_other"),
    (ASIA_OTHER, "asia_other"),
]


def resolve_country(raw_country: str) -> str:
    key = norm_key(raw_country)
    return ALIASES.get(key, key)


def bucket_for_country(country_key: str) -> str:
    if country_key in NOT_STATED_TOKENS:
        return "not_stated"

    if country_key in COUNTRY_BUCKETS:
        return COUNTRY_BUCKETS[country_key]

    for countries, bucket in REGION_RULES:
        if country_key in countries:
            return bucket

    return "not_stated"
