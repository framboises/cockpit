@echo off
REM ============================================================================
REM  Cockpit - Export quotidien des activites du site pour Claude Cowork
REM  (Momentus 60 jours + grands evenements Groundmaster) vers SharePoint
REM  SAFER / COWORK / activites_site_60j.json
REM
REM  Pre-requis :
REM    - connexion Graph faite une fois : python scripts\export_cowork_activites.py --login
REM      (cache msal_cowork_cache.bin dans COCKPIT_DIR)
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM
REM  Logs : <COCKPIT_DIR>\logs\export_cowork-YYYYMMDD.log
REM ============================================================================

setlocal EnableExtensions

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
if not exist "%COCKPIT_DIR%\logs" mkdir "%COCKPIT_DIR%\logs"
set "LOGFILE=%COCKPIT_DIR%\logs\export_cowork-%TODAY%.log"

cd /d "%COCKPIT_DIR%"
"%COCKPIT_PYTHON%" -X utf8 scripts\export_cowork_activites.py >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
