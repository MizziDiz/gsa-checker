@echo off
rem ============================================================================
rem  gsa-agent-run.bat - долгоживущий control-агент ноды с автоперезапуском.
rem  Образец - aparser-relay/relay-run.bat из A-GSA. Ставится install-agent.bat
rem  как задача планировщика (ONLOGON, в сессии пользователя - агент дёргает
rem  gsa_checker.py, а тот автоматизирует GUI GSA, значит нужен рабочий стол).
rem  Лог - data\agent.out.log.
rem  В конфиге ноды (data\gsa_checker.config.json) обязательно:
rem     agent_token  = токен ЭТОЙ ноды из реестра шары (data\ops\agent_token_<нода>.txt)
rem     agent_bind   = 0.0.0.0:8787   (иначе шара не достучится)
rem     server_name  = имя ЭТОЙ ноды
rem ============================================================================
cd /d %~dp0
:loop
py agent.py >> data\agent.out.log 2>&1
timeout /t 10 /nobreak >nul
goto loop
