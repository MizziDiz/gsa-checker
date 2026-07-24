#!/usr/bin/env python3
"""
lib/emails.py — генерация секции [email accounts] для .prj (порт из fill_gsa_emails).

Формат строки аккаунта (нативный для GSA):
    {i}={email}<FF>{provider.ini}<FF>0<FF><FF><FF>0<FF>1<FF>1
где <FF> — один байт 0xFF. В строках он представлен суррогатом '\\udcff', который
lib/prj.py при сохранении (surrogateescape) пишет ровно как 0xFF — в отличие от
исходного скрипта, где символ ÿ под utf-8 давал два байта 0xC3 0xBF.
"""

from __future__ import annotations

import random

FF = "\udcff"  # один байт 0xFF при кодировании surrogateescape

DEFAULT_PROVIDER = "Generator.email.ini"

DEFAULT_DOMAINS = [
    "mailgg.org", "nguyendanhkietisocial.com", "mmds.shop", "bb28.dev",
    "kakaoemail.kr", "sdkajsn.best", "urbanovapro.com", "fintechistanbul.net",
    "moryne.site", "shopeeboost.com", "mailswing.forum", "sentimentdate.com",
    "vietcap.sbs", "tlcfbmt.online", "histartool.com",
]

FIRST_NAMES = [
    "aaron", "abel", "abigail", "adam", "adrian", "aiden", "albert", "alberto", "alex",
    "alexander", "alexandra", "alice", "alicia", "alison", "amanda", "amber", "amelia",
    "andrea", "andreas", "andrew", "angela", "angelica", "anna", "anthony", "antonio",
    "april", "arthur", "ashley", "audrey", "austin", "barbara", "benjamin", "bernard",
    "bernardo", "bethany", "beverly", "blake", "bradley", "brandon", "brenda", "brian",
    "brittany", "brooke", "bruce", "caleb", "cameron", "carla", "carlos", "caroline",
    "catherine", "cedric", "charles", "charlotte", "chelsea", "chloe", "chris",
    "christian", "christina", "christine", "christopher", "claire", "claudia", "clayton",
    "colin", "connor", "cornelia", "crystal", "daniel", "daniela", "danielle", "darren",
    "david", "deborah", "denise", "dennis", "derek", "diana", "diego", "dominique",
    "donald", "dorothy", "douglas", "dylan", "edgar", "edward", "elaine", "elena",
    "eliana", "elisa", "elizabeth", "ella", "ellen", "emily", "emma", "eric", "erica",
    "erin", "ethan", "eva", "evelyn", "felix", "fernando", "frances", "francisco",
    "gabriel", "gabriela", "garrett", "george", "gerald", "gina", "gloria", "grace",
    "gregory", "hannah", "harold", "heather", "henry", "holly", "ian", "irene",
    "isabel", "isabella", "isaac", "jack", "jacob", "james", "janet", "janice", "jared",
    "jasmine", "jason", "javier", "jean", "jeffrey", "jennifer", "jeremy", "jessica",
    "joanna", "john", "jonathan", "jordan", "jose", "joseph", "joshua", "joyce",
    "juan", "judith", "julia", "julian", "julie", "justin", "karen", "katherine",
    "kathleen", "kayla", "keith", "kelly", "kenneth", "kevin", "kimberly", "kristen",
    "kyle", "laura", "lauren", "leah", "leonard", "leslie", "lillian", "linda", "lisa",
    "logan", "lorenzo", "louis", "lucas", "lucy", "luis", "madeline", "madison",
    "manuel", "marc", "marcus", "margaret", "maria", "marie", "mark", "martha",
    "martin", "mary", "mason", "matthew", "megan", "melanie", "melissa", "mia",
    "michael", "michelle", "miguel", "monica", "natalie", "nathan", "nicholas",
    "nicole", "noah", "oliver", "olivia", "oscar", "pamela", "patricia", "patrick",
    "paul", "paula", "peter", "philip", "rachel", "raquel", "raymond", "rebecca",
    "ricardo", "richard", "robert", "robin", "roger", "ronald", "rose", "russell",
    "ryan", "sabrina", "samantha", "samuel", "sandra", "sara", "sarah", "scott",
    "sean", "sergio", "sharon", "sophia", "stacey", "stephen", "steven", "susan",
    "suzanne", "tania", "tegan", "teresa", "thomas", "timothy", "tracy", "ulrich",
    "vanessa", "victor", "victoria", "vincent", "walter", "wendy", "william", "yolanda",
    "zachary",
]

