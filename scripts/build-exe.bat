@echo off
setlocal
:: scripts\build-exe.bat -- builds dist\TextWhisper\TextWhisper.exe via PyInstaller.
:: Output is a one-folder distribution (~250-500 MB) ready to zip and attach
:: to a GitHub Release. Users who download it can run TextWhisper.exe directly
:: with no Python install required.

cd /d "%~dp0\.."

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] venv not found. Run install.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

:: Make sure pyinstaller is available in the venv.
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller into the venv...
    python -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller install failed.
        pause
        exit /b 1
    )
)

:: Make sure the icon exists (build will use it if present).
if not exist "textwhisper.ico" (
    echo Generating textwhisper.ico ...
    python scripts\create-shortcut.py
)

echo.
echo Building TextWhisper.exe (this can take 5-10 minutes)...
echo.
python -m PyInstaller packaging\textwhisper.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed. See output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete.
echo ============================================================
echo  Output: dist\TextWhisper\TextWhisper.exe
echo.
echo  To distribute: zip the entire dist\TextWhisper\ folder and
echo  attach it to a GitHub Release. Users unzip + double-click
echo  TextWhisper.exe — no Python install required.
echo ============================================================
echo.
pause
endlocal
