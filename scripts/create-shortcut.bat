@echo off
setlocal
:: scripts\create-shortcut.bat -- generate TextWhisper.lnk and textwhisper.ico
:: in the project root, pointing the shortcut at venv\Scripts\pythonw.exe.

cd /d "%~dp0\.."

if not exist "venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run scripts\setup.bat first.
    exit /b 1
)

call venv\Scripts\activate.bat
python scripts\create-shortcut.py
if errorlevel 1 (
    echo [ERROR] Shortcut creation failed.
    exit /b 1
)

endlocal
