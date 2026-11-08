# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec pro ForenziQ – vytvoří samostatnou aplikaci (onedir),
která nevyžaduje nainstalovaný Python ani žádné balíčky.

Sestavení:
    python -m PyInstaller ForenziQ.spec --noconfirm

Výstup:  dist/ForenziQ/ForenziQ.exe  (+ složka _internal)
Distribuce: zazipovat celou složku dist/ForenziQ a předat uživateli.
"""
import os
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

# Balíčky, které mají vlastní data/assets nebo mnoho podmodulů –
# collect_all je stáhne kompletně (jinak PyInstaller část vynechá).
for pkg in ("customtkinter", "tkintermapview", "pymobiledevice3"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Přibal platform-tools (adb.exe, fastboot.exe, DLL) do balíčku pod "adb/".
if os.path.isdir("adb"):
    datas += [("adb", "adb")]

# Pomocné balíčky, které bývají importovány dynamicky.
hiddenimports += [
    "PIL._tkinter_finder",
    "reportlab.graphics.barcode",
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
    [],
    exclude_binaries=True,
    name="ForenziQ",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI aplikace – bez konzolového okna
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ForenziQ",
)
