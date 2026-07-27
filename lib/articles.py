#!/usr/bin/env python3
"""lib/articles.py — генератор GSA .articles + спин-заголовков/описаний для boost-проектов.

Собирает статьи из банков спин-шаблонов (stdlib, без LLM): title + тело из спин-предложений
с вплетённым keyword и анкор-ссылкой на url. Плейсхолдеры [[KEYWORD]]/[[ANCHOR]]/[[URL]]/
[[DOMAIN]] подставляются в Python (не путать со спинтаксом GSA {a|b|c} — тот остаётся GSA).

Формат .articles GSA (проверено по реальному template.articles): по строке на статью, 4 поля
через байт 0x01:  title␁%first_paragraph-article%␁</p>\\n<p>{тело}\\n</p>␁XXXXXXXX
(последнее — 8-символьный HEX-id; \\n — литеральный перевод строки в значении).
"""

from __future__ import annotations

import random
from urllib.parse import urlparse

FF1 = "\x01"

TITLES = [
    "{A Complete|An Essential|A Practical|A Detailed|A Simple} {Guide|Overview|Introduction|Look} to [[KEYWORD]]",
    "{Understanding|Exploring|Getting Started With|Making Sense of} [[KEYWORD]]",
    "{What You Should Know About|Key Facts About|A Closer Look at|The Basics of} [[KEYWORD]]",
    "{Everything|All You Need to Know} About [[KEYWORD]]",
    "{Tips|Advice|Ideas|Notes} on [[KEYWORD]]",
    "{Why|How} [[KEYWORD]] {Matters|Works|Can Help}",
    "[[KEYWORD]]: {A Quick|A Short|A Handy} {Guide|Overview|Primer}",
    "{Choosing|Finding|Picking} the {Right|Best} [[KEYWORD]]",
]

OPENERS = [
    "{When it comes to|Regarding|In terms of|Speaking of} [[KEYWORD]], {many|most|a lot of} {people|users|readers} {look for|search for|want} {reliable|trustworthy|quality|solid} {information|resources|guidance}.",
    "{These days|Nowadays|Recently|Lately}, [[KEYWORD]] {has become|is} {a popular|an important|a common} {topic|subject|area of interest}.",
    "{If you are|Whether you are} {new to|interested in|curious about} [[KEYWORD]], {this|the following} {article|overview|guide} {covers|explains|breaks down} the {basics|essentials|key points}.",
    "{Understanding|Knowing about} [[KEYWORD]] {can|will} {help|allow} {you|readers} {make|reach} {better|smarter|informed} {choices|decisions}.",
]

BODY = [
    "{There are|You will find} {several|many|a number of|various} {options|choices|approaches} {to consider|worth exploring|available}.",
    "{It is|It's} {important|helpful|useful|wise} to {compare|review|weigh} {the details|your options|the features} {before deciding|carefully|in advance}.",
    "{Many|Most|Plenty of} {experts|guides|sources} {recommend|suggest|advise} {starting|beginning} {with the basics|slowly|step by step}.",
    "{Quality|Reliability|Trust|Reputation} {should be|is} {a top|a key|an important} {priority|factor|consideration}.",
    "{Over time|With experience|Gradually}, {you|users} {tend to|often|usually} {learn|discover|figure out} what {works best|suits their needs|fits}.",
    "{Good|Solid|Careful} {planning|preparation|research} {makes|can make} {a real|a big|a noticeable} {difference|impact}.",
    "{Reading|Checking|Reviewing} {reviews|feedback|opinions} from {others|real users} {can be|is often} {valuable|helpful|insightful}.",
    "{Keep in mind|Remember|Note} that {needs|situations|requirements} {vary|differ} from {person to person|case to case}.",
    "{A balanced|A sensible|A thoughtful} {approach|strategy|method} {usually|often|tends to} {pays off|delivers results|works well}.",
    "{Support|Guidance|Help} {is|remains} {available|within reach} for {those who|anyone who} {needs|wants} it.",
]

