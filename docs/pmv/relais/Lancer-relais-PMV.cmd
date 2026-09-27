@echo off
rem Lance le relais PMV (voir relais-pmv.ps1). Laisser la fenetre ouverte pendant les envois.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0relais-pmv.ps1"
