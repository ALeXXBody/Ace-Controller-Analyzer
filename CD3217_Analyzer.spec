# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for CD3217B12 Analyzer

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'customtkinter',
        'cd3217_analyzer',
        'cd3217_analyzer.registers',
        'cd3217_analyzer.analyzer',
        'cd3217_analyzer.adapters',
        'cd3217_analyzer.report',
        'cd3217_analyzer.models',
        'cd3217_analyzer.otp',
        'cd3217_analyzer.cli',
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
    a.binaries,
    a.datas,
    [],
    name='CD3217B12_Analyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add icon='app.ico' if you have one
)
