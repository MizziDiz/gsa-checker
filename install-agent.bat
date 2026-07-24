@echo off
chcp 65001 >nul
setlocal
rem ============================================================================
rem  install-agent.bat - регистрация gsa-control-агента (по образцу install-tasks.bat
rem  из A-GSA). Запускать ИЗ папки проекта, в cmd ОТ АДМИНИСТРАТОРА.
rem ----------------------------------------------------------------------------
rem  ПЕРЕД запуском проверь data\gsa_checker.config.json на ЭТОЙ ноде:
rem     agent_token = токен ноды из реестра шары  (data\ops\agent_token_<нода>.txt)
rem     agent_bind  = 0.0.0.0:8787
rem     server_name = имя ЭТОЙ ноды
rem  И открой файрвол на 8787 ТОЛЬКО для IP шары (нода на публичном IP!):
rem     netsh advfirewall firewall add rule name="gsa-agent-in" dir=in action=allow
rem       protocol=TCP localport=8787 remoteip=^<IP-ШАРЫ^>
rem
rem  Почему ONLOGON, а не ONSTART/SYSTEM (как relay): агент запускает gsa_checker.py,
rem  который автоматизирует GUI GSA - значит должен жить в интерактивной сессии
rem  пользователя (там же, где крутится сам GSA при автологоне).
rem ============================================================================
cd /d %~dp0
set HERE=%~dp0
if not exist "%HERE%data" mkdir "%HERE%data"

echo Регистрирую gsa-agent, ONLOGON в сессии пользователя, и запускаю...
schtasks /Create /TN "gsa-agent" /SC ONLOGON /RL HIGHEST /F ^
  /RU "%USERDOMAIN%\%USERNAME%" /RP * ^
  /TR "\"%HERE%gsa-agent-run.bat\""
schtasks /Run /TN "gsa-agent"

echo.
echo ГОТОВО. Проверка:
echo    curl http://127.0.0.1:8787/health
echo    schtasks /Query /TN "gsa-agent" /V /FO LIST ^| findstr /I "Result Run"
echo Лог первого запуска: data\agent.out.log
endlocal
