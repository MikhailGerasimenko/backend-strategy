from __future__ import annotations

import os

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL",
    "https://openrouter.ai/api/v1/chat/completions",
).strip()
BRIEF_MODEL = os.getenv("BRIEF_MODEL", "google/gemini-2.5-flash").strip()
HTTP_TIMEOUT = int(os.getenv("OPENROUTER_TIMEOUT", "180"))
OPENROUTER_RETRIES = int(os.getenv("OPENROUTER_RETRIES", "6"))
OPENROUTER_RETRY_BASE_SEC = float(os.getenv("OPENROUTER_RETRY_BASE_SEC", "15"))

MAX_TOKENS = int(os.getenv("BRIEF_MAX_TOKENS", "32000"))
# 0 = передать в промпт все новости из выборки
MAX_NEWS_IN_PROMPT = int(os.getenv("BRIEF_MAX_NEWS_IN_PROMPT", "0"))
NEWS_BODY_CHARS = int(os.getenv("BRIEF_NEWS_BODY_CHARS", "1200"))

# При числе новостей выше порога — генерация частями (меньше 429 от провайдера)
# Дневной бриф: резать на части, если новостей больше порога (по умолчанию = размеру батча).
BRIEF_CHUNK_SIZE = int(os.getenv("BRIEF_CHUNK_SIZE", "60"))
BRIEF_CHUNK_THRESHOLD = int(os.getenv("BRIEF_CHUNK_THRESHOLD", str(BRIEF_CHUNK_SIZE)))
BRIEF_CHUNK_PAUSE_SEC = float(os.getenv("BRIEF_CHUNK_PAUSE_SEC", "20"))
BRIEF_KALLANISH_MAX_CHARS = int(os.getenv("BRIEF_KALLANISH_MAX_CHARS", "60000"))
