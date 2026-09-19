"""
browser_setup.py
Memastikan Playwright memakai Chromium dari lokasi PERSISTEN (bukan dari dalam
bundel PyInstaller yang bersifat sementara/kosong).

WAJIB diimpor PALING AWAL di main.py, sebelum modul playwright dipakai, karena
variabel lingkungan PLAYWRIGHT_BROWSERS_PATH harus diset sebelum Playwright start.

Masalah yang diatasi:
    Saat aplikasi dibungkus menjadi .exe dengan PyInstaller, Chromium tidak ikut
    terbundel, sehingga Playwright mencarinya di folder temp _MEIxxxx dan gagal:
    "Executable doesn't exist at ...\\.local-browsers\\chromium-xxxx\\chrome.exe".

Solusi:
    Arahkan Playwright ke folder ms-playwright milik user (persisten antar-run),
    dan bila Chromium belum ada, unduh otomatis satu kali.
"""

import os
import subprocess
import sys
from pathlib import Path


def _browsers_dir() -> Path:
    """Folder persisten untuk menyimpan browser Playwright (per-user)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "ms-playwright"


def ensure_browsers_path() -> Path:
    """
    Set PLAYWRIGHT_BROWSERS_PATH ke folder persisten dan kembalikan path-nya.
    Harus dipanggil sebelum import/penggunaan playwright.
    """
    browsers_dir = _browsers_dir()
    browsers_dir.mkdir(parents=True, exist_ok=True)
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_dir)
    return browsers_dir


def _chromium_installed(browsers_dir: Path) -> bool:
    """Cek apakah ada folder chromium-* berisi chrome.exe di lokasi persisten."""
    if not browsers_dir.exists():
        return False
    for child in browsers_dir.glob("chromium-*"):
        # Struktur umum: chromium-XXXX/chrome-win64/chrome.exe
        for exe in child.rglob("chrome.exe"):
            if exe.is_file():
                return True
        for exe in child.rglob("headless_shell.exe"):
            if exe.is_file():
                return True
    return False


def ensure_chromium() -> None:
    """
    Pastikan Chromium tersedia di lokasi persisten. Bila belum, unduh otomatis.
    Aman dipanggil baik dari skrip Python maupun dari .exe hasil PyInstaller.
    """
    browsers_dir = ensure_browsers_path()

    if _chromium_installed(browsers_dir):
        return

    print("[SETUP] Chromium untuk Playwright belum ada. Mengunduh (sekali saja)...")

    # Saat berjalan sebagai .exe (frozen), 'python -m playwright' tidak tersedia.
    # Gunakan modul playwright langsung via API-nya.
    try:
        if getattr(sys, "frozen", False):
            # Jalankan installer Playwright dari dalam proses yang sama.
            from playwright.__main__ import main as playwright_main
            argv_backup = sys.argv[:]
            sys.argv = ["playwright", "install", "chromium"]
            try:
                playwright_main()
            except SystemExit:
                pass
            finally:
                sys.argv = argv_backup
        else:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True,
            )
    except Exception as exc:
        print(f"[SETUP] Gagal mengunduh Chromium otomatis: {exc}")
        print("[SETUP] Jalankan manual di terminal: playwright install chromium")
        return

    if _chromium_installed(browsers_dir):
        print("[SETUP] Chromium siap.")
    else:
        print("[SETUP] Chromium masih belum terdeteksi setelah unduh. "
              "Coba jalankan ulang aplikasi.")
