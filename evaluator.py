"""
evaluator.py
Klien Google Gemini API (default gemini-flash-lite-latest) untuk mengevaluasi jawaban diskusi
mahasiswa dan menghasilkan NILAI (integer 0-100) beserta BALASAN (feedback).
"""

import logging
import re
import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from rate_limiter import RateLimiter

# Redam warning "Automatic function calling (AFC)" yang tidak relevan untuk kita.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

MODEL_NAME = "gemini-flash-lite-latest"

# Retry untuk error sementara (server overload / rate limit).
# Retry berlangsung TANPA BATAS sampai berhasil; nilai di bawah mengatur jeda.
RETRY_BASE_DELAY = 3.0   # detik; basis exponential backoff
MAX_BACKOFF = 60.0       # jeda maksimum per percobaan (detik)
RETRYABLE_STATUS = ("503", "429", "500", "502", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED")

# Kategori rating berdasarkan rentang nilai (untuk baris "Rating:").
def _rating_label(nilai: int) -> str:
    if nilai >= 90:
        return "Sangat Memuaskan"
    if nilai >= 80:
        return "Memuaskan"
    if nilai >= 70:
        return "Baik"
    if nilai >= 60:
        return "Cukup"
    return "Perlu Perbaikan"


PROMPT_TEMPLATE = """Kamu seorang tutor Universitas Terbuka yang sedang menanggapi jawaban diskusi mahasiswa di forum. Balas seperti manusia betulan: mengalir, hangat, dan tidak template.

Topik diskusi: {forum_question}
Nama mahasiswa: {student_name}
Jawaban mahasiswa:
\"\"\"
{student_text}
\"\"\"

Tugasmu:
1. Beri NILAI 0-100 secara objektif sesuai kualitas isi (kedalaman, relevansi, kaitan dengan teori/modul).
2. Tulis BALASAN tanggapan yang natural untuk mahasiswa.
3. Perkirakan INDIKASI_AI: seberapa besar kemungkinan jawaban mahasiswa ditulis oleh AI (0-100 persen), hanya perkiraan gaya bahasa, bukan vonis.

Aturan menulis BALASAN:
- Gaya bahasa manusiawi dan variatif. JANGAN memulai dengan sapaan kaku yang selalu sama; boleh langsung menanggapi isi. Hindari kalimat pembuka klise seperti "Terima kasih atas partisipasinya".
- Rapikan dalam paragraf. Jika kamu menjelaskan hal yang berurutan atau beberapa poin, gunakan penomoran (1., 2., 3.) atau butir agar mudah dibaca.
- Panjang secukupnya, langsung ke inti. Tanggapi hal spesifik dari jawaban mahasiswa, jangan umum.
- Kalau ada yang kurang, sampaikan sebagai saran yang membangun, bukan menggurui.
- Tidak perlu menyebut nilai/rating di dalam teks balasan (itu ditambahkan terpisah).

Keluarkan PERSIS dalam format ini (tanpa tambahan lain):
NILAI: <angka bulat 0-100>
INDIKASI_AI: <angka bulat 0-100>
BALASAN:
<isi tanggapan, boleh beberapa paragraf / penomoran>
"""


def _ai_label(persen: int) -> str:
    """Label kualitatif untuk indikasi AI (bukan vonis, hanya perkiraan)."""
    if persen < 0:
        return "tidak terdeteksi"
    if persen >= 70:
        return "cenderung AI"
    if persen >= 40:
        return "campuran / ragu"
    return "cenderung manusia"


@dataclass
class Evaluation:
    """Hasil evaluasi dari Gemini."""
    nilai: int
    balasan: str
    ai_indikasi: int = -1  # 0-100 (perkiraan gaya AI); -1 = tidak diketahui

    @property
    def rating_label(self) -> str:
        """Kategori rating berdasarkan nilai (mis. 'Memuaskan')."""
        return _rating_label(self.nilai)

    @property
    def ai_label(self) -> str:
        return _ai_label(self.ai_indikasi)

    @property
    def formatted(self) -> str:
        """
        Teks balasan lengkap sesuai template UT:

            Rating: <kategori>
            Nilai: <n>/100
            Tanggapan / Balasan Forum:
            <feedback>
        """
        return (
            f"Rating: {self.rating_label}\n"
            f"Nilai: {self.nilai}/100\n"
            f"Tanggapan / Balasan Forum:\n"
            f"{self.balasan.strip()}"
        )


class Evaluator:
    """Pembungkus klien Gemini untuk evaluasi jawaban diskusi."""

    def __init__(self, api_key: str, model_name: str = MODEL_NAME, limiter: RateLimiter = None):
        if not api_key or api_key == "your_gemini_api_key_here":
            raise ValueError(
                "GEMINI_API_KEY belum diisi. Silakan isi di file .env."
            )
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.limiter = limiter or RateLimiter()

    def evaluate(self, forum_question: str, student_name: str, student_text: str) -> Evaluation:
        """
        Mengevaluasi jawaban mahasiswa dan mengembalikan objek Evaluation.

        Args:
            forum_question: Topik/pertanyaan diskusi (dari FORUM_QUESTION).
            student_name: Nama mahasiswa.
            student_text: Teks jawaban mahasiswa.

        Returns:
            Evaluation(nilai, balasan).
        """
        prompt = PROMPT_TEMPLATE.format(
            forum_question=forum_question,
            student_name=student_name,
            student_text=student_text,
        )

        response = self._generate_with_retry(prompt)
        raw_text = (response.text or "").strip()
        return parse_evaluation(raw_text)

    def _generate_with_retry(self, prompt: str):
        """
        Memanggil Gemini dengan retry TANPA BATAS sampai berhasil, untuk error
        sementara (503 overload, 429 kuota/rate limit, dll).

        - 429 (kuota): menunggu sesuai 'retryDelay' dari server bila tersedia.
        - Lainnya: exponential backoff dengan batas atas MAX_BACKOFF.
        - Error non-sementara (mis. API key salah, model tidak ada): langsung dilempar.
        """
        attempt = 0
        while True:
            attempt += 1
            # Gerbang rate limit (RPM/RPD) sebelum memanggil API.
            self.limiter.acquire()
            try:
                resp = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=0.4),
                )
                self.limiter.record()
                return resp
            except Exception as exc:
                if not _is_retryable(exc):
                    raise

                server_delay = _retry_delay_from_error(exc)
                if server_delay is not None:
                    delay = server_delay + 1.0  # beri margin
                    reason = "kuota/rate limit"
                else:
                    delay = min(RETRY_BASE_DELAY * (2 ** min(attempt - 1, 5)), MAX_BACKOFF)
                    reason = "model sibuk"

                print(
                    f"[AI] {reason} (percobaan {attempt}). "
                    f"Menunggu {delay:.0f} detik lalu mencoba lagi... (Ctrl+C untuk batal)"
                )
                time.sleep(delay)


