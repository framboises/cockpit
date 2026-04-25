@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
cd /d E:\TITAN\production\cockpit
"E:\TITAN\production\titan_prod\Scripts\python.exe" -X utf8 "field_purge_photos.py"
endlocal
exit /b 0
