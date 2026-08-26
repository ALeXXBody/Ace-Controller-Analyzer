@echo off
REM Build CD3217B12 Analyzer .exe with PyInstaller
REM Run this on Windows after installing dependencies:
REM   pip install pyinstaller customtkinter

echo ========================================
echo  Building CD3217B12 Analyzer .exe
echo ========================================

REM Clean previous builds
if dist rmdir /s /q dist
if build rmdir /s /q build

REM Build
pyinstaller CD3217_Analyzer.spec --clean --noconfirm

echo.
echo ========================================
echo  Build complete!
echo  Output: dist\CD3217B12_Analyzer.exe
echo ========================================
pause
