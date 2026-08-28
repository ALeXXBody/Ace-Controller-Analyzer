# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for CD3217B12 Analyzer
#
# NOTE: CI (.github/workflows/build.yml) and build.bat build with equivalent
# command-line flags — keep this file in sync when modules are added.

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=[
        'customtkinter',
        'cd3217_analyzer',
        'cd3217_analyzer.registers',
        'cd3217_analyzer.analyzer',
        'cd3217_analyzer.adapters',
        'cd3217_analyzer.report',
        'cd3217_analyzer.models',
        'cd3217_analyzer.otp',
        'cd3217_analyzer.spi_adapter',
        'cd3217_analyzer.spi_bridge',
        'cd3217_analyzer.flash',
        'cd3217_analyzer.flash_board',
        'cd3217_analyzer.usb_bridge',
        'cd3217_analyzer.cli',
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        'PIL',
        'PIL.Image',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CD3217B12_Analyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed — no console shadow
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name='CD3217B12_Analyzer',
)
