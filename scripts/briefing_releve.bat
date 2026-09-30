@echo off
REM ============================================================================
REM  Cockpit - Briefing de situation a chaque releve
REM  Lance par tache planifiee Windows TOUTES LES 15 MINUTES : le script decide
REM  lui-meme s'il y a un creneau de releve a traiter (heures editees dans
REM  Cockpit, cockpit_settings.briefing_releve).
REM
REM  Pre-requis (identiques au rapport matinal) :
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM    - Variables SMTP_* et COCKPIT_ANTHROPIC_API_KEY au niveau Machine
REM
REM  Logs : <COCKPIT_DIR>\logs\briefing_releve-YYYYMMDD.log
REM ============================================================================

setlocal EnableExtensions EnableDelayedExpansion

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\scripts\briefing_releve.py" (
    echo [ERREUR] scripts\briefing_releve.py introuvable dans %COCKPIT_DIR%
    exit /b 1
)
if not exist "%COCKPIT_PYTHON%" (
    echo [ERREUR] Python introuvable : %COCKPIT_PYTHON%
    exit /b 1
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
if not exist "%COCKPIT_DIR%\logs" mkdir "%COCKPIT_DIR%\logs"
set "LOGFILE=%COCKPIT_DIR%\logs\briefing_releve-%TODAY%.log"

cd /d "%COCKPIT_DIR%"

"%COCKPIT_PYTHON%" -X utf8 scripts\briefing_releve.py >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
