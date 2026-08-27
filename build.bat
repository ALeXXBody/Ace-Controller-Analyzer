@echo off
title Build CD3217B12 Analyzer .exe
color 0A

echo.
echo  ========================================
echo   Build Standalone .exe
echo  ========================================
echo.

REM === Check Python ===
python --version >nul 2>&1
if errorlevel 1 (
    echo  [!] Python not found. Install Python 3.8+ from python.org
    pause
    exit /b 1
)

REM === Check/install build tools ===
echo  [1/4] Installing build tools...
pip install pyinstaller customtkinter smbus2 pyserial --quiet --disable-pip-version-check 2>nul

REM === Clean previous builds ===
echo  [2/4] Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM === Build .exe ===
echo  [3/4] Building .exe (this may take 1-2 minutes)...
echo.
pyinstaller ^
    --name "CD3217B12_Analyzer" ^
    --windowed ^
    --noconfirm ^
    --clean ^
    --hidden-import customtkinter ^
    --hidden-import cd3217_analyzer ^
    --hidden-import cd3217_analyzer.registers ^
    --hidden-import cd3217_analyzer.analyzer ^
    --hidden-import cd3217_analyzer.adapters ^
    --hidden-import cd3217_analyzer.report ^
    --hidden-import cd3217_analyzer.models ^
    --hidden-import cd3217_analyzer.otp ^
    --hidden-import cd3217_analyzer.spi_adapter ^
    --hidden-import cd3217_analyzer.flash ^
    --hidden-import cd3217_analyzer.flash_board ^
    --hidden-import cd3217_analyzer.usb_bridge ^
    --hidden-import cd3217_analyzer.cli ^
    --hidden-import serial ^
    --hidden-import serial.tools ^
    --hidden-import serial.tools.list_ports ^
    gui.py

if errorlevel 1 (
    echo.
    echo  [!] Build failed! Check the error messages above.
    pause
    exit /b 1
)

REM === Create portable folder ===
echo  [4/4] Creating portable package...
if exist "CD3217B12_Portable" rmdir /s /q "CD3217B12_Portable"
mkdir "CD3217B12_Portable"
copy dist\CD3217B12_Analyzer.exe "CD3217B12_Portable\" >nul

REM Create launcher for portable folder
(
echo @echo off
echo title CD3217B12 Analyzer
echo cd /d "%%~dp0"
echo start CD3217B12_Analyzer.exe
echo) > "CD3217B12_Portable\Run.bat"

echo.
echo  ========================================
echo   BUILD COMPLETE!
echo  ========================================
echo.
echo  Output: CD3217B12_Portable\
echo    - CD3217B12_Analyzer.exe   (standalone, no Python needed)
echo    - Run.bat                  (double-click launcher)
echo.
echo  You can copy the CD3217B12_Portable folder
echo  to any Windows machine and run it directly.
echo.
pause
