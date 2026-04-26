@echo off
setlocal
:: run.bat -- launch TextWhisper from the project venv (developer / dev-loop use).
:: Most users should double-click TextWhisper.lnk instead — it has no console
:: window and looks like a real app. This script is for developers who want
:: to see stdout / log output in a terminal.

cd /d "%~dp0"

if not exist "venv\Scripts\activate.bat" (
    echo.
    echo [ERROR] venv not found at "%cd%\venv".
    echo Run install.bat first.
    echo.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate venv.
    pause
    exit /b 1
)

if not exist "logs" mkdir "logs"
echo Starting TextWhisper... (log: logs\textwhisper.log)
python main.py
set EXIT_CODE=%errorlevel%

if not "%EXIT_CODE%"=="0" (
    echo.
    echo TextWhisper exited with error code %EXIT_CODE%.
    echo See logs\textwhisper.log for details.
    pause
)
endlocal
exit /b %EXIT_CODE%
