@echo off
REM ============================================================================
REM  Cockpit - Capture IA (paires de photos analysees par l'IA locale : trafic)
REM  Lance par tache planifiee Windows TOUTES LES MINUTES, 24 h/24. Le script ne
REM  capture que les cameras dues (rythme de chaque camera, page IA) et dans
REM  leur planning ; un verrou empeche deux passages en meme temps.
REM
REM  Pre-requis :
REM    - tache executee sous le COMPTE DE COCKPIT (variable utilisateur
REM      COCKPIT_CAMERA_KEY, sans elle les identifiants cameras sont illisibles)
REM    - service IntrusionVerifier demarre (detection des vehicules, GPU)
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM    - TITAN_ENV=prod / MONGO_URI au niveau Machine (deja le cas en prod)
REM
REM  Logs : <COCKPIT_DIR>\logs\capture_ia-YYYYMMDD.log (ecrit par le script)
REM ============================================================================

setlocal EnableExtensions

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\capture_ia.py" (
    echo [ERREUR] capture_ia.py introuvable dans %COCKPIT_DIR%
    exit /b 1
)
if not exist "%COCKPIT_PYTHON%" (
    echo [ERREUR] Python introuvable : %COCKPIT_PYTHON%
    exit /b 1
)

cd /d "%COCKPIT_DIR%"
"%COCKPIT_PYTHON%" -X utf8 capture_ia.py %* > nul 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
