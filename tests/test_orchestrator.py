"""Тесты оркестратора: реестр нод, аутентификация, фан-аут к агентам, обработка недоступных.

Поднимает ФЕЙКОВЫЙ агент (минимальный HTTP, требует токен ноды) + оркестратор, реестр
которого указывает на фейк-агента и на заведомо недоступную ноду. Проверяет /health,
/nodes (без токенов нод наружу), агрегированный /status, /run (фан-аут) и деградацию по
недоступной ноде.
"""

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import orchestrator

OTOKEN = "orch-token-abc"
NTOKEN = "node-token-xyz"


class FakeAgent(BaseHTTPRequestHandler):
    """Минимальный агент: требует Bearer NTOKEN, отдаёт заготовки."""
    def log_message(self, *a):
        pass

    def _ok(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _auth(self):
        return self.headers.get("Authorization") == f"Bearer {NTOKEN}"

    def do_GET(self):
        if self.path == "/status":
            self._ok({"node": "fake", "jobs_running": 0} if self._auth()
                     else {"error": "unauthorized"}, 200 if self._auth() else 401)
        elif self.path == "/config":
            self._ok({"autopilot_min_targets": 100} if self._auth()
                     else {"error": "unauthorized"}, 200 if self._auth() else 401)
        elif self.path.startswith("/job/"):
            self._ok({"job_id": self.path.split("/")[-1], "status": "done", "rc": 0})
        else:
            self._ok({"error": "nf"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if not self._auth():
            self._ok({"error": "unauthorized"}, 401)
        elif self.path == "/config":
            self._ok({"ok": True, "applied": {"autopilot_min_targets": 500}}, 200)
        else:
            self._ok({"job_id": "fakejob1", "action": "x"}, 202)


@pytest.fixture()
def stack():
    # фейковый агент
    agent = ThreadingHTTPServer(("127.0.0.1", 0), FakeAgent)
    ap = agent.server_address[1]
    threading.Thread(target=agent.serve_forever, daemon=True).start()
    # оркестратор с реестром: живая нода + заведомо мёртвая
    orchestrator.Handler.cfg = {"nodes": [
        {"name": "live", "url": f"http://127.0.0.1:{ap}", "token": NTOKEN},
        {"name": "dead", "url": "http://127.0.0.1:1", "token": NTOKEN},
    ]}
    orchestrator.Handler.token = OTOKEN
    orch = ThreadingHTTPServer(("127.0.0.1", 0), orchestrator.Handler)
    op = orch.server_address[1]
    threading.Thread(target=orch.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{op}"
    orch.shutdown()
    agent.shutdown()


def _get(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url, obj, token=None):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_registry_parsing():
    reg = orchestrator.node_registry({"nodes": [
        {"name": "a", "url": "http://x/", "token": "t"},
        {"name": "bad-no-url"},          # без url — пропуск
        "garbage",                       # не dict — пропуск
    ]})
    assert list(reg) == ["a"] and reg["a"]["url"] == "http://x"


def test_health_no_auth(stack):
    code, body = _get(stack + "/health")
    assert code == 200 and body["ok"] is True and body["nodes"] == 2


def test_auth_required(stack):
    assert _get(stack + "/nodes")[0] == 401
    assert _get(stack + "/nodes", token="wrong")[0] == 401


def test_nodes_hides_tokens(stack):
    code, body = _get(stack + "/nodes", token=OTOKEN)
    assert code == 200
    assert {n["name"] for n in body} == {"live", "dead"}
    assert all("token" not in n for n in body)   # токены нод наружу не отдаём


def test_status_fanout_with_dead_node(stack):
    code, body = _get(stack + "/status", token=OTOKEN)
    assert code == 200
    assert body["live"]["node"] == "fake"        # живая нода ответила
    assert "error" in body["dead"]               # мёртвая — деградация, не падение


def test_run_dispatch(stack):
    code, body = _post(stack + "/run", {"target": "live", "action": "report-dry"}, token=OTOKEN)
    assert code == 200 and body["live"]["job_id"] == "fakejob1"
    # несуществующая нода
    assert _post(stack + "/run", {"target": "ghost", "action": "x"}, token=OTOKEN)[0] == 404


def test_config_get_proxy(stack):
    code, body = _get(stack + "/config/live", token=OTOKEN)
    assert code == 200 and body["autopilot_min_targets"] == 100
    assert _get(stack + "/config/ghost", token=OTOKEN)[0] == 404


def test_config_set_dispatch(stack):
    code, body = _post(stack + "/config",
                       {"target": "live", "set": {"autopilot_min_targets": 500}}, token=OTOKEN)
    assert code == 200 and body["live"]["ok"] is True
