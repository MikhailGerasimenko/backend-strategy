"""Извлечение текста из PDF и Word для индексации."""

from __future__ import annotations

import zipfile
from pathlib import Path


def _file_head(path: Path, size: int = 64) -> bytes:
    with path.open("rb") as handle:
        return handle.read(size)


def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError, PdfStreamError

    head = _file_head(path).lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<htm")):
        raise ValueError(
            f"«{path.name}» не является PDF: внутри HTML-страница. "
            "Так бывает, если вместо документа скачалась веб-страница. "
            "Скачайте исходный PDF и загрузите его снова."
        )
    try:
        reader = PdfReader(str(path))
        parts: list[str] = []
        for page in reader.pages:
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts).strip()
    except (PdfReadError, PdfStreamError, OSError) as exc:
        raise ValueError(
            f"«{path.name}» не удалось прочитать как PDF: файл повреждён или скачан не полностью."
        ) from exc


def extract_docx_text(path: Path) -> str:
    from docx.opc.exceptions import PackageNotFoundError

    from .llm.kallanish_docx import extract_docx_plain_text

    try:
        return extract_docx_plain_text(path).strip()
    except (PackageNotFoundError, zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise ValueError(
            f"«{path.name}» не удалось прочитать как Word: файл повреждён или это не .docx."
        ) from exc


def extract_txt_text(path: Path) -> str:
    raw = path.read_bytes()
    # Kallanish massmail/price .txt часто приходит в UTF-16 (BOM FF FE / FE FF).
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16").strip()
    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1251", "latin-1"):
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
