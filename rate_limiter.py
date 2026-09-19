"""
rate_limiter.py
Pembatas laju panggilan Gemini API:
  - RPM (Requests Per Minute): default 15/menit.
  - RPD (Requests Per Day)   : default 1.500/hari.

Hitungan harian disimpan persisten (rate_usage.json) agar batas 1.500/hari tetap
berlaku walau aplikasi ditutup lalu dibuka lagi pada hari yang sama.
"""

import json
import os
import sys
import time
from collections import deque
from datetime import date
from pathlib import Path

DEFAULT_RPM = 15
DEFAULT_RPD = 1500
USAGE_FILE = "rate_usage.json"


def _app_dir() -> Path:
    """Folder aplikasi (lokasi .exe saat frozen, atau folder skrip)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


class RateLimiter:
    """Menjaga batas RPM (sliding window 60 dtk) dan RPD (per tanggal)."""

    def __init__(self, rpm: int = DEFAULT_RPM, rpd: int = DEFAULT_RPD):
        self.rpm = rpm
        self.rpd = rpd
        self._minute_calls = deque()          # timestamp panggilan dalam 60 dtk terakhir
        self._usage_path = _app_dir() / USAGE_FILE
        self._day = date.today().isoformat()
        self._day_count = 0
        self._load()

    # ----------------------------------------------------------------- #
    # Persistensi hitungan harian
    # ----------------------------------------------------------------- #
    def _load(self) -> None:
        try:
            if self._usage_path.is_file():
                data = json.loads(self._usage_path.read_text(encoding="utf-8"))
                if data.get("day") == self._day:
                    self._day_count = int(data.get("count", 0))
        except Exception:
            self._day_count = 0

    def _save(self) -> None:
        try:
            self._usage_path.write_text(
                json.dumps({"day": self._day, "count": self._day_count}),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _roll_day_if_needed(self) -> None:
        today = date.today().isoformat()
        if today != self._day:
            self._day = today
            self._day_count = 0
            self._save()

    # ----------------------------------------------------------------- #
    # Gerbang sebelum tiap panggilan API
    # ----------------------------------------------------------------- #
    def acquire(self) -> None:
        """
        Menahan eksekusi hingga aman melakukan 1 panggilan API sesuai batas.
        Memblokir (sleep) bila RPM tercapai; berhenti (exit) bila RPD habis.
        """
        self._roll_day_if_needed()

        # Batas harian (RPD)
        if self._day_count >= self.rpd:
            print(f"\n[LIMIT] Batas harian {self.rpd} permintaan/hari tercapai.")
            print("[LIMIT] Coba lagi besok, atau gunakan Mode Pro. Contact Owner!!!")
            raise SystemExit(0)

        # Batas per menit (RPM) - sliding window 60 detik
        now = time.monotonic()
        while self._minute_calls and now - self._minute_calls[0] >= 60:
            self._minute_calls.popleft()

        if len(self._minute_calls) >= self.rpm:
            wait = 60 - (now - self._minute_calls[0]) + 0.5
            if wait > 0:
                print(f"[LIMIT] Batas {self.rpm} permintaan/menit tercapai. "
                      f"Menunggu {wait:.0f} detik...")
                time.sleep(wait)
            # Bersihkan lagi setelah menunggu
            now = time.monotonic()
            while self._minute_calls and now - self._minute_calls[0] >= 60:
                self._minute_calls.popleft()

    def record(self) -> None:
        """Catat satu panggilan yang berhasil dilakukan (RPM + RPD)."""
        self._minute_calls.append(time.monotonic())
        self._day_count += 1
        self._save()

    @property
    def remaining_today(self) -> int:
        self._roll_day_if_needed()
        return max(0, self.rpd - self._day_count)
