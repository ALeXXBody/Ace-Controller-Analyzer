@echo off
title Build CD3217B12 Analyzer .exe
color 0A

echo.
echo  ========================================
echo   Build Standalone .exe (windowed, no console)
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
pip install pyinstaller customtkinter smbus2 pyserial pillow --quiet --disable-pip-version-check 2>nul

REM === Clean previous builds ===
echo  [2/4] Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

REM === Build .exe (windowed + icon; no console shadow) ===
echo  [3/4] Building .exe (this may take 1-2 minutes)...
echo.
pyinstaller ^
    --name "CD3217B12_Analyzer" ^
    --onedir ^
    --windowed ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    --noconfirm ^
    --clean ^
    --collect-all customtkinter ^
    --collect-all cd3217_analyzer ^
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
    --hidden-import cd3217_analyzer.usb_bridge
          --hidden-import cd3217_analyzer.spi_bridge ^
    --hidden-import cd3217_analyzer.cli ^
    --hidden-import serial ^
    --hidden-import serial.tools ^
    --hidden-import serial.tools.list_ports ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    gui.py

if errorlevel 1 (
    echo.
    echo  [!] Build failed! Check the error messages above.
    pause
    exit /b 1
)

REM === Create portable zip (no Run.bat — the exe is launched directly) ===
echo  [4/4] Creating portable package...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\CD3217B12_Analyzer' -DestinationPath 'CD3217B12_Portable.zip' -Force"

echo.
echo  ========================================
echo   BUILD COMPLETE!
echo  ========================================
echo.
echo  Output:
echo    dist\CD3217B12_Analyzer\CD3217B12_Analyzer.exe   (run directly)
echo    CD3217B12_Portable.zip                           (portable package)
echo.
echo  Optional installer (requires Inno Setup 6):
echo    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
echo.
pause
