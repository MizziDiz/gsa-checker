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


def _menu_action(focus_xy, seq, opts, log, label):
    """Выполняет один шаг меню на ОДНОМ выбранном проекте:
      1) клик в грид (фокус) → select_keys (напр. {HOME}{DOWN} — встать на 1-й проект,
         минуя строку категории);
      2) открыть контекстное меню на выбранном проекте (open_key={APPS}), либо ПКМ по
         координате, если open_key пустой;
      3) послать seq (навигация по меню).
    Так меню применяется к проекту, а не к категории. Пустой seq пропускается."""
    import time
    from pywinauto import mouse, keyboard
    pause, delay, select_keys, open_key = opts
    if not seq:
        log.info(f"ui: шаг «{label}» пропущен (последовательность пуста)")
        return False
    mouse.click(coords=focus_xy)                 # фокус в грид
    time.sleep(0.3)
    if open_key:
        if select_keys:
            keyboard.send_keys(select_keys, pause=pause)   # выбрать проект
            time.sleep(0.25)
        keyboard.send_keys(open_key, pause=pause)          # {APPS} → меню на проекте
    else:
        mouse.right_click(coords=focus_xy)
    time.sleep(delay)
    keyboard.send_keys(seq, pause=pause)
    time.sleep(delay)
    log.info(f"ui: шаг «{label}» — выбор [{select_keys or 'ПКМ'}] → меню {seq}")
    return True


def _save_dialog(out_path, pause, wait, log) -> None:
    """Вписывает путь в диалог «Сохранить как» и подтверждает. Диалог — стандартное
    общее окно Windows (класс #32770, ОДИНАКОВ на любой локали), поэтому ищем по классу,
    а не по заголовку («Save as»/«Сохранить как»). Fallback — печать пути в фокус."""
    import time
    from pywinauto import keyboard
    time.sleep(wait)
    try:
        from pywinauto import Desktop
        dlg = Desktop(backend="win32").window(class_name="#32770")
        dlg.wait("exists ready", timeout=wait + 5)
        edit = dlg.child_window(class_name="Edit", found_index=0)
        edit.set_edit_text(str(out_path))
        time.sleep(0.3)
        keyboard.send_keys("{ENTER}", pause=pause)
        log.info(f"ui: путь вписан в диалог сохранения → {out_path}")
        return
    except Exception as e:
        log.info(f"ui: диалог по классу не найден ({type(e).__name__}); печатаю в фокус")
    # fallback: очистить поле и напечатать путь как есть (пробелы — with_spaces)
    keyboard.send_keys("^a{BACKSPACE}", pause=pause)
    keyboard.send_keys(str(out_path), with_spaces=True, pause=0.01)
    keyboard.send_keys("{ENTER}", pause=pause)


