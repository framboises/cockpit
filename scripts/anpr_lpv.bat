@echo off
REM ============================================================================
REM  Cockpit - LAPI par consultation (Axis License Plate Verifier, lecture seule)
REM  Lance par tache planifiee Windows TOUTES LES 5 MINUTES, 24 h/24.
REM  Recupere toutes les nouvelles lectures de plaques depuis la derniere connue.
REM
REM  Pre-requis :
REM    - tache executee sous le COMPTE DE COCKPIT (variable utilisateur
REM      COCKPIT_CAMERA_KEY, sans elle les identifiants cameras sont illisibles)
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM    - TITAN_ENV=prod / MONGO_URI au niveau Machine (deja le cas en prod)
REM
REM  Logs : <COCKPIT_DIR>\logs\anpr_lpv-YYYYMMDD.log (ecrit par le script)
REM ============================================================================

setlocal EnableExtensions

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\anpr_lpv.py" (
    echo [ERREUR] anpr_lpv.py introuvable dans %COCKPIT_DIR%
    exit /b 1
)
if not exist "%COCKPIT_PYTHON%" (
    echo [ERREUR] Python introuvable : %COCKPIT_PYTHON%
    exit /b 1
)

cd /d "%COCKPIT_DIR%"
"%COCKPIT_PYTHON%" -X utf8 anpr_lpv.py %* > nul 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
