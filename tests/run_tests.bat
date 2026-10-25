@echo off
rem Run all regression tests.
rem NOTE: the tests need a Python with tkinter (the official installer has it).
rem Interpreter is auto-detected; override with WB_PYTHON if needed.
setlocal
set "WB_PY="
call "%~dp0..\_findpy.bat"
if not defined WB_PY (
  echo Python not found. Install Python 3.9+ from python.org, or set WB_PYTHON.
  pause
  exit /b 1
)
rem 从仓库根目录跑（%~dp0 是 tests\，上一级才是根）
pushd "%~dp0.."

"%WB_PY%" -c "import tkinter" 2>nul
if errorlevel 1 (
  echo [WARN] this Python has no tkinter: %WB_PY%
  echo        GUI tests below will fail. Use the official python.org build.
  echo.
)

echo ==== 1. window-based WorkBuddy detection (lingering helpers) ====
"%WB_PY%" tests\test_window_detection.py || goto :fail
echo.
echo ==== 2. watcher decision logic (fast) ====
"%WB_PY%" tests\test_watch_logic.py || goto :fail
echo.
echo ==== 3. pid file identity check (PID reuse regression) ====
"%WB_PY%" tests\test_pid_identity.py || goto :fail
echo.
echo ==== 4. widget lifecycle (isolated copy) ====
"%WB_PY%" tests\test_lifecycle.py || goto :fail
echo.
echo ==== 5. right-click menu smoke ====
"%WB_PY%" tests\test_menu_smoke.py || goto :fail
echo.
echo ==== 6. autostart entry sanity (parses the .vbs) ====
"%WB_PY%" tests\check_autostart_vbs.py || goto :fail
echo.
echo ==== 7. all .vbs files: structure / Option Explicit / refs ====
"%WB_PY%" tests\check_vbs_syntax.py || goto :fail
echo.
echo ==== 8. no secrets / personal paths in the repo ====
"%WB_PY%" tests\check_no_secrets.py || goto :fail
echo.
echo ==== 9. full pipeline end-to-end (real processes, ~1 min) ====
"%WB_PY%" tests\test_pipeline_e2e.py || goto :fail
echo.
echo ==== 10. stale pid file recovery (the real crash: PID reuse) ====
"%WB_PY%" tests\test_stale_pid_recovery.py || goto :fail
echo.
echo ==== 11. real-window full flow (window gone, process alive, widget must exit) ====
"%WB_PY%" tests\test_real_window_flow.py || goto :fail
echo.
popd
echo ALL TESTS PASSED
pause
exit /b 0

:fail
echo.
echo SOME TESTS FAILED
popd
pause
exit /b 1
