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


def _menu_keys(row_xy, seq, pause, delay, log, label):
    """Открывает контекстное меню (ПКМ по строке) и шлёт клавиатурную
    последовательность seq (меню owner-drawn, UIA его не видит — управляем клавишами).
    Пустой seq пропускается."""
    import time
    from pywinauto import mouse, keyboard
    if not seq:
        log.info(f"ui: шаг «{label}» пропущен (последовательность не задана)")
        return False
    mouse.right_click(coords=row_xy)
    time.sleep(delay)
    keyboard.send_keys(seq, pause=pause)
    time.sleep(delay)
    log.info(f"ui: шаг «{label}» — послано {seq}")
    return True


def refresh(cfg, log) -> bool:
    """Повторяет ручной рефреш GSA КЛАВИАТУРОЙ (меню owner-drawn, UIA его не видит):
      1) ПКМ по строке проекта → `ui_refresh_seq` (по умолч. {UP}{ENTER} = последний
         пункт "Refresh");
      2) тумблер статуса: ПКМ → `ui_status_off_seq`, затем ПКМ → `ui_status_on_seq`
         (проход по подменю "Set Status" клавишами; задаётся в конфиге).
    Координаты строки — `ui_row_offset`. Возвращает True, если шаг 1 отправлен."""
    _require_pywinauto()
    import time
    from pywinauto import mouse
    delay = float(cfg.get("ui_menu_delay", 0.5) or 0.5)
    pause = float(cfg.get("ui_key_pause", 0.25) or 0.25)   # пауза между клавишами:
    #   критично, чтобы подменю успело раскрыться до {DOWN}/{ENTER}
    gap = float(cfg.get("ui_status_gap", 1.2) or 1.2)      # между Inactive и Active
    try:
        app, win = _connect(cfg)
    except Exception as e:
        log.error(f"ui: не подключились к GSA: {type(e).__name__}: {e}")
        return False

    try:
        win.set_focus()
        grid = _find_grid(win, cfg)
        r = grid.rectangle()
        off = cfg.get("ui_row_offset", [30, 25])
        row_xy = (r.left + int(off[0]), r.top + int(off[1]))
        log.info(f"ui: грид {r.width()}x{r.height()} @({r.left},{r.top}); ПКМ по {row_xy}")
        mouse.click(coords=row_xy)                 # выбрать строку проекта
        time.sleep(0.3)

        # 1) Refresh (по умолчанию последний пункт меню: {UP}{ENTER})
        ok = _menu_keys(row_xy, cfg.get("ui_refresh_seq", "{UP}{ENTER}"),
                        pause, delay, log, "Refresh")

        # 2) тумблер статуса: Set Status (1-й пункт) → подменю Active(1)/Inactive(2)
        if cfg.get("ui_toggle_status", True):
            _menu_keys(row_xy, cfg.get("ui_status_off_seq", "{DOWN}{RIGHT}{DOWN}{ENTER}"),
                       pause, delay, log, "Set Status → Inactive")
            time.sleep(gap)          # дать GSA применить Inactive до возврата в Active
            _menu_keys(row_xy, cfg.get("ui_status_on_seq", "{DOWN}{RIGHT}{ENTER}"),
                       pause, delay, log, "Set Status → Active")
        return ok
    except Exception as e:
        log.error(f"ui: рефреш не удался: {type(e).__name__}: {e}")
        return False
