# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec untuk UT-Bot.

Membundel kode + driver Playwright. Chromium TIDAK dibundel (ukuran besar);
sebagai gantinya, browser_setup.ensure_chromium() mengunduhnya otomatis ke
folder ms-playwright milik user saat pertama kali dijalankan.

Build:
    pyinstaller ut_bot.spec
Hasil:
    dist/UT-Bot.exe
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Sertakan file data & submodule Playwright (driver Node, dll).
playwright_datas = collect_data_files("playwright")
playwright_hidden = collect_submodules("playwright")

# Sertakan file data google-genai bila ada.
genai_datas = collect_data_files("google.genai", include_py_files=False)

datas = playwright_datas + genai_datas
hiddenimports = playwright_hidden + ["google.genai", "dotenv", "rate_limiter", "browser_setup", "auth", "evaluator"]


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name="UT-Bot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,       # tampilkan console (aplikasi ini interaktif via terminal)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
