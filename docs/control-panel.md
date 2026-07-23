# Панель управления (control plane) — подключение

Панель `site/control.html` даёт master-пользователю запускать действия на нодах и видеть
статус. Она **не хранит секретов**: ходит на same-origin прокси `/ctl/*`, а токены и адрес
оркестратора держит серверная сторона (Cloudflare Worker). Цепочка:

```
браузер (control.html)
  → /ctl/*  (Cloudflare Pages Worker, роль master, добавляет orchestrator_token)
      → orchestrator.py на шаре  (через Cloudflare Tunnel)
          → agent.py на каждой ноде
              → gsa_checker.py
```

## 1. Развернуть агенты и оркестратор

- **На каждой ноде** (`agent.py`): в `data/gsa_checker.config.json` задать `agent_token`
  (сильный случайный) и `agent_bind` (LAN/VPN-интерфейс, напр. `0.0.0.0:8787` за файрволом).
  Запустить как службу/в планировщике. Держать **за VPN/файрволом**.
- **На шаре** (`orchestrator.py`): в конфиге задать `orchestrator_token`, `orchestrator_bind`
  (напр. `127.0.0.1:8790`) и реестр `nodes` (`name`/`url`/`token` каждой ноды-агента).
  Запустить как службу.

## 2. Cloudflare Tunnel: шара → CF (без открытия портов наружу)

Оркестратор слушает `127.0.0.1` — наружу его отдаёт Tunnel (исходящее соединение от шары,
никаких проброшенных портов):

```bash
cloudflared tunnel login
cloudflared tunnel create gsa-orchestrator
# ingress: домен -> локальный оркестратор
cat > ~/.cloudflared/config.yml <<'YML'
tunnel: gsa-orchestrator
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: orch.example.com          # свой поддомен
    service: http://127.0.0.1:8790
  - service: http_status:404
YML
cloudflared tunnel route dns gsa-orchestrator orch.example.com
cloudflared tunnel run gsa-orchestrator   # как службу: cloudflared service install
```

> Доступ к `orch.example.com` желательно закрыть **Cloudflare Access** (только твой
> аккаунт), чтобы кроме Worker никто не дёргал оркестратор.

## 3. Роуты `/ctl/*` в Worker (`_worker.js`)

Добавь в свой Pages-Worker проксирование, **под ролью master**. Адрес и токен оркестратора —
из переменных окружения Pages (Settings → Environment variables / Secrets):
`ORCH_URL` (напр. `https://orch.example.com`) и `ORCH_TOKEN` (= `orchestrator_token`).

```js
// в fetch(request, env): ПОСЛЕ твоей проверки роли master для /ctl/*
async function proxyControl(request, env, url) {
  // допускаем только известные пути/методы — без произвольного форварда
  const p = url.pathname.slice("/ctl".length) || "/";
  const ok =
    (request.method === "GET"  && (p === "/status" || p === "/nodes" || p.startsWith("/job/"))) ||
    (request.method === "POST" &&  p === "/run");
  if (!ok) return new Response("not found", { status: 404 });

  const init = {
    method: request.method,
    headers: { "Authorization": "Bearer " + env.ORCH_TOKEN,
               "Content-Type": "application/json" },
  };
  if (request.method === "POST") init.body = await request.text();   // {target, action}
  const resp = await fetch(env.ORCH_URL + p, init);
  return new Response(await resp.text(),
    { status: resp.status, headers: { "Content-Type": "application/json" } });
}

// маршрутизация:
if (url.pathname === "/ctl" || url.pathname.startsWith("/ctl/")) {
  if (role !== "master") return new Response("forbidden", { status: 403 }); // твоя проверка роли
  return proxyControl(request, env, url);
}
```

> Ключевое: браузер бьёт только по `/ctl/*` (same-origin), токен оркестратора живёт в
> `env.ORCH_TOKEN` на стороне Worker и в браузер не попадает. Whitelist действий проверяет
> сам агент, оркестратор их не расширяет.

## 4. Задеплоить панель

`site/control.html` уедет вместе с остальной статикой (`deploy_pages.py` / твой деплой).
Открыть: `https://<pages-домен>/control.html` (под ролью master).

## Безопасность (сводно)
- Токены — только в gitignored-конфигах нод/шары и в **секретах Pages** (`ORCH_TOKEN`), не в
  репо и не в браузере.
- Оркестратор наружу — только через Tunnel (+ желательно Cloudflare Access), bind `127.0.0.1`.
- Агенты — за VPN/файрволом, сильный `agent_token`, bind на LAN/VPN.
- Мутирующие действия (`report` боевой, `autopilot --apply`) в панели просят подтверждение;
  всё пишется в аудит (`data/agent_audit.jsonl`, `data/orchestrator_audit.jsonl`).
- Whitelist действий — только на агенте; оркестратор/Worker не добавляют новых команд.
