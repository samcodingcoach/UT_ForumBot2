"""Uji cepat: memastikan GEMINI_API_KEY valid dan model Gemini bekerja."""

import os
from dotenv import load_dotenv
from evaluator import Evaluator

load_dotenv()

evaluator = Evaluator(api_key=os.getenv("GEMINI_API_KEY", ""))

print("Model dipakai:", evaluator.model_name)

hasil = evaluator.evaluate(
    forum_question="Jelaskan perbedaan antara data primer dan data sekunder dalam penelitian.",
    student_name="Budi Santoso",
    student_text=(
        "Data primer adalah data yang dikumpulkan langsung oleh peneliti dari sumbernya, "
        "misalnya melalui wawancara atau kuesioner. Sedangkan data sekunder adalah data "
        "yang sudah ada sebelumnya, seperti dari buku, jurnal, atau laporan pemerintah."
    ),
)

print("=" * 60)
print(f"Indikasi AI (perkiraan): {hasil.ai_indikasi}% ({hasil.ai_label})")
print("-" * 60)
print("Pratinjau balasan yang akan ditulis ke forum:")
print("-" * 60)
print(hasil.formatted)
print("=" * 60)
print(f"OK: Gemini API + model '{evaluator.model_name}' berhasil dipanggil.")
