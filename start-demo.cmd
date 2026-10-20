@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AgentFlow Pay
set "AGENTFLOW_OPEN_BROWSER=1"
set "PYTHONUTF8=1"
echo AgentFlow Pay 启动窗口，请保持此窗口打开。
echo 正在启动当前版本；已运行的本项目服务会先停止再重启。
if not exist ".cache\venv\Scripts\python.exe" (
  echo 尚未完成首次安装，请先运行 python manage.py setup。
  pause
  exit /b 1
)
".cache\venv\Scripts\python.exe" -u scripts\local_demo.py dev
if errorlevel 1 echo 启动未完成，上方是具体原因。项目记录已保留。
echo.
pause
