@echo off
rem 备用启动方式（.vbs 被安全策略拦截时用它）
setlocal
set "WB_PY="
call "%~dp0_findpy.bat"
if not defined WB_PY (
  echo Python with tkinter not found.
  echo Install Python 3.9+ from python.org, or set WB_PYTHON to python.exe.
  pause
  exit /b 1
)
rem 优先用 pythonw（无黑窗口），退而用 python
set "PYW=%WB_PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%WB_PY%"
start "" "%PYW%" "%~dp0wb_credit_widget.py"
