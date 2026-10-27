@echo off
REM ============================================================================
REM  Cockpit - Evenements voisins (Antares, MSB, Le Mans FC) -> MongoDB
REM  Lance par tache planifiee Windows 3 fois par jour (02:00, 06:15 et 13:15),
REM  puis enchaine foot_sync.py de Groundmaster (matchs du Mans FC).
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

REM Puis Groundmaster : preremplissage des matchs du Mans FC a partir de
REM voisins_events (log : <GROUNDMASTER_DIR>\logs\foot_sync-YYYYMMDD.log).
REM Son echec ne change pas le code retour de la synchro voisins.
if "%GROUNDMASTER_DIR%"=="" set "GROUNDMASTER_DIR=E:\TITAN\production\groundmaster"
if exist "%GROUNDMASTER_DIR%\foot_sync.py" (
    cd /d "%GROUNDMASTER_DIR%"
    "%COCKPIT_PYTHON%" -X utf8 foot_sync.py > nul 2>&1
)

endlocal & exit /b %RC%
