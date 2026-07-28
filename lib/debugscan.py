# -*- coding: utf-8 -*-
"""Разбор debug-папки GSA SER: полный проход по всем дампам → статистика.

GSA кладёт туда HTML-дамп на каждую проблемную цель (имя `<домен>_<hex>.html`).
Файлов десятки тысяч и сотни МБ, поэтому у каждого читается только «голова»
(HEAD_BYTES) — движка, капчи, титула и признаков в ней достаточно, а размер и
время берутся из stat(). Модуль только считает; печать — в gsa_checker.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from pathlib import Path

HEAD_BYTES = 32_768

# Признак причины неудачи: подстрока в «голове» дампа → человеческая метка.
# ВАЖНО: сюда идут только подстроки, которые сами по себе означают отказ. Слова
# «login»/«register» в этот список НЕ входят: на форуме и в WordPress они есть в
# шапке любой страницы, включая успешные, — их разбирает login_wall() отдельно.
SIGNS: tuple[tuple[str, str], ...] = (
    ("captcha", "капча"),
    ("cloudflare", "Cloudflare/WAF"),
    ("access denied", "доступ запрещён"),
    ("forbidden", "доступ запрещён"),
    ("403 ", "доступ запрещён"),
    ("404 ", "страница не найдена"),
    ("not found", "страница не найдена"),
    ("blocked", "блокировка"),
    ("banned", "блокировка"),
    ("spam", "антиспам"),
    ("moderat", "премодерация"),
    ("<error>", "ошибка приложения"),
    ("database error", "ошибка БД сайта"),
    ("maintenance", "техработы"),
    ("suspended", "аккаунт/домен приостановлен"),
)

# Явный отказ «сначала войди/зарегистрируйся» — фраза, а не просто слово в меню.
LOGIN_WALL: tuple[str, ...] = (
    "must be logged in",
    "must be a registered",
    "must register",
    "please log in to",
    "please login to",
    "you need to login",
    "you need to log in",
    "login required",
    "registration required",
    "only registered users",
    "members only",
    "sign in to continue",
    "войдите",
    "требуется авторизац",
    "только для зарегистрированных",
    "необходимо зарегистрироваться",
)

# Поле пароля = на странице реально форма входа/регистрации, а не ссылка в шапке.
PASSWORD_FIELDS: tuple[str, ...] = (
    'type="password"', "type='password'", "type=password",
)

# Слово в разметке (шапка/меню) — сам по себе НЕ признак отказа.
LOGIN_WORDS: tuple[str, ...] = ("login", "log in", "sign in", "register")

# Что сайт сказал про НАШ адрес почты. Здесь только фразы-реакции: просто слово
# «email» есть в любой форме и ничего не значит.
# ВНИМАНИЕ: ошибки самого GSA (POP3 не отвечает, письмо не пришло) сюда попасть
# НЕ МОГУТ — в debug лежат только HTTP-ответы сайтов.
MAIL_SIGNS: tuple[tuple[str, str], ...] = (
    ("invalid email", "адрес отклонён как некорректный"),
    ("not a valid email", "адрес отклонён как некорректный"),
    ("enter a valid email", "адрес отклонён как некорректный"),
    ("valid e-mail address", "адрес отклонён как некорректный"),
    ("email is not valid", "адрес отклонён как некорректный"),
    ("некорректный e-mail", "адрес отклонён как некорректный"),
    ("email already", "адрес уже зарегистрирован"),
    ("already registered", "адрес уже зарегистрирован"),
    ("already in use", "адрес уже зарегистрирован"),
    ("already taken", "адрес уже зарегистрирован"),
    ("уже зарегистрирован", "адрес уже зарегистрирован"),
    ("disposable", "домен в чёрном списке (одноразовый)"),
    ("temporary email", "домен в чёрном списке (одноразовый)"),
    ("throwaway", "домен в чёрном списке (одноразовый)"),
    ("email domain", "домен в чёрном списке (одноразовый)"),
    ("domain is not allowed", "домен в чёрном списке (одноразовый)"),
    ("not allowed to register", "домен в чёрном списке (одноразовый)"),
    ("mx record", "домен в чёрном списке (одноразовый)"),
    ("confirmation email", "ждёт подтверждения по почте"),
    ("verification email", "ждёт подтверждения по почте"),
    ("check your email", "ждёт подтверждения по почте"),
    ("activation link", "ждёт подтверждения по почте"),
    ("activation email", "ждёт подтверждения по почте"),
    ("has been sent to your email", "ждёт подтверждения по почте"),
    ("письмо с подтверждением", "ждёт подтверждения по почте"),
    ("проверьте почту", "ждёт подтверждения по почте"),
)

# Движок сайта: по характерным маркерам в разметке.
ENGINES: tuple[tuple[str, str], ...] = (
    ("wp-content", "WordPress"),
    ("wp-includes", "WordPress"),
    ("phpbb", "phpBB"),
    ("xenforo", "XenForo"),
    ("vbulletin", "vBulletin"),
    ("smf_", "SMF"),
    ("mybb", "MyBB"),
    ("discuz", "Discuz"),
    ("joomla", "Joomla"),
    ("drupal", "Drupal"),
    ("mediawiki", "MediaWiki"),
    ("invision", "IPBoard"),
    ("ipsconfig", "IPBoard"),
    ("wildapricot", "WildApricot"),
    ("prestashop", "PrestaShop"),
    ("opencart", "OpenCart"),
    ("moodle", "Moodle"),
    ("bitrix", "1С-Битрикс"),
    ("shopify", "Shopify"),
    ("wixstatic", "Wix"),
    ("squarespace", "Squarespace"),
)

# Провайдер капчи.
CAPTCHAS: tuple[tuple[str, str], ...] = (
    ("g-recaptcha", "reCAPTCHA"),
    ("recaptcha/api", "reCAPTCHA"),
    ("hcaptcha", "hCaptcha"),
    ("turnstile", "Turnstile"),
    ("solvemedia", "SolveMedia"),
    ("funcaptcha", "FunCaptcha"),
    ("securimage", "Securimage"),
    ("kcaptcha", "KCaptcha"),
)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S)
_LANG_RE = re.compile(r"<html[^>]*\blang=[\"']?([a-zA-Z-]{2,8})")


def split_name(name: str) -> str:
    """Имя дампа `<домен>_<hex>.html` → домен. Хвост `_ABC123` отрезаем только
    если он и правда похож на hex-суффикс GSA (иначе домен вида a_b остался бы битым)."""
    stem = name.rsplit(".", 1)[0]
    head, sep, tail = stem.rpartition("_")
    if sep and tail and all(c in "0123456789ABCDEFabcdef" for c in tail):
        return head
    return stem


def tld_of(host: str) -> str:
    parts = host.rsplit(".", 1)
    return parts[1].lower() if len(parts) == 2 else "(нет)"


def login_wall(head: str) -> str:
    """Насколько «вход/регистрация» на странице — реальная преграда, а не слово в меню.

    Три уровня, потому что вхождение слова «login» само по себе не значит ничего:
    оно есть в шапке почти любого форума и WordPress, в том числе на странице,
    где постинг прошёл успешно.
    """
    if any(w in head for w in LOGIN_WALL):
        return "отказ: сначала вход/регистрация"     # явная фраза-отказ
    if any(w in head for w in PASSWORD_FIELDS):
        return "форма входа/регистрации на странице"  # поле пароля
    if any(w in head for w in LOGIN_WORDS):
        return "слово login в разметке (не преграда)"
    return "без упоминания входа"


def scan(directory: Path, head_bytes: int = HEAD_BYTES,
         mail_domain: str | None = None) -> dict:
    """Полный проход по всем файлам папки. Возвращает словарь статистики.

    mail_domain — домен catch-all (напр. graniteloom.com): если он встретился в
    дампе, значит наш адрес реально дошёл до страницы (форма приняла/отвергла его).
    """
    started = time.time()
    files = [p for p in directory.iterdir() if p.is_file()]

    total_bytes = 0
    empty = 0
    read_errors = 0
    sizes: list[int] = []
    times: list[float] = []
    hosts: Counter = Counter()
    tlds: Counter = Counter()
    exts: Counter = Counter()
    signs: Counter = Counter()
    engines: Counter = Counter()
    captchas: Counter = Counter()
    langs: Counter = Counter()
    titles: Counter = Counter()
    by_hour: Counter = Counter()
    walls: Counter = Counter()
    mail_signs: Counter = Counter()
    mail_hosts: Counter = Counter()
    domain_hits = 0
    domain_hosts: Counter = Counter()
    mail_addrs: Counter = Counter()
    mail_signs_with_addr: Counter = Counter()
    macro_raw: Counter = Counter()
    host_signs: dict[str, Counter] = {}
    digests: Counter = Counter()
    scanned = 0

    for p in files:
        try:
            st = p.stat()
        except OSError:
            read_errors += 1
            continue
        host = split_name(p.name)
        hosts[host] += 1
        tlds[tld_of(host)] += 1
        exts[p.suffix.lower() or "(без расширения)"] += 1
        sizes.append(st.st_size)
        times.append(st.st_mtime)
        total_bytes += st.st_size
        by_hour[time.strftime("%Y-%m-%d %H", time.localtime(st.st_mtime))] += 1

        if st.st_size == 0:
            empty += 1
            signs["пусто (нет ответа)"] += 1
            host_signs.setdefault(host, Counter())["пусто (нет ответа)"] += 1
            continue
        try:
            with p.open("rb") as f:
                raw = f.read(head_bytes)
        except OSError:
            read_errors += 1
            continue
        scanned += 1
        digests[hashlib.blake2b(raw[:4096], digest_size=8).hexdigest()] += 1
        text = raw.decode("utf-8", "replace")
        head = text.lower()          # признаки ищем регистронезависимо…

        wall = login_wall(head)
        walls[wall] += 1
        hit = {label for needle, label in SIGNS if needle in head}
        if wall.startswith("отказ"):
            hit.add("отказ: сначала вход/регистрация")
        for label in hit or {"без явного признака"}:
            signs[label] += 1
            host_signs.setdefault(host, Counter())[label] += 1
        for needle, label in ENGINES:
            if needle in head:
                engines[label] += 1
        for needle, label in CAPTCHAS:
            if needle in head:
                captchas[label] += 1

        mail_hit = {label for needle, label in MAIL_SIGNS if needle in head}
        for label in mail_hit:
            mail_signs[label] += 1
        if mail_hit:
            mail_hosts[host] += 1
        # сообщение про почту + НАШ адрес на той же странице = реакция именно на наш
        # адрес; без адреса это, скорее всего, шаблон JS-валидации пустой формы
        if mail_hit and mail_domain and mail_domain in head:
            for label in mail_hit:
                mail_signs_with_addr[label] += 1
        if mail_domain and mail_domain in head:
            domain_hits += 1
            domain_hosts[host] += 1
            # какие адреса реально дошли до страницы: если тут виден кусок
            # спин-макроса (%spinfile…), значит GSA подставил его как есть
            for addr in re.findall(r"[a-z0-9._%+-]{1,64}@" + re.escape(mail_domain), head):
                mail_addrs[addr] += 1
                if "%" in addr:
                    macro_raw[addr] += 1

        m = _LANG_RE.search(head)
        if m:
            langs[m.group(1).lower()] += 1
        m = _TITLE_RE.search(text)   # …а заголовок сохраняем как есть
        if m:
            title = " ".join(m.group(1).split())[:80]
            if title:
                titles[title] += 1

    sizes.sort()
    n = len(sizes)
    repeat = Counter()
    for host, cnt in hosts.items():
        repeat["1 дамп" if cnt == 1 else "2–4 дампа" if cnt < 5 else "5+ дампов"] += 1

    return {
        "dir": str(directory),
        "scanned_at": int(started),
        "elapsed_sec": round(time.time() - started, 1),
        "files": len(files),
        "bodies_read": scanned,
        "read_errors": read_errors,
        "total_mb": round(total_bytes / 1024 / 1024, 1),
        "empty": empty,
        "size_median_kb": round(sizes[n // 2] / 1024, 1) if n else 0,
        "size_max_kb": round(sizes[-1] / 1024, 1) if n else 0,
        "first": int(min(times)) if times else 0,
        "last": int(max(times)) if times else 0,
        "unique_hosts": len(hosts),
        "unique_bodies": len(digests),
        "hosts": hosts.most_common(30),
        "all_hosts": dict(hosts),          # для сверки со списками .success
        "login_wall": walls.most_common(),
        "mail_signs": mail_signs.most_common(),
        "mail_hosts": mail_hosts.most_common(20),
        "mail_domain": mail_domain or "",
        "mail_domain_hits": domain_hits,
        "mail_domain_hosts": domain_hosts.most_common(20),
        "mail_signs_with_addr": mail_signs_with_addr.most_common(),
        "mail_addr_samples": mail_addrs.most_common(25),
        "mail_addr_unique": len(mail_addrs),
        "mail_macro_unexpanded": macro_raw.most_common(10),
        "host_repeat": dict(repeat),
        "tlds": tlds.most_common(20),
        "exts": exts.most_common(10),
        "signs": signs.most_common(),
        "engines": engines.most_common(15),
        "captchas": captchas.most_common(10),
        "langs": langs.most_common(15),
        "titles": titles.most_common(20),
        "by_hour": sorted(by_hour.items()),
        "top_host_signs": {h: dict(c.most_common(3))
                           for h, _ in hosts.most_common(10) if (c := host_signs.get(h))},
    }
