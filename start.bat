@echo off
cd /d "%~dp0"
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (where py >nul 2>nul && set "PY=py")
if not defined PY (where python3 >nul 2>nul && set "PY=python3")
if not defined PY (
  echo 未找到 Python，请先安装 Python 3 并加入 PATH。
  pause
  exit /b 1
)
echo 正在启动量化策略工作台（本地离线版）...
start "QuantWorkbench" /min "%PY%" "%~dp0server.py"
timeout /t 2 >nul
start "" "http://localhost:8753/"
