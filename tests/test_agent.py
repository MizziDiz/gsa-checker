"""Тесты node-агента: whitelist действий, аутентификация, HTTP-эндпоинты.

Поднимает агент на 127.0.0.1:<эфемерный порт> и проверяет:
  • /health без токена; /actions требует токен; неверный токен → 401;
  • неразрешённое действие → 403; разрешённое → 202 + job_id; /job/<id> отдаёт статус.
Действие для проверки запуска — безопасный `gsa_checker.py --help` (rc 0, без конфига).
"""

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import agent

TOKEN = "test-token-1234567890"


def test_allowed_actions_defaults_and_extras():
    base = agent.allowed_actions({})
    assert "autopilot" in base and "report" in base
    # мутирующих .prj команд (respin/settings) в дефолте нет
    assert "respin" not in base and "settings" not in base
    # расширение из конфига добавляется; кривое (не список строк) отбрасывается
    ext = agent.allowed_actions({"agent_actions": {"x": ["--stats"], "bad": "oops"}})
    assert ext["x"] == ["--stats"]
    assert "bad" not in ext


@pytest.fixture()
def server():
    agent.Handler.cfg = {"server_name": "test-node",
                         "agent_actions": {"selftest": ["--help"]}}
    agent.Handler.token = TOKEN
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url, obj, token=None):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health_no_auth(server):
    code, body = _get(server + "/health")
    assert code == 200 and body["ok"] is True and body["node"] == "test-node"


def test_actions_requires_token(server):
    assert _get(server + "/actions")[0] == 401
    assert _get(server + "/actions", token="wrong")[0] == 401
    code, body = _get(server + "/actions", token=TOKEN)
    assert code == 200 and "autopilot" in body["actions"]


def test_disallowed_action_forbidden(server):
    code, body = _post(server + "/run", {"action": "rm-rf"}, token=TOKEN)
    assert code == 403 and "not allowed" in body["error"]


def test_run_and_job(server):
    code, body = _post(server + "/run", {"action": "selftest"}, token=TOKEN)
    assert code == 202 and "job_id" in body
    job_id = body["job_id"]
    # дождёмся завершения (gsa_checker --help — быстро)
    for _ in range(20):
        jc, jb = _get(f"{server}/job/{job_id}", token=TOKEN)
        assert jc == 200 and jb["job_id"] == job_id
        if jb["status"] == "done":
            assert jb["rc"] == 0        # --help завершается кодом 0
            break
        time.sleep(0.2)
    else:
        pytest.fail("job не завершился")


def test_system_actions_git_pull():
    # по умолчанию — папка агента; фиксированный argv, без shell
    assert agent.system_actions({})["git-pull"] == [
        ["git", "-C", str(agent.ROOT), "pull", "--ff-only"]]
    a = agent.system_actions({"agent_git_dirs": ["C:\\A-GSA", "C:\\Aparser"]})
    assert a["git-pull"] == [["git", "-C", "C:\\A-GSA", "pull", "--ff-only"],
                             ["git", "-C", "C:\\Aparser", "pull", "--ff-only"]]


def test_git_pull_in_actions_list(server):
    _, body = _get(server + "/actions", token=TOKEN)
    assert "git-pull" in body["actions"]


def test_validate_autopilot():
    clean, errors = agent._validate_autopilot(
        {"autopilot_min_targets": 30000, "autopilot_include_names": ["Split", "S2"]})
    assert not errors and clean["autopilot_min_targets"] == 30000
    assert agent._validate_autopilot({"agent_token": "x"})[1]          # не-whitelist ключ
    assert agent._validate_autopilot({"autopilot_min_targets": "many"})[1]  # неверный тип
    assert agent._validate_autopilot({"autopilot_include_names": "Split"})[1]  # не список


def test_config_get_and_set(server, tmp_path, monkeypatch):
    cfgfile = tmp_path / "gsa_checker.config.json"
    cfgfile.write_text(json.dumps({"agent_token": TOKEN, "autopilot_min_targets": 100}),
                       encoding="utf-8")
    monkeypatch.setattr(agent, "CONFIG_PATH", cfgfile)
    # GET /config — только whitelist, секретов нет
    code, body = _get(server + "/config", token=TOKEN)
    assert code == 200 and body["autopilot_min_targets"] == 100 and "agent_token" not in body
    # POST /config — валидное обновление, остальной конфиг (секрет) сохранён
    code, body = _post(server + "/config",
                       {"set": {"autopilot_min_targets": 500, "autopilot_include_names": ["Split"]}},
                       token=TOKEN)
    assert code == 200 and body["ok"] is True
    saved = json.loads(cfgfile.read_text(encoding="utf-8"))
    assert saved["autopilot_min_targets"] == 500 and saved["agent_token"] == TOKEN
    # POST /config — неразрешённый ключ отклоняется, файл не тронут
    code, _ = _post(server + "/config", {"set": {"gsa_exe_path": "C:\\evil"}}, token=TOKEN)
    assert code == 400
    assert "gsa_exe_path" not in json.loads(cfgfile.read_text(encoding="utf-8"))
