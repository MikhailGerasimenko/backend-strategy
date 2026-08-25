"""Определение типа документа для RAG-вложений (PDF, Kallanish, PMI, Word, TXT)."""

from __future__ import annotations

from pathlib import Path

DOCX_DEFAULT_TYPE = "Kallanish"
PDF_TYPE = "PDF отчёт"
PMI_TYPE = "PMI"
KALLANISH_TYPE = "Kallanish"
ALLOWED_ATTACHMENT_SUFFIXES = {".pdf", ".docx", ".txt"}


def detect_attachment_document_type(filename: str) -> str:
    """Тип документа по расширению и имени файла."""
    name = Path(filename or "").name
    lower = name.lower()
    suffix = Path(lower).suffix

    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        raise ValueError("Поддерживаются PDF (.pdf), Word (.docx) и текст (.txt).")

    # Имя важнее расширения: PMI / Kallanish могут быть в любом формате.
    if "pmi" in lower:
        return PMI_TYPE
    if "kallanish" in lower:
        return KALLANISH_TYPE
    if suffix == ".pdf":
        return PDF_TYPE
    if suffix == ".txt":
        # Massmail Kallanish часто приходит как .txt с Kallanish в имени;
        # если имени нет — всё равно считаем текстовую рассылку Kallanish.
        return KALLANISH_TYPE
    return DOCX_DEFAULT_TYPE


def resolve_attachment_document_type(filename: str, requested: str | None = None) -> str:
    """Итоговый тип: автоопределение по имени/расширению важнее ручного выбора.

    - имя содержит PMI → PMI
    - имя содержит Kallanish → Kallanish
    - .pdf → PDF отчёт (если не PMI)
    - .docx / .txt → Kallanish (если не PMI)
    """
    detected = detect_attachment_document_type(filename)
    suffix = Path(filename or "").suffix.lower()
    manual = (requested or "").strip()
    lower_name = Path(filename or "").name.lower()

    if detected == PMI_TYPE:
        return PMI_TYPE
    if "kallanish" in lower_name:
        return KALLANISH_TYPE

    if suffix == ".pdf":
        return PDF_TYPE

    if suffix in {".docx", ".txt"}:
        # Word/TXT не могут быть «PDF отчётом».
        if not manual or manual == PDF_TYPE:
            return detected
        return manual

    raise ValueError("Поддерживаются PDF (.pdf), Word (.docx) и текст (.txt).")
