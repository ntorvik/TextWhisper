@echo off
setlocal
:: scripts\setup-cuda.bat -- install pip-managed CUDA 12 runtime DLLs into the
:: project venv so faster-whisper can find cuBLAS + cuDNN without a system-wide
:: CUDA install.

cd /d "%~dp0\.."

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] venv not found. Run scripts\setup.bat first.
    exit /b 1
)

call venv\Scripts\activate.bat

echo Installing pip-managed CUDA 12 runtime libraries (cuBLAS + cuDNN 9)...
python -m pip install --upgrade nvidia-cublas-cu12 nvidia-cudnn-cu12
if errorlevel 1 (
    echo [ERROR] Failed to install CUDA libraries.
    exit /b 1
)

endlocal
