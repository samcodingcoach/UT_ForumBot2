"""
main.py
Entry point bot Auto-Grader & Auto-Reply forum diskusi UT Moodle (INTERAKTIF).

Flow:
  1. Login: pakai sesi tersimpan (session.json) bila valid; jika tidak, login
     MANUAL di browser lalu sesi disimpan. Hanya Gemini yang tertanam di .env.
  2. Pengguna memasukkan link halaman forum/thread.
  3. App menampilkan daftar post/pertanyaan mahasiswa.
  4. Pengguna memilih: nomor tertentu, "semua", refresh, ganti link,
     ganti akun/logout, atau keluar.
  5. AI menjawab yang dipilih (set nilai + tulis balasan).
  6. List di-refresh, kembali ke langkah pemilihan.
"""

import os
import re
import sys
import time

# Paksa output console ke UTF-8 agar karakter box/emoji tidak crash di Windows
# (cp1252) saat dijalankan sebagai .exe.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# PENTING: set lokasi browser Playwright SEBELUM import playwright.
from browser_setup import ensure_browsers_path
ensure_browsers_path()

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeoutError

from auth import (
    wait_for_manual_login,
    save_session,
    clear_session,
    session_exists,
    verify_session,
    logout,
    SESSION_FILE,
)
from evaluator import Evaluator

from pathlib import Path


