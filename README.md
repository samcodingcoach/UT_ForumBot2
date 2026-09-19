# UT Moodle Forum Discussion Auto-Grader & Auto-Reply Bot (Interaktif)

Bot Python berbasis **Playwright** dan **Google Gemini API** (default `gemini-flash-latest`, bisa diganti via `GEMINI_MODEL`) untuk membantu menilai dan membalas post diskusi mahasiswa di UT Moodle.

Bot ini **tidak menyimpan username/password UT**. Anda login sendiri secara manual di jendela browser satu kali; sesinya disimpan (`session.json`) sehingga login berikutnya otomatis. Tersedia opsi **ganti akun / logout** kapan saja. Satu-satunya kredensial yang tertanam di `.env` adalah **GEMINI_API_KEY**.

> Gunakan hanya pada akun dan forum milik Anda sendiri atau yang Anda berwenang mengelolanya.

---

## Flow Aplikasi

1. **Login:**
   - Jika ada sesi tersimpan yang masih valid, bot **login otomatis**.
   - Jika tidak, bot membuka halaman login UT — Anda **login manual**, tekan ENTER, dan sesi disimpan untuk berikutnya.
2. Anda **memasukkan link** halaman forum/thread.
3. App menampilkan **daftar post/pertanyaan mahasiswa** (bernomor).
4. Anda memilih: **nomor tertentu** (mis. `1,3`), **`semua`**, refresh (`r`), ganti link (`b`), **ganti akun (`g`)**, atau keluar (`q`).
5. AI menjawab yang dipilih (mengisi nilai + menulis balasan).
6. App **me-refresh** daftar post, lalu kembali ke menu pilihan.

---

## Struktur Proyek

```text
UT-Bot/
├── .env                # hanya GEMINI_API_KEY (+ opsi FORUM_QUESTION, AUTO_SUBMIT)
├── .env.example
├── .gitignore
├── session.json        # sesi login tersimpan (dibuat otomatis, TIDAK di-commit)
├── requirements.txt
├── main.py             # loop interaktif Playwright + manajemen sesi
├── auth.py             # login manual, sesi tersimpan, logout / ganti akun
├── evaluator.py        # klien Gemini API & parser respons
└── README.md
```

---

## Prasyarat

