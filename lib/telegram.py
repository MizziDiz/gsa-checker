#!/usr/bin/env python3
"""
lib/telegram.py — отправка уведомлений gsa-checker в Telegram.

Порт проверенной логики из Aparser-checker: прямая отправка (с учётом
telegram_proxy) или через сервер-релей локальной сети (telegram_relay_url).
Каждое сообщение подписывается именем сервера. Ошибки НЕ пробрасываются наружу —
логируются и возвращают False, чтобы сбой Telegram не ронял прогон.
"""

from __future__ import annotations

import logging
import socket

import requests

REQUEST_TIMEOUT = 25

log = logging.getLogger("gsa_checker")


def _proxies(cfg: dict):
    p = cfg.get("telegram_proxy", "")
    return {"http": p, "https": p} if p else None


def server_label(cfg: dict) -> str:
    return str(cfg.get("server_name") or socket.gethostname())


def send_direct(cfg: dict, text: str) -> None:
    """Прямая отправка (бросает исключение при ошибке; используется и на релее)."""
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
    resp = requests.post(
        url,
        json={"chat_id": cfg["telegram_chat_id"], "text": text,
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=cfg.get("request_timeout", REQUEST_TIMEOUT),
        proxies=_proxies(cfg),
    )
    resp.raise_for_status()


def send_via_relay(cfg: dict, text: str) -> None:
    url = cfg["telegram_relay_url"].rstrip("/") + "/send"
    resp = requests.post(
        url,
        json={"secret": cfg.get("relay_secret", ""), "text": text},
        timeout=cfg.get("request_timeout", REQUEST_TIMEOUT),
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"релей вернул ошибку: {body}")


def send(cfg: dict, text: str) -> bool:
    """Отправка сообщения: напрямую или через релей. Подписывает именем сервера.
    Не бросает исключений — при сбое логирует и возвращает False."""
    if not cfg.get("telegram_bot_token") and not cfg.get("telegram_relay_url"):
        log.warning("Telegram не настроен (нет telegram_bot_token / telegram_relay_url)")
        return False
    text = f"🖥 <b>{server_label(cfg)}</b>\n{text}"
    relay = cfg.get("telegram_relay_url", "")
    try:
        if relay:
            send_via_relay(cfg, text)
        else:
            send_direct(cfg, text)
        return True
    except (requests.exceptions.RequestException, RuntimeError, ValueError) as e:
        where = f"релей {relay}" if relay else "напрямую в Telegram"
        log.warning(f"отправка в Telegram не удалась ({where}): {type(e).__name__}: {e}")
        return False


def test_telegram(cfg: dict) -> int:
    """--test-telegram: шлёт тестовое сообщение и печатает диагностику."""
    relay = cfg.get("telegram_relay_url", "")
    print(f"Режим: {'через релей ' + relay if relay else 'напрямую в Telegram'}")
    if not relay and not cfg.get("telegram_bot_token"):
        print("❌ Нет telegram_bot_token и не задан telegram_relay_url.")
        return 1
    proxy = cfg.get("telegram_proxy", "")
    if proxy and not relay:
        print(f"Прокси: {proxy}")
    try:
        if relay:
            send_via_relay(cfg, f"🧪 gsa-checker тест ({server_label(cfg)})")
        else:
            send_direct(cfg, f"🖥 <b>{server_label(cfg)}</b>\n🧪 gsa-checker тест")
    except Exception as e:
        print(f"❌ Не отправлено: {type(e).__name__}: {e}")
        if not relay and not proxy:
            print("Если api.telegram.org недоступен напрямую — задайте telegram_proxy, "
                  "напр. \"socks5://host:1080\", или используйте telegram_relay_url.")
        return 1
    print("OK — тестовое сообщение отправлено, проверьте чат.")
    return 0
