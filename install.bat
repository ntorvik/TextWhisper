@echo off
setlocal EnableDelayedExpansion
:: install.bat -- one-click installer for TextWhisper.
::
:: Steps:
::   1. Create the Python venv and install runtime dependencies.
::   2. Auto-detect an NVIDIA GPU and install pip-managed CUDA runtime if so.
::   3. Generate TextWhisper.lnk + textwhisper.ico in this folder.
::
:: After this finishes, double-click TextWhisper.lnk to launch.

cd /d "%~dp0"

echo.
echo ===========================================================
echo                  TextWhisper Installer
echo ===========================================================
echo.

echo [1/3] Setting up Python virtual environment + dependencies...
call scripts\setup.bat
if errorlevel 1 (
    echo.
    echo [FAIL] Python setup did not complete. See messages above.
    pause
    exit /b 1
)
echo       ...done.
echo.

echo [2/3] Detecting NVIDIA GPU...
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    echo       NVIDIA GPU detected. Installing CUDA 12 runtime libraries...
    call scripts\setup-cuda.bat
    if errorlevel 1 (
        echo       [WARN] CUDA library install failed. App will fall back to CPU mode.
    ) else (
        echo       ...done.
    )
) else (
    echo       No NVIDIA GPU detected. Skipping CUDA runtime install.
    echo       (App will run in CPU mode. You can re-run install.bat after
    echo        adding a GPU + drivers.)
)
echo.

echo [3/3] Creating Windows shortcut + icon...
call scripts\create-shortcut.bat
if errorlevel 1 (
    echo       [WARN] Shortcut creation failed. You can still run the app
    echo              with run.bat.
) else (
    echo       ...done.
)
echo.

echo ===========================================================
echo                Installation complete.
echo ===========================================================
echo.
echo To launch TextWhisper, double-click:    TextWhisper.lnk
echo To pin it to your taskbar:              right-click TextWhisper.lnk
echo                                         -^> Show more options -^> Pin to taskbar
echo.
echo The first launch downloads the Whisper model (~1.5 GB) into the
echo Hugging Face cache. Subsequent launches load in seconds.
echo.
pause
endlocal