- Python 3.10 atau lebih baru
- API key Google Gemini (https://aistudio.google.com/apikey)
- Akun UT Moodle (login manual sekali; setelah itu sesi diingat)

---

## Instalasi

```powershell
# 1. (Opsional) virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install browser Chromium untuk Playwright
playwright install chromium
```

---

## Konfigurasi

Salin `.env.example` menjadi `.env`, lalu isi `GEMINI_API_KEY`:

```powershell
Copy-Item .env.example .env
```

```env
GEMINI_API_KEY=api_key_gemini_anda
GEMINI_MODEL=
FORUM_QUESTION=
AUTO_SUBMIT=false
```

| Variabel         | Wajib | Keterangan                                                                       |
|------------------|-------|----------------------------------------------------------------------------------|
| `GEMINI_API_KEY` | Ya    | API key Google Gemini.                                                           |
| `GEMINI_MODEL`   | Tidak | Nama model. Kosong = `gemini-flash-latest`. Mis. `gemini-3.6-flash`.             |
| `FORUM_QUESTION` | Tidak | Konteks pertanyaan untuk AI. Jika kosong, diambil otomatis dari judul thread.    |
| `AUTO_SUBMIT`    | Tidak | `true` = kirim balasan otomatis. `false` = berhenti untuk direview dulu.         |

---

## Menjalankan

```powershell
python main.py
```

Contoh sesi di terminal (login pertama kali):

```text
[MAIN] Memuat sesi tersimpan dari 'session.json'...   # hanya jika sudah pernah login
[AUTH] Silakan LOGIN secara MANUAL pada jendela browser yang terbuka.
[AUTH] Tekan ENTER setelah Anda selesai login...
[AUTH] Sesi disimpan ke 'session.json'.

[INPUT] Masukkan link forum/thread UT ('g' ganti akun, 'q' keluar): https://elearning.ut.ac.id/mod/forum/discuss.php?d=...

Ditemukan 3 post mahasiswa:
  [1] Budi Santoso
       Menurut saya, konsep tersebut berkaitan dengan...
  [2] Siti Aminah
       Berdasarkan modul, saya berpendapat bahwa...
  [3] Andi Wijaya
       Tanggapan saya terhadap pertanyaan ini...

Pilihan:
  - Ketik nomor untuk dijawab, pisah koma (mis. 1,3)
  - Ketik 'semua' untuk menjawab semua post
  - Ketik 'r' untuk refresh list
  - Ketik 'b' untuk ganti link
  - Ketik 'g' untuk ganti akun / logout
  - Ketik 'q' untuk keluar
[INPUT] Pilihan Anda: 1,3
```

Menjalankan berikutnya: jika `session.json` masih valid, bot langsung login otomatis tanpa perlu login manual lagi.

---

## Menu Pilihan

| Input         | Aksi                                                        |
|---------------|-------------------------------------------------------------|
| `1` / `1,3`   | Jawab post nomor tersebut.                                  |
| `semua`/`all` | Jawab semua post yang terbaca.                              |
| `r`           | Refresh daftar post.                                        |
| `b`           | Ganti link (kembali ke input URL).                          |
| `g`           | Ganti akun / logout (hapus sesi, lalu login akun lain).     |
| `q`           | Keluar dari program.                                        |

---

## Sesi Login & Ganti Akun

- **Sesi tersimpan:** Setelah login manual pertama, sesi disimpan ke `session.json`. Menjalankan bot lagi akan mencoba memakai sesi ini dan memverifikasinya ke dashboard UT.
- **Sesi kedaluwarsa:** Jika sesi tidak lagi valid, bot otomatis meminta login manual dan menyimpan sesi baru.
- **Ganti akun / logout:** Pilih `g` di menu. Bot logout dari UT, membersihkan cookies, menghapus `session.json`, lalu meminta Anda login dengan akun lain.
- **Keamanan:** `session.json` berisi cookie sesi Anda dan **tidak boleh dibagikan**. File ini sudah masuk `.gitignore`.

---

## Catatan Penting

- **Halaman thread vs daftar forum:** Agar post mahasiswa terbaca, gunakan URL **thread** (`mod/forum/discuss.php?d=...`), bukan daftar forum (`mod/forum/view.php?id=...`).
- **Mode review:** Setel `AUTO_SUBMIT=false` agar balasan bisa ditinjau di browser sebelum dikirim.
- **Selektor DOM:** Struktur tema UT bisa berbeda. Jika post tidak terbaca atau tombol tidak ditemukan, sesuaikan selektor di `main.py` (`POST_SELECTORS`, `AUTHOR_SELECTORS`, `CONTENT_SELECTORS`, dropdown rating, tombol submit).
- **Fitur rating:** Dropdown rating hanya muncul jika akun Anda punya hak menilai dan rating forum aktif.
- **Keamanan:** File `.env` dan `session.json` sudah masuk `.gitignore`. Jangan pernah membagikan API key atau file sesi Anda.

---

## Troubleshooting

| Masalah                            | Solusi                                                                     |
|------------------------------------|----------------------------------------------------------------------------|
| "Sesi login belum terdeteksi"      | Pastikan sudah masuk dashboard UT di jendela browser, lalu cek lagi.       |
| Selalu diminta login manual        | Sesi tidak tersimpan/kedaluwarsa. Pastikan `session.json` bisa ditulis.    |
| Ingin ganti akun                   | Pilih `g` di menu untuk logout dan login akun lain.                        |
| "Tidak ada post"                   | Anda di halaman daftar forum. Gunakan URL `discuss.php?d=`.                |
| Dropdown rating tidak ditemukan    | Akun tidak punya hak menilai, atau rating forum nonaktif.                  |
| Editor balasan tidak muncul        | Selektor editor berbeda; sesuaikan di `_fill_editor` pada `main.py`.       |
| Error `GEMINI_API_KEY belum diisi` | Isi `GEMINI_API_KEY` yang valid di `.env`.                                 |
