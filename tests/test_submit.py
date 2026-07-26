"""Тесты клиента submit_tasks: парсинг json/csv/txt + нормализация (без сети)."""

import pytest

import submit_tasks as S


def test_parse_json_array_single_jsonl():
    arr = S.parse_json('[{"url":"https://a/","country":"Poland","anchors":["a","b"],"links_per_day":10}]')
    assert len(arr) == 1 and arr[0]["anchors"] == ["a", "b"] and arr[0]["links_per_day"] == 10
    assert len(S.parse_json('{"url":"https://a/","country":"X","anchors":["a"],"links_per_day":1}')) == 1
    jl = ('{"url":"https://a/","country":"X","anchors":["a"],"links_per_day":1}\n'
          '{"url":"https://b/","country":"Y","anchors":["c"],"links_per_day":2}')
    assert len(S.parse_json(jl)) == 2


def test_parse_csv_anchors_column():
    t = S.parse_csv("url,country,anchors,links_per_day\nhttps://a/,Poland,a1|a2|a3,5\n")
    assert t[0]["anchors"] == ["a1", "a2", "a3"] and t[0]["links_per_day"] == 5


def test_parse_csv_anchor_columns():
    t = S.parse_csv("url,country,anchor1,anchor2,links_per_day\nhttps://a/,X,foo,bar,3\n")
    assert t[0]["anchors"] == ["foo", "bar"]


def test_parse_txt():
    txt = "# коммент\nhttps://a/\tPoland\tx|y|z\t7\n\nhttps://b/\tFrance\tp\t2\n"
    t = S.parse_txt(txt)
    assert len(t) == 2
    assert t[0]["anchors"] == ["x", "y", "z"] and t[1]["country"] == "France"


def test_parse_txt_bad_line():
    with pytest.raises(ValueError):
        S.parse_txt("https://a/ Poland x 5\n")   # нет TAB-разделителей


def test_local_issues():
    assert not S.local_issues({"url": "https://a/", "country": "X",
                               "anchors": ["a"], "links_per_day": 5})
    bad = S.local_issues({"url": "ftp://a", "country": "", "anchors": [], "links_per_day": 0})
    assert set(bad) == {"url", "country", "anchors", "links_per_day"}
