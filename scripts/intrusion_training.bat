@echo off
REM ============================================================================
REM  Cockpit - Entrainement IA intrusions (scripts\intrusion_training.py)
REM  Lance par tache planifiee Windows CHAQUE NUIT a 03:30. Ne fait rien tant
REM  qu'il n'y a pas assez de reponses nouvelles des operateurs ; sinon apprend
REM  l'avis de l'IA (niveau 2), regle le seuil d'alerte (niveau 1) et envoie le
REM  rapport par mail (INTRUSION_TRAINING_MAIL_TO, defaut l.arnault@lemans.org).
REM
REM  Pre-requis :
REM    - service IntrusionVerifier demarre (empreintes SigLIP, POST /embed)
REM    - COCKPIT_PYTHON (defaut E:\TITAN\production\titan_prod\Scripts\python.exe)
REM    - COCKPIT_DIR    (defaut E:\TITAN\production\cockpit)
REM    - TITAN_ENV=prod / MONGO_URI au niveau Machine (deja le cas en prod)
REM
REM  Logs : <COCKPIT_DIR>\logs\intrusion_training-YYYYMMDD.log
REM ============================================================================

setlocal EnableExtensions

if "%COCKPIT_DIR%"=="" set "COCKPIT_DIR=E:\TITAN\production\cockpit"
if "%COCKPIT_PYTHON%"=="" set "COCKPIT_PYTHON=E:\TITAN\production\titan_prod\Scripts\python.exe"

if not exist "%COCKPIT_DIR%\scripts\intrusion_training.py" (
    echo [ERREUR] intrusion_training.py introuvable dans %COCKPIT_DIR%\scripts
    exit /b 1
)

cd /d "%COCKPIT_DIR%"
"%COCKPIT_PYTHON%" -X utf8 scripts\intrusion_training.py %* > nul 2>&1
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
