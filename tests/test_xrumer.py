"""Тесты lib/xrumer.py — чистая логика без сети и SMB.

Проверяет то, что должно держать оборону независимо от вендора: whitelist команд,
разбор `ls`, безопасные имена, шаблон→проект→разбор (round-trip), выжимку состояния,
чтение ключа. Живой канал вендора/SMB тут НЕ дёргается.
"""


import pytest

from lib import xrumer as xr


# ───────────────────────── whitelist команд ─────────────────────────

def test_validate_ok_no_params():
    assert xr.validate_command("run.stop", None) == ("run.stop", {})
    assert xr.validate_command("run.continue", {}) == ("run.continue", {})


def test_validate_threads_range():
    assert xr.validate_command("threads.set", {"value": 100}) == ("threads.set", {"value": 100})
    with pytest.raises(xr.BadCommand):
        xr.validate_command("threads.set", {"value": 0})
    with pytest.raises(xr.BadCommand):
        xr.validate_command("threads.set", {"value": xr.THREADS_MAX + 1})


def test_validate_threads_bool_rejected():
    # bool — подкласс int; True не должен пролезать как число потоков
    with pytest.raises(xr.BadCommand):
        xr.validate_command("threads.set", {"value": True})


def test_validate_unknown_command():
    with pytest.raises(xr.BadCommand):
        xr.validate_command("system.exec", {"cmd": "rm -rf"})


def test_validate_project_set_field_whitelist():
    assert xr.validate_command("project.set", {"field": "text", "value": "x"})[0] == "project.set"
    with pytest.raises(xr.BadCommand):
        xr.validate_command("project.set", {"field": "EmailPassword", "value": "x"})


def test_validate_extra_and_missing_params():
    with pytest.raises(xr.BadCommand):
        xr.validate_command("threads.set", {"value": 10, "extra": 1})
    with pytest.raises(xr.BadCommand):
        xr.validate_command("project.set", {"field": "text"})


def test_validate_llm_set_flat_scalars():
    m, p = xr.validate_command("llm.set", {"provider": "anthropic", "temp_min": 6, "tc_solve": True})
    assert m == "llm.set" and p["provider"] == "anthropic"
    with pytest.raises(xr.BadCommand):
        xr.validate_command("llm.set", {"nested": {"a": 1}})


# ───────────────────────── safe_name ─────────────────────────

@pytest.mark.parametrize("bad", ["../x", "a\\b", 'a"b', "a/b", "", "x" * 121, None, 5])
def test_safe_name_rejects(bad):
    with pytest.raises(ValueError):
        xr.safe_name(bad)


@pytest.mark.parametrize("ok", ["Template", "forum_07", "RU-catchers #2", "Проект (1)"])
def test_safe_name_accepts(ok):
    assert xr.safe_name(ok) == ok


# ───────────────────────── разбор ls ─────────────────────────

def test_parse_ls():
    # Реальный вывод smbclient: каждая строка начинается с 2 пробелов (их регекс и ждёт).
    sample = "\n".join([
        "  .                                   D        0  Wed Sep 16 11:16:04 2026",
        "  ..                                  D        0  Wed Sep 16 11:16:04 2026",
        "  Template.xml                       An     1534  Sun Feb  4 19:23:16 2024",
        "  Posting.2026.08.txt                An 13046500  Wed Aug 19 18:27:59 2026",
        "  Logs                                D        0  Wed Sep 16 11:16:04 2026",
    ])
    rows = xr.parse_ls(sample)
    names = {r["name"]: r for r in rows}
    assert set(names) == {"Template.xml", "Posting.2026.08.txt", "Logs"}
    assert names["Template.xml"]["size"] == 1534 and not names["Template.xml"]["is_dir"]
    assert names["Logs"]["is_dir"]
    assert names["Posting.2026.08.txt"]["size"] == 13046500


# ───────────────────────── проект: шаблон → правка → разбор ─────────────────────────

