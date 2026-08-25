"""Извлечение текста из PDF и Word для индексации."""

from __future__ import annotations

from pathlib import Path


def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts).strip()


def extract_docx_text(path: Path) -> str:
    from .llm.kallanish_docx import extract_docx_plain_text

    return extract_docx_plain_text(path).strip()


def extract_txt_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace").strip()


def extract_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path)
    if suffix == ".docx":
        return extract_docx_text(path)
    if suffix == ".txt":
        return extract_txt_text(path)
    raise ValueError(f"Неподдерживаемый формат: {suffix or path.name}")
