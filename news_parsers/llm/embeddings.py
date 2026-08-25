"""Эмбеддинги через OpenRouter (OpenAI-compatible /embeddings)."""

from __future__ import annotations

import os
from typing import Sequence

import requests

from .config import HTTP_TIMEOUT, OPENROUTER_BASE_URL
from .openrouter import OpenRouterError, get_api_key

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small").strip()
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
EMBEDDINGS_URL = os.getenv(
    "OPENROUTER_EMBEDDINGS_URL",
    OPENROUTER_BASE_URL.replace("/chat/completions", "/embeddings"),
).strip()


def embed_texts(texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    api_key = get_api_key()
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set.")

    payload: dict = {
        "model": model or EMBEDDING_MODEL,
        "input": list(texts),
        "encoding_format": "float",
    }
    if EMBEDDING_DIMENSIONS:
        payload["dimensions"] = EMBEDDING_DIMENSIONS

    response = requests.post(
        EMBEDDINGS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/strategic-navigator",
            "X-Title": "Strategic Navigator RAG",
        },
        json=payload,
        timeout=HTTP_TIMEOUT,
    )
    if not response.ok:
        raise OpenRouterError(
            f"Embeddings request failed ({response.status_code}): {response.text[:500]}"
        )
    data = response.json()
    rows = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
    vectors: list[list[float]] = []
    for row in rows:
        vector = row.get("embedding")
        if not isinstance(vector, list):
            raise OpenRouterError(f"Unexpected embeddings response: {data}")
        vectors.append([float(value) for value in vector])
    if len(vectors) != len(texts):
        raise OpenRouterError(
            f"Expected {len(texts)} embeddings, got {len(vectors)}"
        )
    return vectors
