@echo off
REM ============================================================================
REM  Cockpit - Synchro Momentus Elite -> MongoDB (lecture seule cote Momentus)
REM  Lance par tache planifiee Windows TOUTES LES HEURES. Le script bascule seul
REM  en import complet (reconciliation des suppressions) une fois par jour.
REM
REM  Pre-requis :
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM    - MOMENTUS_CLIENT_ID / MOMENTUS_CLIENT_SECRET au niveau Machine
REM      (la tache tourne en SYSTEM, qui ne voit pas les variables utilisateur)
REM    - TITAN_ENV / MONGO_URI au niveau Machine (deja le cas en prod)
REM
REM  Logs : <COCKPIT_DIR>\logs\momentus_sync-YYYYMMDD.log (ecrit par le script)
REM ============================================================================

setlocal EnableExtensions

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\momentus_sync.py" (
    echo [ERREUR] momentus_sync.py introuvable dans %COCKPIT_DIR%
    exit /b 1
)
if not exist "%COCKPIT_PYTHON%" (
    echo [ERREUR] Python introuvable : %COCKPIT_PYTHON%
    exit /b 1
)

cd /d "%COCKPIT_DIR%"
"%COCKPIT_PYTHON%" -X utf8 momentus_sync.py %* > nul 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
