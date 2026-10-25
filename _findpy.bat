@echo off
rem Locate a Python that can actually run this app, and expose it as %WB_PY% in the CALLER.
rem   usage:  call "%~dp0_findpy.bat"
rem
rem No setlocal here on purpose - the whole point is to leak %WB_PY% upward.
rem
rem Two things learned the hard way:
rem
rem 1) Do NOT name the result %PY%. Some environments already define PY
rem    (this machine does, pointing at a stripped "managed" Python), and a
rem    "if defined PY exit" guard would then silently hand you the wrong one.
rem
rem 2) Do not just take the first python.exe on PATH. Stripped Pythons without
rem    tkinter are common and frequently come first; the widget would die
rem    instantly with no visible error. So every candidate is probed with
rem    `-c "import tkinter"` and the first one that passes wins.
rem
rem   override:  set WB_PYTHON=<full path to python.exe>

if defined WB_PY exit /b 0

if defined WB_PYTHON call :wb_try "%WB_PYTHON%"
if defined WB_PY exit /b 0

for /f "delims=" %%i in ('where python.exe 2^>nul') do call :wb_try "%%i"
if defined WB_PY exit /b 0

for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :wb_try "%%d\python.exe"
if defined WB_PY exit /b 0

for /d %%d in ("%ProgramFiles%\Python3*") do call :wb_try "%%d\python.exe"
if defined WB_PY exit /b 0

for /d %%d in ("%ProgramFiles%\Python\Python3*") do call :wb_try "%%d\python.exe"
exit /b 0


:wb_try
if defined WB_PY exit /b 0
if "%~1"=="" exit /b 0
if not exist "%~1" exit /b 0
rem Microsoft Store stub lives in WindowsApps and opens the Store instead of running.
rem Pure-batch substring test: calling `find` would hit Git Bash's find.exe
rem if the user has MSYS/Git on PATH, which just prints errors and does nothing useful.
set "WB_CAND=%~1"
if not "%WB_CAND%"=="%WB_CAND:\WindowsApps\=%" exit /b 0
"%~1" -c "import tkinter" >nul 2>nul
if errorlevel 1 exit /b 0
set "WB_PY=%~1"
exit /b 0
