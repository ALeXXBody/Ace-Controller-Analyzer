@echo off
title CD3217B12 Analyzer - Setup
color 0B

echo.
echo  ========================================
echo   CD3217B12 Analyzer - First Time Setup
echo  ========================================
echo.

REM === Check Python ===
python --version >nul 2>&1
if errorlevel 1 (
    echo  Python is not installed on your system.
    echo.
    echo  To use this tool, you need Python 3.8 or newer.
    echo.
    echo  Steps:
    echo    1. Go to https://www.python.org/downloads/
    echo    2. Download Python 3.8+ (click the big yellow button)
    echo    3. Run the installer
    echo    4. CHECK THE BOX "Add Python to PATH" (very important!)
    echo    5. Click "Install Now"
    echo    6. Run this setup script again
    echo.
    echo  After Python is installed, this script will automatically:
    echo    - Install required packages (customtkinter, smbus2)
    echo    - Create a desktop shortcut
    echo    - Launch the application
    echo.
    pause
    exit /b 1
)

echo  [1/3] Python found: 
python --version
echo.

echo  [2/3] Installing packages...
pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo  [!] Package installation failed.
    echo  Try running as Administrator, or:
    echo      pip install -r requirements.txt
    pause
    exit /b 1
)
echo  [+] Packages installed!
echo.

REM === Create desktop shortcut ===
echo  [3/3] Creating desktop shortcut...

set "SCRIPT_DIR=%~dp0"
set "SHORTCUT_PATH=%USERPROFILE%\Desktop\CD3217B12 Analyzer.bat"

(
echo @echo off
echo cd /d "%SCRIPT_DIR%"
echo call CD3217_Analyzer.bat
) > "%SHORTCUT_PATH%"

echo  [+] Desktop shortcut created!
echo.

echo  ========================================
echo   SETUP COMPLETE!
echo  ========================================
echo.
echo  You can now:
echo    - Double-click "CD3217B12 Analyzer" on your Desktop
echo    - Or run CD3217_Analyzer.bat from this folder
echo.
pause

REM === Launch the app ===
cd /d "%~dp0"
pythonw gui.py 2>nul
if errorlevel 1 (
    python gui.py
)
