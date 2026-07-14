#!/usr/bin/env python3
"""
lib/ui.py — UI-автоматизация GSA SER через pywinauto (только Windows).

Назначение: после файловой дозаливки целей (--autopilot) «толкнуть» GSA, чтобы он
подхватил новые цели — обновить интерфейс / убедиться, что проекты активны.

pywinauto импортируется ЛЕНИВО внутри функций: на Linux (машина разработки) модуль
недоступен, но остальные команды gsa-checker от этого не страдают.

Порядок ввода в строй:
  1. На Windows-сервере с ЗАПУЩЕННЫМ GSA:  pip install pywinauto
  2. python gsa_checker.py --ui-check   → выгрузит структуру окна в data/ui_controls.txt
  3. по дампу настраиваем ui_* ключи (грид проектов, пункт меню), затем --ui-refresh.

Все селекторы вынесены в конфиг (ui_window_title, ui_backend, ui_context_item,
ui_refresh_keys, ui_select_all) — под конкретный билд GSA, без правки кода.
"""

from __future__ import annotations

import contextlib
import re
import sys
from pathlib import Path


def _require_pywinauto():
    try:
        import pywinauto  # noqa: F401
    except ImportError:
        sys.exit("pywinauto не установлен. На Windows-сервере: pip install pywinauto\n"
                 "(на Linux эта команда не работает — GSA UI есть только на Windows).")


def _connect(cfg):
    """Подключается к запущенному GSA, возвращает (app, main_window)."""
    from pywinauto import Application
    backend = cfg.get("ui_backend", "uia")
    title = cfg.get("ui_window_title", "GSA Search Engine Ranker")
    pattern = f".*{re.escape(title)}.*"
    timeout = int(cfg.get("ui_connect_timeout", 15) or 15)
    app = Application(backend=backend).connect(title_re=pattern, timeout=timeout)
    win = app.window(title_re=pattern)
    return app, win


