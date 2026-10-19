@echo off
cd /d "%~dp0"
".cache\venv\Scripts\python.exe" manage.py dev
pause
