@echo off
rem Tache planifiee "Cockpit - Fiches structurees" (chaque minute) :
rem demandes de lots deposees depuis /admin/gravite-fiches, et traitement
rem de nuit 01:00-06:00 s'il est active dans la page. Sort aussitot sinon.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d E:\TITAN\production\cockpit
"E:\TITAN\production\titan_prod\Scripts\python.exe" -X utf8 "pcorg_digest.py" --poll --night 01:00-06:00
endlocal
exit /b 0
