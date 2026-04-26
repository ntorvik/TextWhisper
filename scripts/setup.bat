@echo off
setlocal
:: scripts\setup.bat -- create venv and install Python dependencies.
:: Run from anywhere; the script navigates to the project root itself.

cd /d "%~dp0\.."

where py >nul 2>&1
if not errorlevel 1 (
    set "PY=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Neither "py" nor "python" is on PATH. Install Python 3.11+ first.
        echo Download: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PY=python"
)

if not exist "venv" (
    echo Creating virtual environment...
    %PY% -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv with: %PY%
        pause
        exit /b 1
    )
)

call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate venv.
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed.
    pause
    exit /b 1
)

endlocal
