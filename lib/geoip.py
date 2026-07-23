#!/usr/bin/env python3
"""
lib/geoip.py — страна по IP для доменов без country-зоны (gTLD).

GSA определяет страну по ccTLD; для .com/.net зоны нет. Здесь для таких доменов
резолвим host → IP и смотрим страну в базе MaxMind GeoLite2-Country (.mmdb).

Требует:
  • pip install maxminddb
  • файл базы GeoLite2-Country.mmdb (бесплатно у MaxMind), путь в конфиге geoip_db.
Если библиотеки/базы нет — молча возвращает "" (страна остаётся «gTLD»).

DNS медленный, поэтому host→код страны кэшируется (в память + на диск между прогонами).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

_reader = None
_reader_path = None


def available(db_path: str) -> bool:
    return bool(db_path) and Path(db_path).is_file() and _get_reader(db_path) is not None


def _get_reader(db_path: str):
    global _reader, _reader_path
    if _reader is not None and _reader_path == db_path:
        return _reader
    try:
        import maxminddb
    except ImportError:
        return None
    try:
        _reader = maxminddb.open_database(db_path)
        _reader_path = db_path
        return _reader
    except (OSError, ValueError):
        return None


def load_cache(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_cache(path: Path, cache: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def country_name_by_ip(ip: str, db_path: str, cache: dict) -> str:
    """Английское название страны по ГОТОВОМУ IP (без DNS — IP уже есть в CSV GSA).
    '' — не найдено. Кэшируется по IP."""
    ip = (ip or "").strip()
    if not ip:
        return ""
    if ip in cache:
        return cache[ip]
    reader = _get_reader(db_path)
    if reader is None:
        return ""
    name = ""
    try:
        rec = reader.get(ip)                 # ValueError — если ip не парсится как адрес
        if rec:
            c = rec.get("country") or rec.get("registered_country") or {}
            name = (c.get("names") or {}).get("en", "") or ""
    except (ValueError, KeyError):
        name = ""
    cache[ip] = name
    return name


def country_iso(host: str, db_path: str, cache: dict, timeout: float = 3.0) -> str:
    """ISO-код страны (напр. 'US','PL') для host по GeoIP. '' — не удалось.
    Результат кэшируется в cache (host → код или '')."""
    if host in cache:
        return cache[host]
    reader = _get_reader(db_path)
    if reader is None:
        return ""
    try:
        socket.setdefaulttimeout(timeout)
        ip = socket.gethostbyname(host)      # OSError (gaierror/timeout) — если DNS не резолвит
    except OSError:
        cache[host] = ""
        return ""
    code = ""
    try:
        rec = reader.get(ip)                 # ValueError — если ip не парсится
        if rec:
            code = (rec.get("country") or rec.get("registered_country") or {}).get("iso_code", "") or ""
    except (ValueError, KeyError):
        code = ""
    cache[host] = code
    return code
