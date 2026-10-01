@echo off
REM ============================================================================
REM  Cockpit - Collecte frequentation du Musee (borne Handshake, lecture seule)
REM  Lance par tache planifiee Windows TOUTES LES 5 MINUTES, 24 h/24.
REM  Autonome du live controle : n'ecrit que les collections musee_*.
REM
REM  Pre-requis :
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM    - TITAN_ENV=prod / MONGO_URI au niveau Machine (deja le cas en prod)
REM
REM  Logs : <COCKPIT_DIR>\logs\musee_collect-YYYYMMDD.log (ecrit par le script)
REM ============================================================================

setlocal EnableExtensions

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\scripts\musee_collect.py" (
    echo [ERREUR] scripts\musee_collect.py introuvable dans %COCKPIT_DIR%
    exit /b 1
)
if not exist "%COCKPIT_PYTHON%" (
    echo [ERREUR] Python introuvable : %COCKPIT_PYTHON%
    exit /b 1
)

cd /d "%COCKPIT_DIR%"
"%COCKPIT_PYTHON%" -X utf8 scripts\musee_collect.py %* > nul 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
