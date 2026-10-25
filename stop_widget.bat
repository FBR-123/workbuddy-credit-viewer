@echo off
rem Stop the watcher FIRST, otherwise it would relaunch the widget right away.
setlocal
set "WB_PY="
call "%~dp0_findpy.bat"
if not defined WB_PY (
  echo Python with tkinter not found.
  rem 停止不需要 tkinter，随手找个 python 也行
  for /f "delims=" %%i in ('where python.exe 2^>nul') do if not defined WB_PY set "WB_PY=%%i"
)
if not defined WB_PY (
  echo No Python found. Install Python 3.9+ from python.org, or set WB_PYTHON.
  pause
  exit /b 1
)
"%WB_PY%" "%~dp0wb_credit_widget.py" --stop-all
echo.
pause
