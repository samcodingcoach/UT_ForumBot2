"""Menampilkan daftar model Gemini yang tersedia untuk API key ini."""

import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY", ""))

print("Model yang mendukung generateContent:")
print("=" * 60)
for m in client.models.list():
    actions = getattr(m, "supported_actions", None) or []
    if not actions or "generateContent" in actions:
        print(f"  {m.name}")