def export_verified(cfg, out_path, log) -> bool:
    """Автоматизирует РУЧНУЮ выгрузку verified-CSV из GSA (тот, где колонки IP/Country).
    Ручной путь оператора (GSA v18.98): выделить все проекты → ПКМ → Modify Project →
    Export → Create Report → в окне «Select Reports» галка «Verified URLs (CSV Format)» →
    OK → «Сохранить как». Один общий CSV на все выбранные проекты (колонка Project).

    Клавишами (меню owner-drawn, UIA слепо):
      1) `ui_export_select_seq` (по умолч. `^a` — выделить все проекты);
      2) `ui_open_menu_key` ({VK_APPS}) + `ui_export_menu_seq` — пройти до Create Report:
         `{UP}{UP}{RIGHT}` = Modify Project (предпоследний пункт) → его подменю; далее
         `{DOWN}×6{RIGHT}` = дойти до Export и открыть его подменю (клавиатура GSA НЕ
         пропускает серые пункты — 2 серых в счёте: Edit Only Engines/Options, Edit single
         Option for All); затем `{UP}{ENTER}` = Create Report (последний из трёх, wrap);
      3) `ui_export_trigger_seq` ({ENTER}) — OK в «Select Reports» (галка CSV запоминается
         GSA между запусками);
      4) диалог «Сохранить как» — вписать out_path (см. _save_dialog).
    Рядом с Export деструктивные пункты (Delete/Reset Data), поэтому первый прогон на новом
    билде — глазами; при промахе править число {DOWN} в `ui_export_menu_seq`. Возвращает
    True, если файл появился на диске."""
    _require_pywinauto()
    import time
    from pywinauto import mouse, keyboard
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        if out_path.exists():
            out_path.unlink()          # чтобы проверка «файл появился» была честной

    delay = float(cfg.get("ui_menu_delay", 0.5) or 0.5)
    pause = float(cfg.get("ui_key_pause", 0.25) or 0.25)
    wait = float(cfg.get("ui_export_dialog_wait", 2.0) or 2.0)
    try:
        app, win = _connect(cfg)
        win.set_focus()
        grid = _find_grid(win, cfg)
        r = grid.rectangle()
        off = cfg.get("ui_row_offset", [30, 25])
        focus_xy = (r.left + int(off[0]), r.top + int(off[1]))
        select_keys = cfg.get("ui_export_select_seq", "^a")        # все проекты
        open_key = cfg.get("ui_open_menu_key", "{VK_APPS}")
        menu_seq = cfg.get("ui_export_menu_seq", "")               # → Show URLs → Verified
        if not menu_seq:
            log.error("ui: не задан ui_export_menu_seq (путь меню Show URLs → Verified). "
                      "Настройте по --ui-check и ручным шагам — экспорт не запускаю.")
            return False

        # 1-2) выбрать проекты и пройти меню до «Show URLs → Verified»
        mouse.click(coords=focus_xy)
        time.sleep(0.3)
        if select_keys:
            keyboard.send_keys(select_keys, pause=pause)
            time.sleep(0.3)
        keyboard.send_keys(open_key, pause=pause)
        time.sleep(delay)
        keyboard.send_keys(menu_seq, pause=pause)
        log.info(f"ui: выбор {select_keys!r} → меню {open_key!r} → {menu_seq!r}")
        time.sleep(wait)               # ждём окно списка Show URLs

        # 3) вызвать экспорт в окне списка (если у билда есть горячая последовательность)
        trigger = cfg.get("ui_export_trigger_seq", "")
        if trigger:
            keyboard.send_keys(trigger, pause=pause)
            log.info(f"ui: экспорт в окне списка {trigger!r}")

        # 4) диалог «Сохранить как»
        _save_dialog(out_path, pause, wait, log)

        # подтверждение: ждём появления файла
        for _ in range(int(cfg.get("ui_export_settle_sec", 20) or 20)):
            if out_path.exists() and out_path.stat().st_size > 0:
                log.info(f"ui: выгрузка готова → {out_path} "
                         f"({out_path.stat().st_size:,} байт)")
                return True
            time.sleep(1)
        log.error(f"ui: файл не появился: {out_path}. Проверьте ui_export_menu_seq / "
                  f"ui_export_trigger_seq по --ui-check и ручным шагам.")
        return False
    except Exception as e:
        log.error(f"ui: экспорт не удался: {type(e).__name__}: {e}")
        return False


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
        focus_xy = (r.left + int(off[0]), r.top + int(off[1]))
        select_keys = cfg.get("ui_select_keys", "{HOME}{DOWN}")   # 1-й проект (мимо категории)
        open_key = cfg.get("ui_open_menu_key", "{VK_APPS}")       # меню на выбранном проекте
        opts = (pause, delay, select_keys, open_key)
        log.info(f"ui: грид {r.width()}x{r.height()} @({r.left},{r.top}); "
                 f"фокус {focus_xy}; выбор {select_keys!r}; меню {open_key!r}")

        # 1) Refresh (последний пункт меню)
        ok = _menu_action(focus_xy, cfg.get("ui_refresh_seq", "{UP}{ENTER}"),
                          opts, log, "Refresh")

        # 2) тумблер статуса ОДНОГО проекта: Set Status → Inactive, затем → Active
        if cfg.get("ui_toggle_status", True):
            _menu_action(focus_xy, cfg.get("ui_status_off_seq", "{DOWN}{RIGHT}{DOWN}{ENTER}"),
                         opts, log, "Set Status → Inactive")
            time.sleep(gap)          # дать GSA применить Inactive до возврата в Active
            _menu_action(focus_xy, cfg.get("ui_status_on_seq", "{DOWN}{RIGHT}{ENTER}"),
                         opts, log, "Set Status → Active")
        return ok
    except Exception as e:
        log.error(f"ui: рефреш не удался: {type(e).__name__}: {e}")
        return False