TEMPLATE = b"""<?xml version="1.0" encoding="UTF-8"?>
<XRumerProject>
  <PrimarySection>
    <ProjectName>Template</ProjectName>
    <NickName>old</NickName>
    <EmailAddress>old@example.com</EmailAddress>
    <EmailLogin>old@example.com</EmailLogin>
    <Homepage></Homepage>
    <City>Beijing</City>
    <Country>China</Country>
    <Occupation>Health</Occupation>
    <Interests>club</Interests>
    <Password>p</Password>
    <RealName>r</RealName>
    <Signature></Signature>
  </PrimarySection>
  <SecondarySection>
    <Subject1>old subj</Subject1>
    <Subject2>old subj</Subject2>
    <PostText>old text</PostText>
    <Prior>flood</Prior>
  </SecondarySection>
</XRumerProject>
"""


def test_build_and_parse_project_roundtrip():
    xml = xr.build_project_xml(TEMPLATE, "forum_07",
                              {"nick": "user", "email": "u@site.com",
                               "subject1": "Hi {a|b}", "text": "Body {x|y}"})
    prj = xr.parse_project_xml(xml)
    assert prj["name"] == "forum_07"
    assert prj["nick"] == "user"
    assert prj["email"] == "u@site.com"
    assert prj["subject1"] == "Hi {a|b}"
    assert prj["text"] == "Body {x|y}"
    # незаданные поля берутся из шаблона
    assert prj["country"] == "China"


def test_build_project_email_syncs_login():
    xml = xr.build_project_xml(TEMPLATE, "p", {"email": "new@site.com"})
    assert b"<EmailLogin>new@site.com</EmailLogin>" in xml


def test_build_project_rejects_unknown_field():
    with pytest.raises(ValueError):
        xr.build_project_xml(TEMPLATE, "p", {"password": "hack"})


def test_build_project_rejects_nonstring_value():
    with pytest.raises(ValueError):
        xr.build_project_xml(TEMPLATE, "p", {"nick": 123})


# ───────────────────────── выжимка состояния ─────────────────────────

def test_summarize_state():
    st = {"_online": True, "_age": 4, "_sid": "S",
          "state": {"ver": "23.0.8", "project": "forum_07", "base": "b.txt",
                    "run": {"job": "posting", "active_threads": 98, "max_threads": 100,
                            "pos": 42, "total": 700, "speed": 12},
                    "reports": {"success": 1240, "profiles": 186},
                    "proxy": {"use": True, "count": 84},
                    "llm": {"provider": "anthropic", "model": "claude", "key_set": True,
                            "tokens_used": 5}}}
    s = xr.summarize_state(st)
    assert s["online"] and s["project"] == "forum_07" and s["job"] == "posting"
    assert s["success"] == 1240 and s["proxy"] == 84
    assert s["llm"]["provider"] == "anthropic" and s["llm"]["key_set"] is True
    assert "providers" not in s["llm"]          # тяжёлый список не тащим в выжимку


def test_summarize_state_empty():
    s = xr.summarize_state({})
    assert s["online"] is False and s["project"] is None


# ───────────────────────── ключ вендора ─────────────────────────

def test_read_key_from_file(tmp_path):
    f = tmp_path / "k.key"
    f.write_text("MCP key: xmcp_ABCDEFGH12345\n", encoding="utf-8")
    assert xr.read_key({"key_file": str(f)}, tmp_path) == "xmcp_ABCDEFGH12345"


def test_read_key_inline():
    assert xr.read_key({"key": "  xmcp_ZZZ99999 "}, __import__("pathlib").Path(".")) == "xmcp_ZZZ99999"


def test_read_key_missing(tmp_path):
    with pytest.raises(xr.VendorError):
        xr.read_key({"key": "not-a-key"}, tmp_path)


def test_mask_key():
    assert xr.mask_key("xmcp_ABCDEFGH1234") == "xmcp_ABC…1234"
    assert xr.mask_key("short") == "***"