LINK = [
    "{For more|To learn more|If you want more} {details|information} {about|on} [[KEYWORD]], {visit|check out|see|take a look at} <a href=\"[[URL]]\">[[ANCHOR]]</a>.",
    "{You can|Feel free to} {find|read|discover} {more|further details} {at|on} <a href=\"[[URL]]\">[[ANCHOR]]</a>.",
    "{Learn more|Read more|Find out more} {here|at this resource}: <a href=\"[[URL]]\">[[ANCHOR]]</a>.",
]

CLOSERS = [
    "{In short|To sum up|All in all|Overall}, [[KEYWORD]] {is worth|deserves} {a closer look|attention|some thought}.",
    "{Hopefully|We hope} {this|the above} {helps|is useful|gives you a starting point}.",
    "{Take your time|Do your homework|Explore your options} and {choose|decide} {wisely|with confidence}.",
    "{Thanks for reading|Thank you for reading}{!|.}",
]

DESCRIPTIONS = [
    "{A|Your} {trusted|reliable|handy|useful} {resource|guide|source} {for|about|on} [[KEYWORD]].",
    "{Learn|Discover|Explore} {more|everything} about [[KEYWORD]] {here|with us|today}.",
    "{Quality|Helpful|Practical} {information|tips|guidance} {on|about} [[KEYWORD]].",
    "{Everything|All} {you need|you want} to {know|understand} about [[KEYWORD]].",
]


def _split_keywords(keywords) -> list[str]:
    if isinstance(keywords, list):
        kws = [str(k).strip() for k in keywords if str(k).strip()]
    else:
        kws = [k.strip() for k in str(keywords or "").replace(",", "\n").splitlines() if k.strip()]
    return kws or ["this topic"]


def _subst(text: str, keyword: str, anchor: str = "", url: str = "", domain: str = "") -> str:
    return (text.replace("[[KEYWORD]]", keyword).replace("[[ANCHOR]]", anchor or keyword)
                .replace("[[URL]]", url).replace("[[DOMAIN]]", domain))


def spin_title(keyword: str, rng: random.Random | None = None) -> str:
    """Спин-заголовок (сохраняет {a|b|c} для GSA) с подставленным keyword."""
    rng = rng or random
    return _subst(rng.choice(TITLES), keyword)


def spin_description(keyword: str, rng: random.Random | None = None) -> str:
    """Спин-описание (сохраняет {a|b|c}) с keyword."""
    rng = rng or random
    return _subst(rng.choice(DESCRIPTIONS), keyword)


def generate_articles(keywords, anchors, url: str, count: int = 20,
                      seed: int | None = None, with_link: bool = True) -> str:
    """Возвращает содержимое .articles: count статей в GSA-формате (4 поля через 0x01,
    строки через \\n). keyword/анкор ротируются; тело — спин-предложения + анкор-ссылка."""
    rng = random.Random(seed)
    kws = _split_keywords(keywords)
    ancs = _split_keywords(anchors) if anchors else kws
    domain = (urlparse(url).netloc or "").lstrip("www.")
    lines = []
    for i in range(max(1, int(count))):
        kw, anc = kws[i % len(kws)], ancs[i % len(ancs)]
        title = spin_title(kw, rng)
        mids = rng.sample(BODY, min(rng.randint(3, 5), len(BODY)))
        sents = [_subst(rng.choice(OPENERS), kw)] + [_subst(m, kw) for m in mids]
        if with_link:
            sents.append(_subst(rng.choice(LINK), kw, anc, url, domain))
        sents.append(_subst(rng.choice(CLOSERS), kw))
        # два абзаца: разбиваем предложения примерно пополам (литеральный \n как в .prj)
        half = max(1, len(sents) // 2)
        body = " ".join(sents[:half]) + "\\n</p>\\n<p>\\n" + " ".join(sents[half:])
        body_html = f"</p>\\n<p>{body}\\n</p>"
        hexid = "".join(rng.choice("0123456789ABCDEF") for _ in range(8))
        lines.append(f"{title}{FF1}%first_paragraph-article%{FF1}{body_html}{FF1}{hexid}")
    return "\n".join(lines)