def _app_dir() -> Path:
    """
    Folder aplikasi: lokasi .exe (saat frozen oleh PyInstaller) atau folder
    skrip main.py (saat dijalankan sebagai Python biasa). Dipakai untuk
    menemukan .env dan menyimpan session.json di tempat yang persisten.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


# --------------------------------------------------------------------------- #
# Konfigurasi
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    """Membaca konfigurasi dari .env. Hanya GEMINI_API_KEY yang wajib."""
    # Cari .env di folder tempat exe/skrip berada (bukan folder temp _MEI saat .exe).
    load_dotenv(dotenv_path=_app_dir() / ".env")

    config = {
        "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
        "gemini_model": os.getenv("GEMINI_MODEL", "").strip(),
        "forum_question": os.getenv("FORUM_QUESTION", "").strip(),
        "auto_submit": os.getenv("AUTO_SUBMIT", "false").strip().lower() == "true",
    }

    if not config["gemini_api_key"] or config["gemini_api_key"].startswith("your_"):
        print("[CONFIG] GEMINI_API_KEY belum diisi dengan benar di file .env.")
        sys.exit(1)

    return config


# --------------------------------------------------------------------------- #
# Scraping post forum
# --------------------------------------------------------------------------- #
# Selektor kontainer post, diurutkan dari yang paling spesifik.
# scrape_posts memakai selektor PERTAMA yang menghasilkan elemen, sehingga
# tidak menggabungkan elemen yang saling bersarang (penyebab duplikat).
POST_SELECTORS = [
    "div[data-region='post']",
    "article.forum-post-container",
    ".forumpost",
]
AUTHOR_SELECTORS = ".author a, a[href*='user/view.php'], .author"
CONTENT_SELECTORS = ".fullpost, .post-content-container, .message, .posting"
DATE_SELECTORS = "time, .posttime, .forumpostdate, .author time, .text-muted time"


def wait_page_ready(page: Page, settle_ms: int = 1200, timeout: int = 45000) -> None:
    """
    Memastikan halaman termuat sepenuhnya sebelum scraping. Tidak terburu-buru:

      1. Tunggu DOM siap.
      2. Tunggu jaringan idle (semua request selesai / lazy-load beres).
      3. Tunggu container post benar-benar muncul di DOM.
      4. Jeda kecil (settle) agar rendering akhir selesai.

    Semua langkah dibungkus try/except agar halaman yang lambat tidak menggagalkan bot.
    """
    # 1. DOM dasar
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass

    # 2. Jaringan idle (menangkap konten yang dimuat via JS)
    try:
        page.wait_for_load_state("networkidle", timeout=timeout)
    except Exception:
        # networkidle bisa timeout di situs yang polling terus-menerus; abaikan.
        pass

    # 3. Tunggu minimal satu container post muncul
    selector_union = ", ".join(POST_SELECTORS)
    try:
        page.wait_for_selector(selector_union, state="attached", timeout=timeout)
    except Exception:
        # Halaman mungkin memang tidak punya post (mis. daftar forum); lanjut saja.
        pass

    # 4. Jeda pengendapan agar rendering akhir & gambar/JS tuntas
    page.wait_for_timeout(settle_ms)


def get_forum_question(page: Page, fallback: str) -> str:
    """Mengambil konteks pertanyaan: dari .env bila diisi, jika tidak dari judul thread."""
    if fallback:
        return fallback
    for selector in ["h1", ".discussionname", "h3.discussionname", "title"]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0:
                text = loc.inner_text(timeout=3000).strip()
                if text:
                    return text
        except Exception:
            continue
    return "Diskusi Universitas Terbuka"


def _select_post_containers(page: Page):
    """
    Mengembalikan (locator, count) untuk kontainer post.

    Memakai selektor PERTAMA (paling spesifik) yang menghasilkan elemen, agar
    tidak menggabungkan selektor berbeda yang mengenai node bersarang/sama.
    """
    for selector in POST_SELECTORS:
        loc = page.locator(selector)
        if loc.count() > 0:
            return loc, loc.count(), selector
    return None, 0, None


def _post_key(post) -> str:
    """
    Kunci unik untuk sebuah post, dipakai untuk deduplikasi.
    Prioritas: atribut id/data-post-id dari Moodle; fallback ke potongan teks.
    """
    for attr in ("data-post-id", "data-postid", "id"):
        try:
            val = post.get_attribute(attr)
            if val:
                return f"{attr}={val}"
        except Exception:
            continue
    return ""


def _extract_post_id(post) -> str:
    """
    Mengambil ID numerik post Moodle, dipakai untuk membuka halaman balasan
    Advanced (post.php?reply=<ID>).

    Sumber (berurutan):
      1. Atribut data-post-id / data-postid.
      2. Atribut id seperti 'p127439160' -> 127439160.
      3. Link reply di dalam post yang mengandung 'reply=<ID>'.
    """
    for attr in ("data-post-id", "data-postid"):
        try:
            val = (post.get_attribute(attr) or "").strip()
            if val.isdigit():
                return val
        except Exception:
            pass

    try:
        raw_id = (post.get_attribute("id") or "").strip()
        m = re.search(r"(\d{4,})", raw_id)
        if m:
            return m.group(1)
    except Exception:
        pass

    # Cari link reply di dalam post
    try:
        links = post.locator("a[href*='reply=']")
        if links.count() > 0:
            href = links.first.get_attribute("href") or ""
            m = re.search(r"reply=(\d+)", href)
            if m:
                return m.group(1)
    except Exception:
        pass

    return ""


def _looks_like_bot_reply(text: str) -> bool:
    """
    Deteksi apakah sebuah post sebenarnya balasan bot (bukan jawaban mahasiswa).
    Balasan bot mengandung pola penilaian seperti 'NILAI:', 'BALASAN:', 'Rating:'.
    """
    low = text.lower()
    markers = ["nilai:", "balasan:", "rating:", "tanggapan / balasan", "nilai :"]
    hits = sum(1 for m in markers if m in low)
    return hits >= 2


def _get_current_rating(post) -> str:
    """
    Membaca nilai rating yang sedang terpilih pada dropdown post (jika ada).

    Returns:
        String nilai (mis. "85") jika sudah dinilai; "" jika belum / tidak ada dropdown.
    """
    for selector in ["select[name*='rating']", ".postratingmenu select", "select.postratingmenu"]:
        try:
            dropdown = post.locator(selector).first
            if dropdown.count() == 0:
                continue
            value = (dropdown.input_value() or "").strip()
            if not value:
                return ""
            # Moodle memakai placeholder "belum dinilai": "0", "-1", "-999",
            # atau nilai negatif lain. Anggap semua itu sebagai belum dinilai.
            try:
                if int(value) <= 0:
                    return ""
            except ValueError:
                # Nilai non-numerik (mis. "Rate...") -> anggap belum dinilai.
                return ""
            return value
        except Exception:
            continue
    return ""


def _get_bot_reply_text(post) -> str:
    """
    Mengembalikan teks balasan bot yang bersarang di dalam post (bila ada),
    atau string kosong bila belum ada balasan bot.
    """
    try:
        children = post.locator(".forumpost, article.forum-post-container, div[data-region='post']")
        n = children.count()
        for i in range(n):
            try:
                child_text = children.nth(i).inner_text(timeout=2000)
            except Exception:
                continue
            if _looks_like_bot_reply(child_text):
                return child_text
    except Exception:
        pass
    return ""


def _has_bot_reply(post) -> bool:
    """
    Deteksi apakah post mahasiswa ini SUDAH memiliki balasan bot.

    Moodle menyusun balasan sebagai post anak yang bersarang di dalam container
    post induk. Kita cek apakah ada sub-post di dalamnya yang isinya berformat
    balasan bot ('Rating:' + 'Tanggapan / Balasan Forum:').
    """
    return bool(_get_bot_reply_text(post))


def _extract_nilai_from_reply(reply_text: str) -> int:
    """
    Mengambil nilai dari teks balasan bot yang sudah ada, mis. 'Nilai: 85/100'
    atau 'Nilai: 85'. Mengembalikan int 0-100, atau -1 bila tidak ditemukan.
    """
    m = re.search(r"Nilai\s*[:：]?\s*(\d{1,3})", reply_text, re.IGNORECASE)
    if m:
        return max(0, min(100, int(m.group(1))))
    return -1


def scrape_posts(page: Page, skip_bot_replies: bool = True) -> list:
    """
    Mengumpulkan semua post pada halaman forum, sudah dideduplikasi.

    Args:
        skip_bot_replies: jika True, melewati post yang tampak seperti balasan bot.

    Returns:
        List of dict: {"number", "name", "text", "date", "rating", "graded",
                       "replied", "post_id", "locator"}.
    """
    posts_locator, count, used_selector = _select_post_containers(page)
    if not posts_locator:
        return []

    results = []
    seen_keys = set()
    seen_texts = set()

    for i in range(count):
        post = posts_locator.nth(i)
        text = _first_text(post, CONTENT_SELECTORS)
        if not text:
            continue

        # Lewati post yang tampak seperti balasan bot sebelumnya.
        if skip_bot_replies and _looks_like_bot_reply(text):
            continue

        # Dedup 1: berdasarkan ID post Moodle (paling andal).
        key = _post_key(post)
        if key and key in seen_keys:
            continue

        # Dedup 2: berdasarkan isi teks (menangkap kasus tanpa ID unik).
        text_key = " ".join(text.split())[:200]
        if text_key in seen_texts:
            continue

        name = _first_text(post, AUTHOR_SELECTORS) or "Mahasiswa"
        date = _first_text(post, DATE_SELECTORS)
        rating = _get_current_rating(post)
        post_id = _extract_post_id(post)
        replied = _has_bot_reply(post)

        if key:
            seen_keys.add(key)
        seen_texts.add(text_key)

        results.append({
            "number": len(results) + 1,
            "name": name.strip(),
            "text": text.strip(),
            "date": date.strip(),
            "rating": rating,          # "" = belum dinilai
            "graded": bool(rating),    # True jika sudah dinilai
            "replied": replied,        # True jika sudah ada balasan bot
            "post_id": post_id,        # untuk membuka halaman balasan Advanced
            "locator": post,
        })

    return results


def _first_text(scope, selectors: str) -> str:
    """Mengambil inner_text dari selector pertama yang cocok di dalam scope."""
    for selector in selectors.split(","):
        selector = selector.strip()
        try:
            loc = scope.locator(selector).first
            if loc.count() > 0:
                text = loc.inner_text(timeout=5000).strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


FILTER_LABELS = {
    "all": "Semua",
    "unreplied": "Belum dibalas",
    "replied": "Sudah dibalas",
    "ungraded": "Belum dinilai",
    "graded": "Sudah dinilai",
}


def apply_filter(posts: list, mode: str) -> list:
    """
    Menyaring post berdasarkan status.

    mode: "all" | "unreplied" | "replied" | "ungraded" | "graded".
    Penomoran diberikan ulang (1..n) sesuai hasil filter.
    """
    if mode == "unreplied":
        filtered = [p for p in posts if not p["replied"]]
    elif mode == "replied":
        filtered = [p for p in posts if p["replied"]]
    elif mode == "ungraded":
        filtered = [p for p in posts if not p["graded"]]
    elif mode == "graded":
        filtered = [p for p in posts if p["graded"]]
    else:
        filtered = list(posts)

    for idx, post in enumerate(filtered, start=1):
        post["number"] = idx
    return filtered


def compute_stats(posts: list) -> dict:
    """Menghitung statistik status post mahasiswa."""
    total = len(posts)
    replied = sum(1 for p in posts if p["replied"])
    graded = sum(1 for p in posts if p["graded"])
    return {
        "total": total,
        "replied": replied,
        "unreplied": total - replied,
        "graded": graded,
        "ungraded": total - graded,
    }


# --------------------------------------------------------------------------- #
# Tampilan console (rapi & clean)
# --------------------------------------------------------------------------- #
BOX_WIDTH = 78

# Jeda antar post saat memproses batch (detik). Beri waktu server & halaman siap.
DELAY_BETWEEN_POSTS = 15


def _line(char: str = "─") -> str:
    return char * BOX_WIDTH


def print_startup_banner() -> None:
    """Banner sambutan + disclaimer yang tampil saat aplikasi dibuka."""
    print()
    print("╔" + "═" * BOX_WIDTH + "╗")
    line = lambda t: print("║" + t.center(BOX_WIDTH) + "║")
    line("")
    line("U T   -   B O T")
    line("Auto-Grader & Auto-Reply Forum Diskusi UT")
    line("")
    line("TIDAK DIPERJUALBELIKAN - GRATIS")
    line("DO AT YOUR OWN RISK")
    line("")
    line("Tersedia Mode Pro. Contact Owner!!!")
    line("")
    print("╚" + "═" * BOX_WIDTH + "╝")


def print_banner(title: str) -> None:
    """Header berbingkai untuk judul bagian."""
    print()
    print("╔" + "═" * BOX_WIDTH + "╗")
    # Perataan manual (hindari center() karena spasi ganjil). Teks ASCII, aman.
    pad = BOX_WIDTH - len(title)
    left = pad // 2
    right = pad - left
    print("║" + " " * left + title + " " * right + "║")
    print("╚" + "═" * BOX_WIDTH + "╝")


def print_stats(stats: dict) -> None:
    """Panel ringkasan statistik status post."""
    print(_line())
    print(
        f"  Total post : {stats['total']:<4}"
        f"  |  Sudah dibalas : {stats['replied']:<4}"
        f"  |  Belum dibalas : {stats['unreplied']:<4}"
    )
    print(
        f"  {'':<11}"
        f"  |  Sudah dinilai : {stats['graded']:<4}"
        f"  |  Belum dinilai : {stats['ungraded']:<4}"
    )
    print(_line())


def _truncate(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def print_post_list(posts: list, filter_mode: str = "all",
                    stats: dict = None, total_all: int = None) -> None:
    """
    Menampilkan daftar post mahasiswa dengan tampilan clean:

        [ N] ✅ 92  Nama dan NIM
             📅 Tanggal
             Preview jawaban...
    """
    label = FILTER_LABELS.get(filter_mode, "Semua")

    if stats is not None:
        print_stats(stats)

    if not posts:
        if total_all:
            print(f"  Filter [{label}]: tidak ada post yang cocok.")
        else:
            print("  Tidak ada post mahasiswa yang terbaca pada halaman ini.")
            print("  Tip: buka THREAD diskusi (mod/forum/discuss.php?d=...), bukan daftar forum.")
        print(_line())
        return

    tampil = f"  Filter: [{label}]  —  menampilkan {len(posts)} post"
    if total_all is not None and total_all != len(posts):
        tampil += f" (dari {total_all})"
    print(tampil)
    print(_line())

    for post in posts:
        # Status teks berlebar tetap (rata kolom terjaga).
        reply_str = "[BALAS]" if post["replied"] else "[     ]"
        nilai = post["rating"] if post["graded"] else "-"
        nilai_str = f"Nilai:{nilai}"

        name_line = _truncate(post["name"], 55)
        tanggal = _truncate(post["date"], 60) if post["date"] else "-"
        snippet = _truncate(post["text"], BOX_WIDTH - 7)

        print(f"  [{post['number']:>2}] {reply_str} {nilai_str:<10} {name_line}")
        print(f"       Tanggal : {tanggal}")
        print(f"       {snippet}")
        print()


# --------------------------------------------------------------------------- #
# Parsing pilihan pengguna
# --------------------------------------------------------------------------- #
def parse_selection(raw: str, max_number: int) -> list:
    """
    Mengubah input pengguna menjadi daftar nomor post.

    Contoh: "1,3,5" -> [1,3,5]; "semua"/"all" -> semua nomor.
    Mengembalikan list kosong bila input tidak valid.
    """
    raw = raw.strip().lower()
    if raw in ("semua", "all", "*"):
        return list(range(1, max_number + 1))

    numbers = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if part.isdigit():
            n = int(part)
            if 1 <= n <= max_number and n not in numbers:
                numbers.append(n)
    return numbers


# --------------------------------------------------------------------------- #
# Interaksi DOM: rating + balasan
# --------------------------------------------------------------------------- #
def set_rating(page: Page, post_locator, nilai: int) -> bool:
    """
    Mengatur nilai pada dropdown rating sebuah post, lalu memastikan tersimpan.

    Moodle: memilih nilai di dropdown biasanya memicu auto-submit (onchange),
    tetapi bila ada tombol 'Rate'/'Nilai', kita klik untuk memastikan.
    """
    dropdown = None
    for selector in ["select[name*='rating']", ".postratingmenu select", "select.postratingmenu"]:
        try:
            loc = post_locator.locator(selector).first
            if loc.count() > 0:
                dropdown = loc
                break
        except Exception:
            continue

    if dropdown is None:
        print(f"  │  ! Dropdown rating tidak ditemukan (nilai {nilai}).")
        return False

    try:
        dropdown.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    try:
        dropdown.select_option(str(nilai))
    except Exception as exc:
        print(f"  │  ! Gagal memilih nilai {nilai}: {exc}")
        return False

    # Beri waktu auto-submit (onchange) memproses.
    page.wait_for_timeout(1500)

    # Bila ada tombol submit rating eksplisit, klik.
    for btn_sel in [
        "input[type='submit'][value*='Rate']",
        "input[type='submit'][name*='rating']",
        "button:has-text('Rate')",
        "input[value*='Nilai']",
    ]:
        try:
            btn = post_locator.locator(btn_sel).first
            if btn.count() > 0:
                btn.click()
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                page.wait_for_timeout(1000)
                break
        except Exception:
            continue

    # Verifikasi: baca ulang nilai terpilih.
    try:
        current = (dropdown.input_value() or "").strip()
        if current == str(nilai):
            print(f"  │  Nilai {nilai} tersimpan.")
            return True
    except Exception:
        pass

    print(f"  │  Nilai {nilai} dipilih (verifikasi tidak konklusif).")
    return True


def _open_reply_form(page: Page, post: dict) -> bool:
    """
    Membuka form balasan Advanced (halaman dengan editor Message).

    Strategi:
      1. Jika post_id diketahui, navigasi langsung ke post.php?reply=<ID>
         (Moodle akan menampilkan form lengkap dengan editor Message).
      2. Jika tidak, klik link Reply pada post lalu klik "Advanced" bila ada.
    """
    post_id = post.get("post_id", "")

    # Jalur 1: URL langsung ke form balasan.
    if post_id:
        reply_url = f"https://elearning.ut.ac.id/mod/forum/post.php?reply={post_id}"
        print(f"[REPLY] Membuka form balasan (reply={post_id})...")
        try:
            page.goto(reply_url, wait_until="domcontentloaded")
            wait_page_ready(page, settle_ms=800)
            # Konfirmasi editor Message muncul.
            if _message_editor_present(page):
                return True
        except Exception as exc:
            print(f"[REPLY] Gagal membuka URL balasan langsung: {exc}")

    # Jalur 2: klik link Reply pada post, lalu Advanced.
    reply_link = None
    for selector in ["a:has-text('Reply')", "a:has-text('Tanggapi')", "a:has-text('Balas')"]:
        try:
            loc = post["locator"].locator(selector).first
            if loc.count() > 0:
                reply_link = loc
                break
        except Exception:
            continue

    if reply_link is None:
        print("[REPLY] Link Reply/Tanggapi tidak ditemukan.")
        return False

    try:
        reply_link.click()
        wait_page_ready(page, settle_ms=800)
    except Exception as exc:
        print(f"[REPLY] Gagal klik link Reply: {exc}")
        return False

    # Klik "Advanced" / "Use advanced editor" bila ada (memunculkan editor penuh).
    for selector in [
        "a:has-text('Advanced')",
        "a:has-text('Use advanced editor')",
        "input[value*='Advanced']",
        "button:has-text('Advanced')",
    ]:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0:
                loc.click()
                wait_page_ready(page, settle_ms=800)
                break
        except Exception:
            continue

    return _message_editor_present(page)


def _message_editor_present(page: Page, timeout: int = 5000) -> bool:
    """Cek apakah editor Message (Atto/TinyMCE/textarea) ada di halaman."""
    candidates = [
        "textarea#id_message",
        "textarea[name='message[text]']",
        "textarea[name='message']",
        "textarea[name='post']",
        "textarea[id*='id_message']",
        "div[contenteditable='true']",
        "iframe#id_message_ifr",
        "iframe[id*='editor']",
        "iframe.tox-edit-area__iframe",
    ]
    for selector in candidates:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


def post_reply(page: Page, post: dict, balasan: str, auto_submit: bool) -> bool:
    """Membuka form balasan Advanced, mengisi editor Message, dan (opsional) submit."""
    if not _open_reply_form(page, post):
        print("[REPLY] Form balasan / editor Message tidak ditemukan.")
        return False

    if not _fill_editor(page, balasan):
        print("[REPLY] Editor Message tidak dapat diisi.")
        return False

    if auto_submit:
        return _submit_reply(page)

    print("[REPLY] AUTO_SUBMIT=false -> balasan siap direview (tidak dikirim otomatis).")
    return True


def _fill_editor(page: Page, balasan: str, timeout: int = 15000) -> bool:
    """
    Mengisi editor Message. Menangani berbagai jenis editor Moodle:
      1. TinyMCE via JavaScript API (paling andal untuk UT).
      2. TinyMCE iframe lama (#id_message_ifr).
      3. TinyMCE iframe modern (.tox-edit-area__iframe).
      4. Atto (div[contenteditable='true']).
      5. Textarea langsung (message[text] / message / post).
    """
    # 1. TinyMCE via JS API. UT memakai <textarea id="id_message"> yang
    #    dikelola TinyMCE, jadi setContent lewat instance-nya paling andal.
    try:
        ok = page.evaluate(
            """(text) => {
                if (window.tinymce) {
                    let ed = tinymce.get('id_message') ||
                             (tinymce.editors && tinymce.editors[0]);
                    if (ed) {
                        ed.setContent(text.replace(/\\n/g, '<br>'));
                        ed.save();  // sinkronkan ke textarea tersembunyi
                        return true;
                    }
                }
                return false;
            }""",
            balasan,
        )
        if ok:
            print("[REPLY] Balasan diisi ke editor TinyMCE (JS API).")
            return True
    except Exception:
        pass

    # 2 & 3. TinyMCE di dalam iframe (isi <body> editor).
    for frame_selector in [
        "iframe#id_message_ifr",
        "iframe.tox-edit-area__iframe",
        "iframe[id*='editor']",
        "iframe[title*='Rich']",
    ]:
        try:
            frame_el = page.locator(frame_selector).first
            if frame_el.count() == 0:
                continue
            frame = frame_el.content_frame()
            if frame is None:
                continue
            body = frame.locator("body").first
            body.wait_for(state="visible", timeout=timeout)
            body.click()
            body.fill(balasan)
            print(f"[REPLY] Balasan diisi ke editor TinyMCE (iframe '{frame_selector}').")
            return True
        except Exception:
            continue

    # 4. Atto (contenteditable langsung di halaman)
    try:
        editable = page.locator("div[contenteditable='true']").first
        if editable.count() > 0:
            editable.wait_for(state="visible", timeout=timeout)
            editable.click()
            editable.fill(balasan)
            print("[REPLY] Balasan diisi ke editor Atto.")
            return True
    except Exception:
        pass

    # 5. Textarea langsung (fallback terakhir). Isi lewat JS agar tetap jalan
    #    walau textarea disembunyikan oleh editor.
    for selector in [
        "textarea#id_message",
        "textarea[name='message[text]']",
        "textarea[name='message']",
        "textarea[name='post']",
    ]:
        try:
            ta = page.locator(selector).first
            if ta.count() == 0:
                continue
            try:
                ta.fill(balasan, timeout=3000)
            except Exception:
                # Textarea tersembunyi: set value via JS.
                ta.evaluate("(el, v) => { el.value = v; }", balasan)
            print(f"[REPLY] Balasan diisi ke textarea '{selector}'.")
            return True
        except Exception:
            continue

    return False


def _submit_reply(page: Page, timeout: int = 20000) -> bool:
    """
    Menekan tombol submit/post pada form balasan, lalu verifikasi terkirim.
    Tombol utama UT: id_submitbutton.
    """
    submit_selectors = [
        "#id_submitbutton",
        "input[id*='id_submitbutton']",
        "button[id*='id_submitbutton']",
        "input[type='submit'][value*='Post']",
        "input[type='submit'][value*='Kirim']",
        "button:has-text('Post to forum')",
        "button:has-text('Kirim ke forum')",
        "input[value*='Simpan']",
    ]
    for selector in submit_selectors:
        try:
            btn = page.locator(selector).first
            if btn.count() == 0:
                continue
            btn.scroll_into_view_if_needed(timeout=5000)
            try:
                # Tunggu navigasi setelah klik (form submit -> kembali ke thread).
                with page.expect_navigation(wait_until="domcontentloaded", timeout=timeout):
                    btn.click()
            except Exception:
                # Bila tidak ada navigasi eksplisit, tetap tunggu load.
                page.wait_for_load_state("domcontentloaded", timeout=timeout)

            page.wait_for_timeout(1500)

            # Verifikasi: sudah tidak di halaman post.php (form) lagi.
            if "mod/forum/post.php" not in page.url:
                print(f"  │  Balasan TERKIRIM (via '{selector}').")
                return True
            # Masih di form -> mungkin ada error validasi.
            print(f"  │  ! Setelah klik submit masih di form. Cek validasi editor.")
            return False
        except Exception:
            continue
    print("  │  ! Tombol submit tidak ditemukan.")
    return False


def answer_post(page: Page, evaluator: Evaluator, post: dict, forum_question: str, auto_submit: bool) -> None:
    """
    Memproses satu post sesuai status nilai & balasan:

      - Belum dinilai & belum dibalas : nilai + balas (AI).
      - Belum dinilai & sudah dibalas : HANYA set nilai (ambil dari balasan yang ada).
      - Sudah dinilai & belum dibalas : HANYA tulis balasan (AI).
      - Sudah dinilai & sudah dibalas : lewati.
    """
    print(f"\n  ┌─ [{post['number']}] {_truncate(post['name'], 60)}")

    graded = post["graded"]
    reply_text = _get_bot_reply_text(post["locator"])
    replied = bool(reply_text)
    print(f"  │  Status  : {'sudah' if graded else 'belum'} dinilai, "
          f"{'sudah' if replied else 'belum'} dibalas")

    # Kasus 4: sudah lengkap -> lewati.
    if graded and replied:
        print("  └─ Sudah dinilai & dibalas. Dilewati.")
        return

    # Kasus 2: belum dinilai TAPI sudah dibalas -> ambil nilai dari balasan.
    if not graded and replied:
        nilai = _extract_nilai_from_reply(reply_text)
        if nilai < 0:
            print("  │  ! Nilai tidak terbaca dari balasan yang ada. "
                  "Meminta nilai baru dari AI...")
            try:
                nilai = evaluator.evaluate(forum_question, post["name"], post["text"]).nilai
            except Exception as exc:
                print(f"  └─ ✗ Gagal ambil nilai dari AI: {exc}")
                return
        print(f"  │  Aksi    : set NILAI saja = {nilai} (balasan sudah ada)")
        set_rating(page, post["locator"], nilai)
        print("  └─ selesai.")
        return

    # Kasus 1 & 3: perlu balasan dari AI (dan nilai bila belum ada).
    try:
        evaluation = evaluator.evaluate(
            forum_question=forum_question,
            student_name=post["name"],
            student_text=post["text"],
        )
    except Exception as exc:
        print(f"  │  ✗ Gagal evaluasi Gemini: {exc}")
        print(f"  └─ Post DILEWATI. Coba lagi nanti (refresh 'r').")
        return

    print(f"  │  Nilai   : {evaluation.nilai} ({evaluation.rating_label})")
    if evaluation.ai_indikasi >= 0:
        print(f"  │  Indikasi AI (perkiraan): {evaluation.ai_indikasi}% ({evaluation.ai_label})")
    print(f"  │  Balasan : {_truncate(evaluation.balasan, 60)}")

    # Simpan info untuk kembali ke thread & set rating setelah balasan terkirim.
    thread_url = post.get("thread_url", "")
    post_id = post.get("post_id", "")

    # 1. BALAS DULU (post_reply memakai post_id -> tidak tergantung locator basi).
    if auto_submit:
        print("  │  Aksi    : kirim BALASAN...")
    reply_ok = post_reply(page, post, evaluation.formatted, auto_submit)

    # 2. Bila balasan tidak di-auto-submit, jangan lanjut set rating dengan
    #    navigasi (form balasan masih terbuka & menunggu review manual).
    if not auto_submit:
        print("  └─ Balasan terisi (menunggu review, tidak auto-submit).")
        return

    # 3. SET RATING (bila belum dinilai). post_reply sudah pindah halaman,
    #    jadi kembali ke thread, scrape ulang, dapatkan locator fresh.
    if not graded:
        _set_rating_after_reply(page, thread_url, post_id, post["name"],
                                post["text"], evaluation.nilai)
    else:
        print("  │  Nilai sudah ada, tidak diubah.")

    print("  └─ selesai.")


def _set_rating_after_reply(page: Page, thread_url: str, post_id: str,
                            name: str, text: str, nilai: int) -> None:
    """Kembali ke thread, cari post-nya, lalu set rating dengan locator fresh."""
    if not thread_url:
        print("  │  ! URL thread tidak tersedia; nilai tidak diset otomatis.")
        return
    try:
        page.goto(thread_url, wait_until="domcontentloaded")
        wait_page_ready(page)
    except Exception as exc:
        print(f"  │  ! Gagal kembali ke thread untuk set nilai: {exc}")
        return

    target = {"post_id": post_id, "name": name,
              "text_key": " ".join(text.split())[:200]}
    fresh = _find_post_by_target(scrape_posts(page), target)
    if fresh is None:
        print("  │  ! Post tidak ditemukan lagi untuk set nilai.")
        return
    set_rating(page, fresh["locator"], nilai)


# --------------------------------------------------------------------------- #
# Loop interaktif
# --------------------------------------------------------------------------- #
def ask_forum_url() -> str:
    """
    Meminta link halaman forum/thread dari pengguna.

    Returns:
        URL (http...) | "" untuk keluar | "switch" untuk ganti akun.
    """
    while True:
        url = input(
            "\n[INPUT] Masukkan link forum/thread UT "
            "('g' ganti akun, 'q' keluar): "
        ).strip()
        low = url.lower()
        if low == "q":
            return ""
        if low == "g":
            return "switch"
        if url.startswith("http"):
            return url
        print("[INPUT] Link tidak valid. Harus diawali http/https.")


def ask_filter_mode(current: str) -> str:
    """Menanyakan mode filter kepada pengguna."""
    print("\n  Filter tampilan:")
    print("    1. Semua post")
    print("    2. Belum dibalas")
    print("    3. Sudah dibalas")
    print("    4. Belum dinilai")
    print("    5. Sudah dinilai")
    choice = input(f"  Pilih (1-5) [sekarang: {FILTER_LABELS.get(current)}]: ").strip()
    mapping = {"1": "all", "2": "unreplied", "3": "replied", "4": "ungraded", "5": "graded"}
    return mapping.get(choice, current)


def _find_post_by_target(posts: list, target: dict):
    """Mencari post pada hasil scrape yang cocok dengan target (post_id, lalu teks)."""
    if target.get("post_id"):
        for p in posts:
            if p.get("post_id") and p["post_id"] == target["post_id"]:
                return p
    # Fallback: cocokkan berdasarkan potongan teks + nama.
    for p in posts:
        p_key = " ".join(p["text"].split())[:200]
        if p_key == target["text_key"] and p["name"] == target["name"]:
            return p
    return None


def process_targets(page: Page, evaluator: Evaluator, config: dict, url: str,
                    forum_question: str, targets: list) -> None:
    """
    Memproses beberapa post secara berurutan. Untuk SETIAP post:
      1. Kembali ke halaman thread dan refresh (halaman fresh).
      2. Scrape ulang -> dapatkan locator yang valid (tidak basi).
      3. Set rating + tulis balasan.
      4. Jeda DELAY_BETWEEN_POSTS detik sebelum post berikutnya.
    """
    total = len(targets)
    for idx, target in enumerate(targets, start=1):
        print(f"\n  ═══ Post {idx}/{total} ═══")

        # 1 & 2. Muat ulang thread agar locator segar & status terbaru.
        try:
            page.goto(url, wait_until="domcontentloaded")
            wait_page_ready(page)
        except Exception as exc:
            print(f"  ✗ Gagal memuat ulang thread: {exc}")
            continue

        fresh_posts = scrape_posts(page)
        post = _find_post_by_target(fresh_posts, target)
        if post is None:
            print(f"  ! Post '{_truncate(target['name'], 40)}' tidak ditemukan lagi "
                  f"(mungkin sudah dibalas). Dilewati.")
            continue

        # Sisipkan URL thread agar answer_post bisa kembali untuk set rating.
        post["thread_url"] = url

        # 3. Proses.
        answer_post(page, evaluator, post, forum_question, config["auto_submit"])

        if not config["auto_submit"]:
            input("\n  ⏸ AUTO_SUBMIT=false. Review balasan di browser, lalu [ENTER] untuk lanjut...")

        # 4. Jeda antar post (kecuali post terakhir).
        if idx < total:
            print(f"  ⏳ Menunggu {DELAY_BETWEEN_POSTS} detik sebelum post berikutnya...")
            time.sleep(DELAY_BETWEEN_POSTS)


def process_forum(page: Page, evaluator: Evaluator, config: dict, url: str) -> str:
    """
    Menampilkan list post untuk satu URL dan menangani pilihan pengguna (dengan refresh).

    Returns:
        "back"    -> kembali ke menu link
        "switch"  -> pengguna minta ganti akun / logout
    """
    print(f"\n  → Membuka: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded")
        print("  → Menunggu halaman termuat sepenuhnya...")
        wait_page_ready(page)
    except Exception as exc:
        print(f"  ✗ Gagal membuka halaman: {exc}")
        return "back"

    forum_question = get_forum_question(page, config["forum_question"])

    filter_mode = "unreplied"  # default: fokus ke yang belum dibalas

    while True:
        all_posts = scrape_posts(page)
        stats = compute_stats(all_posts)
        posts = apply_filter(all_posts, filter_mode)

        print_banner("DAFTAR POST MAHASISWA")
        print(f"  Diskusi: {_truncate(forum_question, 66)}")
        print_post_list(posts, filter_mode=filter_mode, stats=stats, total_all=len(all_posts))

        if not all_posts:
            action = input("  [ENTER] refresh  •  [b] ganti link  •  [q] keluar : ").strip().lower()
            if action == "b":
                return "back"
            if action == "q":
                raise KeyboardInterrupt
            page.reload(wait_until="domcontentloaded")
            wait_page_ready(page)
            continue

        print("  Aksi:")
        print("    [1,3]  jawab post nomor tertentu     [semua] jawab semua di daftar")
        print("    [f]    ganti filter                  [r]     refresh")
        print("    [b]    ganti link                    [g]     ganti akun")
        print("    [q]    keluar")
        choice = input("  > Pilihan Anda: ").strip().lower()

        if choice == "q":
            raise KeyboardInterrupt
        if choice == "b":
            return "back"
        if choice == "g":
            return "switch"
        if choice == "r":
            print("  → Me-refresh & menunggu halaman termuat...")
            page.reload(wait_until="domcontentloaded")
            wait_page_ready(page)
            continue
        if choice == "f":
            filter_mode = ask_filter_mode(filter_mode)
            continue

        if not posts:
            print("  ! Daftar kosong untuk filter ini. Ganti filter dengan 'f'.")
            continue

        selected = parse_selection(choice, len(posts))
        if not selected:
            print("  ! Pilihan tidak dikenali. Coba lagi.")
            continue

        # Simpan identitas post terpilih (post_id / nama+teks) SEBELUM diproses,
        # karena setiap pemrosesan me-refresh halaman -> locator lama jadi basi.
        targets = []
        for number in selected:
            p = next((p for p in posts if p["number"] == number), None)
            if p:
                targets.append({
                    "post_id": p.get("post_id", ""),
                    "name": p["name"],
                    "text_key": " ".join(p["text"].split())[:200],
                })

        print(f"\n  ▶ Memproses {len(targets)} post secara berurutan (fresh tiap post).")
        process_targets(page, evaluator, config, url, forum_question, targets)

        # Refresh list setelah selesai memproses semua.
        print("  → Me-refresh daftar post...")
        page.reload(wait_until="domcontentloaded")
        wait_page_ready(page)


# --------------------------------------------------------------------------- #
# Orkestrasi utama
# --------------------------------------------------------------------------- #
def establish_session(browser, use_saved: bool):
    """
    Membuat context browser dan memastikan pengguna login.

    Jika use_saved dan session.json ada, sesi tersimpan dimuat lalu diverifikasi.
    Bila tidak valid / tidak ada, pengguna diminta login manual dan sesi disimpan.

    Returns:
        (context, page) jika berhasil, atau (None, None) jika login dibatalkan.
    """
    if use_saved and session_exists():
        print(f"[MAIN] Memuat sesi tersimpan dari '{SESSION_FILE}'...")
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        if verify_session(page):
            print("[MAIN] Sesi tersimpan masih valid. Login otomatis berhasil.")
            return context, page
        print("[MAIN] Sesi tersimpan tidak valid / kedaluwarsa. Perlu login ulang.")
        context.close()

    # Login manual + simpan sesi
    context = browser.new_context()
    page = context.new_page()
    if not wait_for_manual_login(page):
        return None, None
    save_session(context)
    return context, page


def switch_account(browser, context, page):
    """
    Logout akun saat ini, hapus sesi tersimpan, lalu login ulang dengan akun baru.

    Returns:
        (context, page) baru jika berhasil, atau (None, None) jika dibatalkan.
    """
    logout(page, context, clear_saved=True)
    context.close()
    print("[MAIN] Silakan login dengan akun yang berbeda.")
    return establish_session(browser, use_saved=False)


def run():
    print_startup_banner()

    config = load_config()
    if config["gemini_model"]:
        evaluator = Evaluator(api_key=config["gemini_api_key"], model_name=config["gemini_model"])
    else:
        evaluator = Evaluator(api_key=config["gemini_api_key"])
    print(f"[MAIN] Model Gemini: {evaluator.model_name}")
    print(f"[MAIN] Batas pemakaian: {evaluator.limiter.rpm} permintaan/menit, "
          f"{evaluator.limiter.rpd} permintaan/hari "
          f"(sisa hari ini: {evaluator.limiter.remaining_today}).")

    # Pastikan Chromium tersedia (unduh otomatis bila belum ada / saat jalan sebagai .exe).
    from browser_setup import ensure_chromium
    ensure_chromium()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        # Login: pakai sesi tersimpan bila ada, jika tidak login manual
        context, page = establish_session(browser, use_saved=True)
        if context is None:
            print("[MAIN] Login dibatalkan. Program dihentikan.")
            browser.close()
            return

        # Loop interaktif per link
        try:
            while True:
                url = ask_forum_url()
                if url == "":
                    break
                if url == "switch":
                    context, page = switch_account(browser, context, page)
                    if context is None:
                        print("[MAIN] Login dibatalkan. Program dihentikan.")
                        break
                    continue

                result = process_forum(page, evaluator, config, url)
                if result == "switch":
                    context, page = switch_account(browser, context, page)
                    if context is None:
                        print("[MAIN] Login dibatalkan. Program dihentikan.")
                        break
        except KeyboardInterrupt:
            print("\n[MAIN] Keluar atas permintaan pengguna.")

        print("[MAIN] Selesai. Menutup browser.")
        browser.close()


if __name__ == "__main__":
    run()
