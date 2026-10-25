@echo off
rem Installs the "start with WorkBuddy" logon item and starts the watcher.
rem Invoked by autostart_on.vbs (which only hides/shows the console window).
rem
rem All real logic lives here rather than in the .vbs: the script host is
rem blocked in some environments, so batch is the layer that can be tested.
setlocal
set "WB_PY="
call "%~dp0_findpy.bat"
if not defined WB_PY goto :nopy

rem The console may be hidden; force Python to use a dialog for its message.
rem 允许测试时用 WB_FORCE_MSG=0 强制走 stdout（对话框没法自动化断言）
if not defined WB_FORCE_MSG set "WB_FORCE_MSG=1"
set "PYW=%WB_PY:python.exe=pythonw.exe%"
if not exist "%PYW%" set "PYW=%WB_PY%"
"%PYW%" "%~dp0wb_credit_widget.py" --autostart on
exit /b 0

:nopy
echo.
echo   Python with tkinter was not found.
echo   Install Python 3.9+ from python.org (keep the "tcl/tk" component),
echo   or set WB_PYTHON to the full path of python.exe.
echo.
pause
exit /b 1