def ui_check(cfg, data_dir: Path) -> None:
    """Диагностика: перечисляет окна GSA и выгружает дерево контролов главного окна
    в data/ui_controls.txt (по нему настраиваем селекторы рефреша)."""
    _require_pywinauto()
    from pywinauto import Desktop
    backend = cfg.get("ui_backend", "uia")
    title = cfg.get("ui_window_title", "GSA Search Engine Ranker")

    print(f"backend: {backend}  |  ищем окно ~ «{title}»")
    matches = []
    for w in Desktop(backend=backend).windows():
        try:
            t = w.window_text()
        except Exception:
            continue
        if title.lower() in (t or "").lower():
            matches.append(t)
    print(f"подходящих окон: {len(matches)}")
    for t in matches:
        print(f"  • {t}")
    if not matches:
        print("Окно GSA не найдено. Запущен ли GSA? Совпадает ли ui_window_title "
              "с заголовком окна? Попробуйте ui_backend=\"win32\".")
        return

    try:
        _app, win = _connect(cfg)
        win.set_focus()
    except Exception as e:
        print(f"Не удалось подключиться: {type(e).__name__}: {e}")
        return

    out = data_dir / "ui_controls.txt"
    data_dir.mkdir(parents=True, exist_ok=True)
    depth = int(cfg.get("ui_dump_depth", 6) or 6)
    with out.open("w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
        try:
            win.print_control_identifiers(depth=depth)
        except Exception as e:
            fh.write(f"print_control_identifiers упал: {e}\n")
    print(f"Дерево контролов (depth={depth}) записано в {out}")
    print("Пришлите этот файл — по нему настрою грид проектов и пункт меню рефреша.")
    if backend == "uia":
        print("Если грид/меню не видны (только Pane) — GSA рисует их сам. Сравните с "
              "win32-бэкендом: добавьте в конфиг \"ui_backend\": \"win32\" и повторите "
              "--ui-check (Delphi-контролы и меню часто видны там лучше).")


def _find_grid(win, cfg):
    """Грид проектов. auto_id у GSA — это HWND (меняется при запуске), поэтому по
    умолчанию ищем по геометрии: самая большая панель в верхней части окна. Явный
    ui_grid_auto_id можно задать, если стабилен."""
    aid = cfg.get("ui_grid_auto_id")
    if aid:
        return win.child_window(auto_id=str(aid))
    try:
        wr = win.rectangle()
        top_limit = wr.top + wr.height() * 0.7      # верхние ~70% окна
        best, best_area = None, 0
        for p in win.descendants(control_type="Pane"):
            r = p.rectangle()
            if r.top > top_limit:
                continue
            area = r.width() * r.height()
            if area > best_area:
                best, best_area = p, area
        return best or win
    except Exception:
        return win


def _click_menu_item(app, win, target, backend, item, log, delay=0.4) -> bool:
    """Правый клик по target → клик пункта меню по тексту. При неудаче закрывает
    меню (Esc) и возвращает False. Работает и с uia, и с win32 (#32768)."""
    import time
    from pywinauto import Desktop
    try:
        target.right_click_input()
    except Exception:
        win.right_click_input()
    time.sleep(delay)
    try:
        if backend == "win32":
            menu = app.window(class_name="#32768")
            menu.menu_item(item).click_input()
        else:
            menu = Desktop(backend="uia").window(control_type="Menu")
            menu.child_window(title=item, control_type="MenuItem").click_input()
        log.info(f"ui: пункт меню «{item}» нажат")
        return True
    except Exception as e:
        try:
            win.type_keys("{ESC}")
        except Exception:
            pass
        log.error(f"ui: пункт меню «{item}» не найден: {type(e).__name__}: {e}")
        return False


def refresh(cfg, log) -> bool:
    """Повторяет ручной рефреш GSA:
      1) ПКМ по гриду → пункт `ui_refresh_item` (по умолчанию "refresh");
      2) тумблер статуса одного проекта Active→Inactive→Active (чтобы всё перечиталось):
         выбрать первую строку и дважды дёрнуть статус через пункты
         `ui_status_off`/`ui_status_on`.
    Все тексты/координаты — в конфиге. Возвращает True, если шаг 1 удался."""
    _require_pywinauto()
    import time
    delay = float(cfg.get("ui_menu_delay", 0.4) or 0.4)
    backend = cfg.get("ui_backend", "uia")
    try:
        app, win = _connect(cfg)
    except Exception as e:
        log.error(f"ui: не подключились к GSA: {type(e).__name__}: {e}")
        return False

    try:
        win.set_focus()
        grid = _find_grid(win, cfg)
        try:
            grid.click_input()
        except Exception:
            pass

        # 1) ПКМ → refresh
        ok = _click_menu_item(app, win, grid, backend,
                              cfg.get("ui_refresh_item", "refresh"), log, delay)

        # 2) тумблер статуса одного проекта
        if cfg.get("ui_toggle_status", True):
            from pywinauto import mouse
            r = grid.rectangle()
            off = cfg.get("ui_row_offset", [30, 25])
            row_xy = (r.left + int(off[0]), r.top + int(off[1]))
            mouse.click(coords=row_xy)           # выбрать первую строку проекта
            time.sleep(0.3)
            # ПКМ по выбранной строке; статус off, затем on
            grid_at_row = grid
            _click_menu_item(app, win, grid_at_row, backend,
                             cfg.get("ui_status_off", "Inactive"), log, delay)
            time.sleep(0.3)
            mouse.click(coords=row_xy)
            time.sleep(0.2)
            _click_menu_item(app, win, grid_at_row, backend,
                             cfg.get("ui_status_on", "Active"), log, delay)

        keys = cfg.get("ui_refresh_keys", "")
        if keys:
            win.type_keys(keys, set_foreground=True)
        return ok
    except Exception as e:
        log.error(f"ui: рефреш не удался: {type(e).__name__}: {e}. "
                  "Сверьте пункты меню по --ui-check (win32).")
        return False
