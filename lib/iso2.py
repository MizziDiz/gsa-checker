#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lib/iso2.py — ISO2 (код страны из .success, поле[-2] от GSA) → английское название.
Название подаётся в buckets.resolve_country/bucket_for_country, поэтому имена совпадают с
тем, что ждёт split1404 (region-сеты, COUNTRY_FILES). Плюс ccTLD-код `uk` = United Kingdom."""

from __future__ import annotations

ISO2_TO_NAME = {
    # COUNTRY_FILES / прямые
    "ar": "Argentina", "au": "Australia", "br": "Brazil", "cl": "Chile",
    "co": "Colombia", "ec": "Ecuador", "fr": "France", "de": "Germany",
    "in": "India", "id": "Indonesia", "it": "Italy", "my": "Malaysia",
    "mx": "Mexico", "pk": "Pakistan", "pe": "Peru", "ph": "Philippines",
    "pl": "Poland", "pt": "Portugal", "ru": "Russia", "sg": "Singapore",
    "es": "Spain", "uy": "Uruguay", "gb": "United Kingdom", "uk": "United Kingdom",
    "us": "United States", "vn": "Viet Nam", "ca": "Canada", "nz": "New Zealand",
    "nu": "Niue", "tv": "Tuvalu",
    # CHINA_MIX / JAPANESE / KOREA / THAI / TURKISH
    "cn": "China", "hk": "Hong Kong", "mo": "Macau", "tw": "Taiwan",
    "jp": "Japan", "kr": "South Korea", "kp": "North Korea",
    "th": "Thailand", "tr": "Turkey",
    # LATAM
    "bz": "Belize", "bo": "Bolivia", "cr": "Costa Rica", "cu": "Cuba",
    "do": "Dominican Republic", "sv": "El Salvador", "gt": "Guatemala",
    "gy": "Guyana", "ht": "Haiti", "hn": "Honduras", "jm": "Jamaica",
    "ni": "Nicaragua", "pa": "Panama", "py": "Paraguay", "sr": "Suriname",
    "tt": "Trinidad and Tobago", "ve": "Venezuela", "pr": "Puerto Rico",
    # SNG
    "am": "Armenia", "az": "Azerbaijan", "by": "Belarus", "kz": "Kazakhstan",
    "kg": "Kyrgyzstan", "md": "Moldova", "tj": "Tajikistan", "tm": "Turkmenistan",
    "uz": "Uzbekistan", "ua": "Ukraine", "ge": "Georgia",
    # ARABIC
    "dz": "Algeria", "bh": "Bahrain", "km": "Comoros", "dj": "Djibouti",
    "eg": "Egypt", "iq": "Iraq", "jo": "Jordan", "kw": "Kuwait", "lb": "Lebanon",
    "ly": "Libya", "mr": "Mauritania", "ma": "Morocco", "om": "Oman",
    "ps": "Palestine", "qa": "Qatar", "sa": "Saudi Arabia", "so": "Somalia",
    "sd": "Sudan", "sy": "Syria", "tn": "Tunisia", "ae": "United Arab Emirates",
    "ye": "Yemen", "eh": "Western Sahara",
    # AFRICA_ALL
    "ao": "Angola", "bj": "Benin", "bw": "Botswana", "bf": "Burkina Faso",
    "bi": "Burundi", "cm": "Cameroon", "cf": "Central African Republic",
    "td": "Chad", "cg": "Congo", "cd": "Democratic Republic of the Congo",
    "ci": "Cote d Ivoire", "gq": "Equatorial Guinea", "er": "Eritrea",
    "sz": "Eswatini", "et": "Ethiopia", "ga": "Gabon", "gm": "Gambia",
    "gh": "Ghana", "gn": "Guinea", "gw": "Guinea Bissau", "ke": "Kenya",
    "ls": "Lesotho", "lr": "Liberia", "mg": "Madagascar", "mw": "Malawi",
    "ml": "Mali", "mz": "Mozambique", "na": "Namibia", "ne": "Niger",
    "ng": "Nigeria", "rw": "Rwanda", "sn": "Senegal", "sl": "Sierra Leone",
    "za": "South Africa", "ss": "South Sudan", "tz": "Tanzania", "tg": "Togo",
    "ug": "Uganda", "zm": "Zambia", "zw": "Zimbabwe", "cv": "Cape Verde",
    # OTHER_AFRICA
    "sc": "Seychelles", "mu": "Mauritius", "re": "Reunion", "yt": "Mayotte",
    "sh": "Saint Helena", "st": "Sao Tome and Principe",
    # EUROPE (→ Europe-Other)
    "al": "Albania", "ad": "Andorra", "at": "Austria", "be": "Belgium",
    "ba": "Bosnia and Herzegovina", "bg": "Bulgaria", "hr": "Croatia",
    "cy": "Cyprus", "cz": "Czechia", "dk": "Denmark", "ee": "Estonia",
    "fi": "Finland", "gr": "Greece", "hu": "Hungary", "is": "Iceland",
    "ie": "Ireland", "lv": "Latvia", "li": "Liechtenstein", "lt": "Lithuania",
    "lu": "Luxembourg", "mt": "Malta", "mc": "Monaco", "me": "Montenegro",
    "nl": "Netherlands", "mk": "North Macedonia", "no": "Norway", "ro": "Romania",
    "sm": "San Marino", "rs": "Serbia", "sk": "Slovakia", "si": "Slovenia",
    "se": "Sweden", "ch": "Switzerland", "va": "Holy See",
    # ASIA_OTHER
    "af": "Afghanistan", "bd": "Bangladesh", "bt": "Bhutan", "bn": "Brunei",
    "kh": "Cambodia", "ir": "Iran", "il": "Israel", "mv": "Maldives",
    "mn": "Mongolia", "mm": "Myanmar", "np": "Nepal", "lk": "Sri Lanka",
    "tl": "Timor Leste", "la": "Laos", "io": "British Indian Ocean Territory",
    # мелкие территории — известны, но не входят в бакеты split1404 → Not Stated
    "ai": "Anguilla", "to": "Tonga", "vg": "British Virgin Islands",
}


def name_for(code: str) -> str:
    """ISO2 (регистр не важен) → английское название, '' если не знаем/пусто."""
    return ISO2_TO_NAME.get((code or "").strip().lower(), "")
