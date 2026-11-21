# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ForenziQ – builds ONE standalone .exe (onefile) that needs
no installed Python, packages or additional folders.

Build:
    python -m PyInstaller ForenziQ.spec --noconfirm

Output:  dist/ForenziQ.exe   (a single file, everything inside)
Distribution: give the user just ForenziQ.exe.
"""
import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Packages with their own data/assets or many submodules –
# collect_all pulls them in completely (otherwise PyInstaller misses parts).
for pkg in ("customtkinter", "tkintermapview", "pymobiledevice3", "paramiko"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Bundle platform-tools (adb.exe, fastboot.exe, DLLs) under "adb/" – the adb/ folder is local only (not in git).
if os.path.isdir("adb"):
    datas += [("adb", "adb")]

# Assets folder (window icon + brand icons of backup formats) – loaded at run time.
if os.path.isdir("assets"):
    datas += [("assets", "assets")]

# Helper packages that tend to be imported dynamically.
hiddenimports += [
    "PIL._tkinter_finder",
    "reportlab.graphics.barcode",
    "core.s30.report",          # S30+ PDF report – imported at run time
    "core.ios_backup_crypto",   # decryption of encrypted iOS backups – imported at run time
    "cryptography.hazmat.primitives.keywrap",
    "cryptography.hazmat.primitives.ciphers",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "PyInstaller"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ForenziQ",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI application – no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/forenziq.ico",
)
