@echo off
title CD3217B12 Analyzer
color 0B

echo.
echo  ========================================
echo   CD3217B12 (Apple ACE2) I2C Analyzer
echo  ========================================
echo.

REM === Check Python ===
python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python not found.
    echo.
    echo  Python 3.8+ is required to run this application.
    echo  Download from: https://www.python.org/downloads/
    echo.
    echo  IMPORTANT: Check "Add Python to PATH" during installation!
    echo.
    echo  After installing Python, run this script again.
    echo.
    pause
    exit /b 1
)

REM === Check/install dependencies (only once) ===
if not exist ".deps_installed" (
    echo  [*] First run - installing dependencies...
    echo.
    pip install customtkinter smbus2 pyftdi --quiet --disable-pip-version-check
    if errorlevel 1 (
        echo  [!] Failed to install dependencies.
        echo  Try running: pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo. > .deps_installed
    echo  [+] Dependencies installed successfully!
    echo.
)

REM === Launch GUI ===
echo  Starting CD3217B12 Analyzer...
echo.
pythonw gui.py 2>nul
if errorlevel 1 (
    python gui.py 2>nul
)
