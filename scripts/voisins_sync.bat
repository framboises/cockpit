@echo off
REM ============================================================================
REM  Cockpit - Evenements voisins (Antares, MSB, Le Mans FC) -> MongoDB
REM  Lance par tache planifiee Windows 2 fois par jour (06:15 et 13:15).
REM  Sources publiques : antaresarena.com, lnb.fr, ESPN. Aucun identifiant.
REM
REM  Pre-requis :
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM    - TITAN_ENV / MONGO_URI au niveau Machine (deja le cas en prod)
REM
REM  Logs : <COCKPIT_DIR>\logs\voisins_sync-YYYYMMDD.log (ecrit par le script)
REM ============================================================================

setlocal EnableExtensions

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\voisins_sync.py" (
    echo [ERREUR] voisins_sync.py introuvable dans %COCKPIT_DIR%
    exit /b 1
)
if not exist "%COCKPIT_PYTHON%" (
    echo [ERREUR] Python introuvable : %COCKPIT_PYTHON%
    exit /b 1
)

cd /d "%COCKPIT_DIR%"
"%COCKPIT_PYTHON%" -X utf8 voisins_sync.py %* > nul 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
