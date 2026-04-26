@echo off
REM ============================================================================
REM  Cockpit - Rapport matinal PC Organisation
REM  Lance par tache planifiee Windows tous les matins a 07h00.
REM
REM  Pre-requis :
REM    - Python prod (par defaut : E:\TITAN\production\titan_prod\Scripts\python.exe)
REM      Surcharge possible via la variable d'env COCKPIT_PYTHON.
REM    - Cockpit installe (par defaut : E:\TITAN\production\cockpit)
REM      Surcharge via COCKPIT_DIR.
REM    - Variables d'env SMTP_* et ANTHROPIC_API_KEY definies au niveau Machine
REM      (ou User si la tache tourne sous ton compte) — voir README.
REM
REM  Detection de l'evenement cible :
REM    Le script DEVINE l'evenement actuel via la logique du live-status
REM    (parametrages dont montage.start <= now <= demontage.end).
REM    Si aucun event actif n'est trouve, fallback automatique sur SAISON.
REM    Aucune variable d'env ni argument n'est necessaire.
REM
REM  Logs : ecrits dans <COCKPIT_DIR>\logs\morning_report-YYYYMMDD.log
REM ============================================================================

setlocal EnableExtensions EnableDelayedExpansion

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\pcorg_morning_report.py" (
    echo [ERREUR] pcorg_morning_report.py introuvable dans %COCKPIT_DIR%
    exit /b 1
)
if not exist "%COCKPIT_PYTHON%" (
    echo [ERREUR] Python introuvable : %COCKPIT_PYTHON%
    exit /b 1
)

REM Date du jour pour nommer le log (format YYYYMMDD via PowerShell pour eviter
REM les surprises de date locale d'invite de commande).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
if not exist "%COCKPIT_DIR%\logs" mkdir "%COCKPIT_DIR%\logs"
set "LOGFILE=%COCKPIT_DIR%\logs\morning_report-%TODAY%.log"

cd /d "%COCKPIT_DIR%"

echo. >> "%LOGFILE%"
echo === Lancement %DATE% %TIME% === >> "%LOGFILE%"
"%COCKPIT_PYTHON%" -X utf8 pcorg_morning_report.py >> "%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"
echo === Fin %DATE% %TIME% (code retour %RC%) === >> "%LOGFILE%"

endlocal & exit /b %RC%
