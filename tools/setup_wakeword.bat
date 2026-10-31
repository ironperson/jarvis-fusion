@echo off
REM ============================================================
REM  One-click setup for the "Hey Jarvis" wake word.
REM  What it does: download KWS model -> extract -> keywords.txt
REM                -> load self-test (same path as the app).
REM
REM  Usage:
REM    double-click this file, or:
REM    setup_wakeword.bat --mirror
REM    setup_wakeword.bat --archive D:\kws.tar.bz2
REM    setup_wakeword.bat --record 6
REM ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo Using Python: %PY%
echo.
"%PY%" tools\kws_setup.py %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo [FAILED] exit code = %RC%
echo Press any key to close this window...
pause >nul
exit /b %RC%
