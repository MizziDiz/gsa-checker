"""Тесты генератора статей: формат GSA .articles + спин-заголовки/описания."""

from lib import articles as art


def test_generate_articles_format():
    out = art.generate_articles(["online casino"], ["play now"], "https://x.com/", count=5, seed=1)
    lines = out.split("\n")
    assert len(lines) == 5
    for ln in lines:
        p = ln.split("\x01")
        assert len(p) == 4
        assert p[1] == "%first_paragraph-article%"
        assert len(p[3]) == 8 and all(c in "0123456789ABCDEF" for c in p[3])
    assert 'href="https://x.com/"' in out          # анкор-ссылка в теле
    assert "play now" in out and "online casino" in out


def test_no_link_option():
    out = art.generate_articles(["x"], ["y"], "https://z/", count=2, seed=1, with_link=False)
    assert "href=" not in out


def test_spin_title_desc_keep_spintax():
    assert "kw" in art.spin_title("kw") and "{" in art.spin_title("kw")
    assert "kw" in art.spin_description("kw") and "{" in art.spin_description("kw")


def test_deterministic_seed():
    a = art.generate_articles(["x"], ["y"], "https://z/", 3, seed=42)
    b = art.generate_articles(["x"], ["y"], "https://z/", 3, seed=42)
    assert a == b
