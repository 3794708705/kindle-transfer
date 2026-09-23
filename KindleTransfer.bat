@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title KindleTransfer

rem ============================================================
rem  Locate a usable Python interpreter.
rem  NOTE: "python" on PATH may be a 0-byte Microsoft Store stub,
rem        so full paths are preferred here.
rem
rem  This file is intentionally ASCII-only: cmd.exe decodes a .bat
rem  with whatever code page is active when it starts reading, so
rem  non-ASCII text here renders differently depending on the
rem  terminal. ASCII is safe everywhere. The application itself
rem  is still fully Chinese.
rem ============================================================
set "PY="

rem 1) project-local virtualenv
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

rem 2) the interpreter this project was set up with
if not defined PY if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"

rem 3) uv-managed CPython
if not defined PY (
  for /d %%d in ("%APPDATA%\uv\python\cpython-3*") do (
    if not defined PY if exist "%%d\python.exe" set "PY=%%d\python.exe"
  )
)

rem 4) last resort
if not defined PY set "PY=python"

rem verify it actually runs (the Store stub fails here)
"%PY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto no_python

rem ============================================================
rem  Skip the menu when given an argument:
rem      KindleTransfer.bat app
rem      KindleTransfer.bat test
rem ============================================================
if /i "%~1"=="app"  goto run_app
if /i "%~1"=="1"    goto run_app
if /i "%~1"=="test" goto run_tests
if /i "%~1"=="2"    goto run_tests

:menu
cls
echo.
echo    ==============================================
echo                 KindleTransfer
echo    ==============================================
echo.
echo         [1]   Start program
echo         [2]   Run tests
echo         [0]   Exit
echo.
echo    ----------------------------------------------
echo         Python: %PY%
echo    ----------------------------------------------
echo.
set "CH="
set /p "CH=    Select [1/2/0]: "

rem exit on empty input so a redirected stdin cannot loop forever
if "%CH%"=="" goto done
if "%CH%"=="1" goto run_app
if "%CH%"=="2" goto run_tests
if "%CH%"=="0" goto done
goto menu


:run_app
cls
echo.
echo    Starting KindleTransfer ...
echo.
"%PY%" main.py
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo    [ERROR] Program exited with code %RC%
  echo    See logs\kindle_transfer.log for details.
  echo.
  pause
)
goto done


:run_tests
cls
echo.
echo    Running tests ...
echo.
"%PY%" -m pytest tests\ -q
echo.
pause
goto menu


:no_python
cls
echo.
echo    [ERROR] No usable Python interpreter found.
echo.
echo    Tried:
echo      %~dp0.venv\Scripts\python.exe
echo      C:\Python314\python.exe
echo      %APPDATA%\uv\python\cpython-3*
echo      python   (from PATH)
echo.
echo    Note: if "python" opens the Microsoft Store, that is a 0-byte
echo          placeholder, not a real interpreter. Install Python first.
echo.
pause


:done
endlocal
