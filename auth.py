"""
auth.py
Helper autentikasi UT Moodle dengan SESI TERSIMPAN (storage state).

Bot tidak menyimpan username/password. Login dilakukan manual sekali; sesi
disimpan ke session.json sehingga login berikutnya otomatis. Tersedia juga
fungsi logout / hapus sesi untuk berganti akun.
"""

import os

from playwright.sync_api import Page

LOGIN_URL = "https://elearning.ut.ac.id/login/index.php"
LOGOUT_URL = "https://elearning.ut.ac.id/login/logout.php"
HOME_URL = "https://elearning.ut.ac.id/my/"

SESSION_FILE = "session.json"


# --------------------------------------------------------------------------- #
# Storage state (sesi tersimpan)
# --------------------------------------------------------------------------- #
def session_exists(path: str = SESSION_FILE) -> bool:
    """True jika file sesi tersimpan tersedia."""
    return os.path.isfile(path)


def save_session(context, path: str = SESSION_FILE) -> None:
    """Menyimpan storage state (cookies/localStorage) ke file."""
    try:
        context.storage_state(path=path)
        print(f"[AUTH] Sesi disimpan ke '{path}'.")
    except Exception as exc:
        print(f"[AUTH] Gagal menyimpan sesi: {exc}")


def clear_session(path: str = SESSION_FILE) -> None:
    """Menghapus file sesi tersimpan (untuk ganti akun / logout penuh)."""
    try:
        if os.path.isfile(path):
            os.remove(path)
            print(f"[AUTH] Sesi tersimpan '{path}' dihapus.")
    except Exception as exc:
        print(f"[AUTH] Gagal menghapus sesi: {exc}")


# --------------------------------------------------------------------------- #
# Deteksi status login
# --------------------------------------------------------------------------- #
def is_logged_in(page: Page) -> bool:
    """
    Mengecek apakah sesi pengguna sudah aktif.
    Moodle menambahkan class body 'userloggedin' saat pengguna login.
    """
    try:
        body_class = page.locator("body").get_attribute("class") or ""
        if "userloggedin" in body_class:
            return True
    except Exception:
        pass

    indicators = [
        ".usermenu",
        "a[href*='logout']",
        "#user-menu-toggle",
        ".userinitials",
    ]
    for selector in indicators:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue

    return False


def verify_session(page: Page) -> bool:
    """Membuka dashboard UT dan memverifikasi apakah sesi tersimpan masih valid."""
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded")
    except Exception as exc:
        print(f"[AUTH] Gagal membuka dashboard: {exc}")
        return False
    return is_logged_in(page)


# --------------------------------------------------------------------------- #
# Login & logout
# --------------------------------------------------------------------------- #
def wait_for_manual_login(page: Page) -> bool:
    """
    Membuka halaman login UT dan menunggu pengguna login secara manual.

    Returns:
        True jika sesi terdeteksi aktif.
    """
    print("[AUTH] Membuka halaman login UT...")
    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
    except Exception as exc:
        print(f"[AUTH] Gagal membuka halaman login: {exc}")

    print("\n" + "=" * 70)
    print("[AUTH] Silakan LOGIN secara MANUAL pada jendela browser yang terbuka.")
    print("[AUTH] Setelah berhasil masuk ke dashboard UT, kembali ke terminal ini.")
    print("=" * 70)

    while True:
        input("[AUTH] Tekan ENTER setelah Anda selesai login... ")
        if is_logged_in(page):
            print("[AUTH] Sesi login terdeteksi. Melanjutkan.")
            return True

        print("[AUTH] Sesi login belum terdeteksi.")
        retry = input("[AUTH] Coba cek lagi? (y = cek lagi / n = batal): ").strip().lower()
        if retry == "n":
            return False


def logout(page: Page, context, clear_saved: bool = True) -> None:
    """
    Logout dari Moodle dan (opsional) menghapus sesi tersimpan.

    Digunakan saat pengguna ingin berganti akun.
    """
    print("[AUTH] Melakukan logout dari UT Moodle...")
    try:
        page.goto(LOGOUT_URL, wait_until="domcontentloaded")
    except Exception as exc:
        print(f"[AUTH] Peringatan saat logout: {exc}")

    # Bersihkan cookies dari context agar benar-benar keluar
    try:
        context.clear_cookies()
    except Exception:
        pass

    if clear_saved:
        clear_session()

    print("[AUTH] Logout selesai.")
