@echo off
rem Tache planifiee "Cockpit - Patrouilles" (chaque minute) : delais des
rem propositions de patrouille, pauses / reprises, planifications et envoi
rem des intrusions aux tablettes. Boucle ~55 s puis sort (verrou en base).
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d E:\TITAN\production\cockpit
"E:\TITAN\production\titan_prod\Scripts\python.exe" -X utf8 "patrol_engine.py"
endlocal
exit /b 0
