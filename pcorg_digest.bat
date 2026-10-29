@echo off
rem Tache planifiee "Cockpit - Fiches structurees" (chaque minute) :
rem demandes de lots deposees depuis /admin/gravite-fiches, et traitement
rem de nuit 23:00-06:50 s'il est active dans la page. Sort aussitot sinon.
rem Fin a 06:50 : la tache a une limite de 8 h (PT8H) qui tuait le
rem traitement a 07:00:02, avant son bilan (constat du 28/10/2026).
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d E:\TITAN\production\cockpit
"E:\TITAN\production\titan_prod\Scripts\python.exe" -X utf8 "pcorg_digest.py" --poll --night 23:00-06:50
endlocal
exit /b 0
