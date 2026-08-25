"""Разбиение текста брифа на чанки для векторной индексации."""

from __future__ import annotations

import re

DEFAULT_CHUNK_SIZE = 1200
DEFAULT_CHUNK_OVERLAP = 200


def chunk_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    compact = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not compact:
        return []
    if len(compact) <= chunk_size:
        return [compact]

    paragraphs = [part.strip() for part in compact.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            flush()
        if len(paragraph) <= chunk_size:
            current = paragraph
            continue
        start = 0
        while start < len(paragraph):
            piece = paragraph[start : start + chunk_size].strip()
            if piece:
                chunks.append(piece)
            start += max(chunk_size - overlap, 1)

    flush()

    if overlap > 0 and len(chunks) > 1:
        merged: list[str] = [chunks[0]]
        for index in range(1, len(chunks)):
            prev_tail = merged[-1][-overlap:]
            merged.append(f"{prev_tail}\n{chunks[index]}".strip())
        return merged
    return chunks
