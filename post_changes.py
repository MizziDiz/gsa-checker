#!/usr/bin/env python3
"""Отправить записи мониторинга на сайт. Массив JSON — на stdin.

Живёт на сервере, потому что здесь лежит мастер-пароль. С рабочей машины
уходит только содержимое записей; пароль не пересекает границу машины и
не печатается ни при успехе, ни при ошибке.
"""
import io, json, sys
import requests

SITE = "https://region-report.pages.dev"
PW_FILE = "/root/.cloudflare/region-report-master.txt"


def main() -> int:
    try:
        items = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception as exc:
        print(f"ОТКАЗ: на вход подан не JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(items, list) or not items:
        print("ОТКАЗ: ожидался непустой массив записей", file=sys.stderr)
        return 2

    try:
        pw = io.open(PW_FILE, encoding="utf-8").read().strip()
    except OSError as exc:
        print(f"ОТКАЗ: мастер-пароль не прочитан ({exc.strerror})", file=sys.stderr)
        return 2

    s = requests.Session()
    r = s.post(f"{SITE}/login", json={"password": pw}, timeout=30)
    pw = None
    if r.status_code != 200:
        print(f"ОТКАЗ: вход не выполнен, код {r.status_code}", file=sys.stderr)
        return 3

    r = s.post(f"{SITE}/api/changes", json=items, timeout=60)
    if r.status_code != 200:
        # Текст ошибки печатаем: молчаливый ненулевой код не говорит, что чинить.
        print(f"ОТКАЗ: запись не принята, код {r.status_code}: {r.text[:300]}",
              file=sys.stderr)
        return 4

    res = r.json()
    print(f"принято: добавлено {res.get('added')}, "
          f"повторов {res.get('duplicates')}, всего в журнале {res.get('total')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
