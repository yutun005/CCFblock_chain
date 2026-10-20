@echo off
setlocal
cd /d "%~dp0"
set "AGENTFLOW_OPEN_BROWSER=1"
".cache\venv\Scripts\python.exe" manage.py dev
if errorlevel 1 pause
