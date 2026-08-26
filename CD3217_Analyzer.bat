@echo off
REM CD3217B12 (Apple ACE2) I2C Diagnostic Analyzer - Windows Launcher
REM Double-click this file to start the GUI

cd /d "%~dp0"

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Python not found!
    echo.
    echo Install Python 3.8+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM Check/install dependencies
echo Checking dependencies...
pip install smbus2 >nul 2>&1

REM Check for FTDI (optional)
pip show pyftdi >nul 2>&1
if errorlevel 1 (
    echo.
    echo NOTE: pyftdi not installed (needed for FTDI FT232H adapters)
    echo To install: pip install pyftdi
    echo.
)

REM Launch GUI
echo Starting CD3217B12 Analyzer...
python gui.py

if errorlevel 1 (
    echo.
    echo Application exited with an error.
    pause
)
