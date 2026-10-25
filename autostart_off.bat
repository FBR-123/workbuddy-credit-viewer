@echo off
rem Removes the "start with WorkBuddy" logon item and stops the watcher.
rem Invoked by autostart_off.vbs (which only hides/shows the console window).
setlocal
set "WB_PY="
call "%~dp0_findpy.bat"
if not defined WB_PY goto :nopy

rem 卸载不需要 tkinter，随手找个 python 也能完成；但 --autostart off 会 import 主模块，
rem 主模块已做到 tkinter 可选，所以这里用找到的任意一个即可。
rem 允许测试时用 WB_FORCE_MSG=0 强制走 stdout（对话框没法自动化断言）
if not defined WB_FORCE_MSG set "WB_FORCE_MSG=1"
set "PYW=%WB_PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%WB_PY%"
"%PYW%" "%~dp0wb_credit_widget.py" --autostart off
exit /b 0

:nopy
echo.
echo   Python was not found.
echo   Install Python 3.9+ from python.org, or set WB_PYTHON to python.exe.
echo.
pause
exit /b 1