LAST_NAMES = [
    "abbott", "adams", "alexander", "allen", "anderson", "andrews", "armstrong",
    "arnold", "atkins", "austin", "bailey", "baker", "baldwin", "banks", "barnes",
    "barrett", "bates", "bell", "bennett", "berry", "bishop", "black", "blair",
    "bowman", "boyd", "bradley", "brewer", "brooks", "brown", "bryant", "burke",
    "burns", "butler", "byrd", "cameron", "campbell", "carpenter", "carr", "carroll",
    "carter", "chambers", "chandler", "chavez", "chinn", "christensen", "clark",
    "clayton", "cole", "coleman", "collins", "conner", "cook", "cooper", "cortez",
    "courtois", "cox", "craig", "crawford", "cunningham", "daniels", "davidson",
    "davis", "day", "dean", "diaz", "dixon", "douglas", "doyle", "duncan", "dunn",
    "eddington", "edwards", "elliott", "ellis", "evans", "falconer", "fisher",
    "fleming", "flores", "ford", "foster", "fox", "franklin", "freeman", "garcia",
    "gardner", "garland", "gibson", "gill", "gomez", "gonzales", "goodman", "graham",
    "grant", "gray", "green", "griffin", "gross", "guerrero", "hall", "hamilton",
    "hansen", "hardy", "harper", "harris", "hart", "hawkins", "hayes", "henderson",
    "henry", "hernandez", "hicks", "holland", "hollars", "holmes", "hopkins", "howard",
    "hudson", "hughes", "hunter", "jacobs", "james", "jenkins", "johnson", "jones",
    "jordan", "kennedy", "kim", "king", "knight", "lawrence", "lawson", "lee", "lewis",
    "long", "lopez", "lowe", "martin", "martinez", "mason", "mcdonald", "medina",
    "mendez", "mendoza", "michels", "miller", "millard", "mitchell", "moore", "morgan",
    "morris", "murphy", "murray", "nelson", "newman", "nichols", "oliver", "ortega",
    "owens", "palmer", "parker", "patel", "payne", "pearson", "perkins", "perry",
    "peters", "peterson", "phillips", "pierce", "porter", "powell", "price", "ramirez",
    "reed", "reyes", "reynolds", "rice", "richardson", "rivera", "roberts", "robinson",
    "rodgers", "rodriguez", "rogers", "romero", "ross", "ruiz", "russell", "sanders",
    "santiago", "schmidt", "scott", "shaw", "simmons", "smith", "snyder", "spencer",
    "stanley", "stevens", "stone", "sullivan", "taylor", "teece", "thomas", "thompson",
    "tolbert", "torres", "turner", "valdez", "vargas", "vasquez", "wagner", "walker",
    "wallace", "wallner", "walsh", "ward", "watson", "weaver", "webb", "wells",
    "west", "white", "williams", "willis", "wilson", "wood", "wright", "young",
]


def random_email(domains: list[str], used: set[str]) -> str:
    """Адрес firstname+lastname(+число)@domain, уникальный в рамках used."""
    for _ in range(10000):
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        style = random.randint(1, 5)
        if style == 1:
            local = f"{first}{last}"
        elif style == 2:
            local = f"{first}{last}{random.randint(10, 9999)}"
        elif style == 3:
            local = f"{first}.{last}{random.randint(10, 999)}"
        elif style == 4:
            local = f"{first[0]}{last}{random.randint(10, 9999)}"
        else:
            local = f"{first}{last[0]}{random.randint(100, 9999)}"
        email = f"{local}@{random.choice(domains)}"
        if email not in used:
            used.add(email)
            return email
    raise RuntimeError("Не удалось сгенерировать уникальный email.")


def build_account_lines(count: int, provider: str = DEFAULT_PROVIDER,
                        domains: list[str] | None = None,
                        used: set[str] | None = None) -> list[str]:
    """Строки секции [email accounts] (без заголовка, без переводов строк).
    Разделитель — 0xFF (как '\\udcff')."""
    domains = domains or DEFAULT_DOMAINS
    used = used if used is not None else set()
    lines = []
    for i in range(count):
        email = random_email(domains, used)
        lines.append(f"{i}={email}{FF}{provider}{FF}0{FF}{FF}{FF}0{FF}1{FF}1")
    return lines


# GSA-catch-all аккаунт (кнопка «Catch All»): в email-поле — спин-макрос, GSA сам генерит
# адреса; читает из нашего Mailpit по POP3. Поля 0xFF: [спин-макрос]@домен␦POP3-сервер␦порт␦
# логин␦ПАРОЛЬ-в-GSA-шифре␦0␦1␦1.
# ВАЖНО: сама строка содержит внутренний POP3-IP + логин + (шифрованный) пароль — поэтому в
# коде её НЕТ (репозиторий публичный). Hex строки берётся из gitignored data-конфига:
# ключ `email_catchall_hex` в data/gsa_checker.config.json. Захват из реального .prj
# (кнопка Catch All в GSA + POP3), пароль в GSA-обфускации (фикс. алгоритм, проекты переносимы).


def build_catchall_lines(count: int = 1, account_hex: str | None = None) -> list[str]:
    """Строки [email accounts] для GSA-catch-all: одна фикс. запись (спин-макрос + POP3),
    повторённая count раз. `account_hex` — hex 0xFF-строки аккаунта (из конфига
    `email_catchall_hex`; содержит IP/логин/шифр-пароль, в коде не хранится). Байты
    (0xFF-разделители и шифрованный пароль) пишутся как есть через surrogateescape."""
    if not account_hex:
        raise ValueError("build_catchall_lines: нет account_hex "
                         "(задайте email_catchall_hex в data/gsa_checker.config.json)")
    raw = bytes.fromhex(account_hex.replace(" ", ""))
    value = raw.decode("utf-8", "surrogateescape")
    return [f"{i}={value}" for i in range(max(1, int(count)))]