def _is_retryable(exc: Exception) -> bool:
    """True jika error kemungkinan sementara (overload/rate limit) dan layak di-retry."""
    # google-genai punya atribut code untuk APIError; cek juga teks pesan.
    code = getattr(exc, "code", None)
    if code in (503, 429, 500, 502, 504):
        return True
    text = str(exc).upper()
    return any(status in text for status in RETRYABLE_STATUS)


def _retry_delay_from_error(exc: Exception):
    """
    Mengambil saran jeda dari server pada error 429, mis. "retryDelay": "35s"
    atau "Please retry in 35.5s". Mengembalikan detik (float) atau None.
    """
    text = str(exc)
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", text)
    if m:
        return float(m.group(1))
    m = re.search(r"retry in\s+(\d+(?:\.\d+)?)s", text, re.IGNORECASE)
    if m:
        return float(m.group(1))
    return None


def parse_evaluation(raw_text: str) -> Evaluation:
    """
    Mem-parsing keluaran Gemini menjadi Evaluation.

    Format yang diharapkan:
        NILAI: <int>
        BALASAN: <teks>

    Bersifat toleran terhadap variasi spasi/format.
    """
    nilai = _extract_nilai(raw_text)
    ai_indikasi = _extract_ai_indikasi(raw_text)
    balasan = _extract_balasan(raw_text)

    if not balasan:
        # Fallback: gunakan seluruh teks bila label BALASAN tidak ditemukan.
        balasan = raw_text.strip() or "Terima kasih atas partisipasi Anda dalam diskusi."

    return Evaluation(nilai=nilai, balasan=balasan, ai_indikasi=ai_indikasi)


def _extract_nilai(raw_text: str) -> int:
    """Mengambil nilai integer 0-100 dari baris NILAI (hindari INDIKASI_AI)."""
    # Pakai NILAI yang bukan bagian dari 'INDIKASI_AI'. \bNILAI memastikan
    # 'INDIKASI_AI' tidak ikut tertangkap.
    match = re.search(r"(?<![A-Z_])NILAI\s*[:\-]?\s*(\d{1,3})", raw_text, re.IGNORECASE)
    if match:
        return max(0, min(100, int(match.group(1))))

    fallback = re.search(r"\b(\d{1,3})\b", raw_text)
    if fallback:
        return max(0, min(100, int(fallback.group(1))))
    return 0


def _extract_ai_indikasi(raw_text: str) -> int:
    """Mengambil INDIKASI_AI (0-100) dari teks; -1 bila tidak ada."""
    match = re.search(r"INDIKASI[_\s]*AI\s*[:\-]?\s*(\d{1,3})", raw_text, re.IGNORECASE)
    if match:
        return max(0, min(100, int(match.group(1))))
    return -1


def _extract_balasan(raw_text: str) -> str:
    """Mengambil teks BALASAN (semua baris setelah label BALASAN)."""
    match = re.search(r"BALASAN\s*[:\-]?\s*(.+)", raw_text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1).strip()
    return ""
