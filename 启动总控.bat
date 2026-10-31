@echo off
chcp 65001 >nul 2>&1
REM Jarvis console launcher. Double-click to start the GUI.
REM Uses the batch file's own location so no hard-coded Chinese path is needed.

cd /d "%~dp0"
if errorlevel 1 (
    echo Failed to locate the project directory.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo .venv\Scripts\python.exe not found. Please install dependencies per README first.
    pause
    exit /b 1
)

echo Starting Jarvis console (Xiao Wang) ...
.venv\Scripts\python.exe -m jarvis_console

echo.
echo Console exited with code %errorlevel%.
pause
