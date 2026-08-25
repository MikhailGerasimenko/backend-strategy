"""Фасад выбора векторного бэкенда: Qdrant (если задан QDRANT_URL) или pgvector.

Сервисы могут перейти на импорты из этого модуля вместо store/news_store —
сигнатуры совместимы с существующим кодом (`database_url` первым аргументом).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import news_store as _pg_news
from . import qdrant_store as _qd
from . import store as _pg_brief
from .news_store import NewsDocument, NewsSearchHit
from .qdrant_store import (
    COLLECTION_BRIEF,
    COLLECTION_NEWS,
    ensure_collections,
    get_qdrant_client,
    qdrant_configured,
)
from .store import (
    AttachmentDocument,
    AttachmentSearchHit,
    BriefIndexError,
    DuplicateDocumentError,
    IndexedChunk,
    database_url,
)


def vector_backend() -> str:
    """Активный стор: ``qdrant`` при QDRANT_URL, иначе ``pgvector``."""
    return "qdrant" if qdrant_configured() else "pgvector"


def uses_qdrant() -> bool:
    return vector_backend() == "qdrant"


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def index_news_items(
    database_url: str | None,
    items: Iterable[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    if uses_qdrant():
        return _qd.index_news_items(items, database_url=database_url, **kwargs)
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.index_news_items(database_url, items, **kwargs)


def index_news_jsonl(
    database_url: str | None,
    jsonl_path: Path,
    **kwargs: Any,
) -> dict[str, Any]:
    if uses_qdrant():
        return _qd.index_news_jsonl(jsonl_path, database_url=database_url, **kwargs)
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.index_news_jsonl(database_url, jsonl_path, **kwargs)


def fetch_news_documents_for_period(
    database_url: str | None,
    *,
    period_start: date,
    period_end: date,
    keyword_block: str | None = None,
    sources: Sequence[str] | None = None,
) -> list[NewsDocument]:
    if uses_qdrant():
        return _qd.fetch_news_documents_for_period(
            period_start=period_start,
            period_end=period_end,
            keyword_block=keyword_block,
            sources=sources,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.fetch_news_documents_for_period(
        database_url,
        period_start=period_start,
        period_end=period_end,
        keyword_block=keyword_block,
        sources=sources,
    )


def search_news_chunks(
    database_url: str | None,
    *,
    query_text: str,
    period_start: date | None = None,
    period_end: date | None = None,
    source_contains: str | None = None,
    top_k: int = 12,
    candidate_limit: int | None = None,
) -> list[NewsSearchHit]:
    if uses_qdrant():
        return _qd.search_news_chunks(
            query_text=query_text,
            period_start=period_start,
            period_end=period_end,
            source_contains=source_contains,
            top_k=top_k,
            candidate_limit=candidate_limit,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.search_news_chunks(
        database_url,
        query_text=query_text,
        period_start=period_start,
        period_end=period_end,
        source_contains=source_contains,
        top_k=top_k,
        candidate_limit=candidate_limit,
    )


def get_news_document(
    database_url: str | None,
    document_id: int,
) -> NewsDocument | None:
    if uses_qdrant():
        return _qd.get_news_document(document_id, database_url=database_url)
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.get_news_document(database_url, document_id)


def get_news_chunks_for_documents(
    database_url: str | None,
    document_ids: Sequence[int],
) -> list[NewsSearchHit]:
    if uses_qdrant():
        return _qd.get_news_chunks_for_documents(document_ids, database_url=database_url)
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.get_news_chunks_for_documents(database_url, document_ids)


def period_news_stats(
    database_url: str | None,
    *,
    period_start: date,
    period_end: date,
    sources: Sequence[str] | None = None,
) -> dict[str, Any]:
    if uses_qdrant():
        return _qd.period_news_stats(
            period_start=period_start,
            period_end=period_end,
            sources=sources,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.period_news_stats(
        database_url,
        period_start=period_start,
        period_end=period_end,
        sources=sources,
    )


def list_period_news_sources(
    database_url: str | None,
    *,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    if uses_qdrant():
        return _qd.list_period_news_sources(
            period_start=period_start,
            period_end=period_end,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_news.list_period_news_sources(
        database_url, period_start=period_start, period_end=period_end
    )


# ---------------------------------------------------------------------------
# Attachments / brief index
# ---------------------------------------------------------------------------


def index_attachment_document(
    database_url: str | None,
    *,
    file_path: Path,
    raw_bytes: bytes,
    brief_date: date,
    period_end: date | None,
    document_type: str,
    indexed_by: str,
    title: str = "",
) -> dict[str, Any]:
    if uses_qdrant():
        return _qd.index_attachment_document(
            file_path=file_path,
            raw_bytes=raw_bytes,
            brief_date=brief_date,
            period_end=period_end,
            document_type=document_type,
            indexed_by=indexed_by,
            title=title,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.index_attachment_document(
        database_url,
        file_path=file_path,
        raw_bytes=raw_bytes,
        brief_date=brief_date,
        period_end=period_end,
        document_type=document_type,
        indexed_by=indexed_by,
        title=title,
    )


def list_attachment_documents(
    database_url: str | None,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[dict[str, Any]]:
    if uses_qdrant():
        return _qd.list_attachment_documents(
            period_start=period_start,
            period_end=period_end,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.list_attachment_documents(
        database_url, period_start=period_start, period_end=period_end
    )


def delete_attachment_document(
    database_url: str | None,
    document_id: int,
    *,
    allowed_roots: Sequence[Path] | None = None,
) -> dict[str, Any]:
    if uses_qdrant():
        return _qd.delete_attachment_document(
            document_id,
            allowed_roots=allowed_roots,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.delete_attachment_document(
        database_url, document_id, allowed_roots=allowed_roots
    )


def search_attachment_chunks(
    database_url: str | None,
    *,
    query_text: str,
    period_start: date | None = None,
    period_end: date | None = None,
    document_type_contains: str | None = None,
    top_k: int = 8,
    candidate_limit: int | None = None,
) -> list[AttachmentSearchHit]:
    if uses_qdrant():
        return _qd.search_attachment_chunks(
            query_text=query_text,
            period_start=period_start,
            period_end=period_end,
            document_type_contains=document_type_contains,
            top_k=top_k,
            candidate_limit=candidate_limit,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.search_attachment_chunks(
        database_url,
        query_text=query_text,
        period_start=period_start,
        period_end=period_end,
        document_type_contains=document_type_contains,
        top_k=top_k,
        candidate_limit=candidate_limit,
    )


def get_attachment_document(
    database_url: str | None,
    document_id: int,
) -> AttachmentDocument | None:
    if uses_qdrant():
        return _qd.get_attachment_document(document_id, database_url=database_url)
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.get_attachment_document(database_url, document_id)


def get_attachment_documents_by_ids(
    database_url: str | None,
    document_ids: Sequence[int],
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[AttachmentDocument]:
    if uses_qdrant():
        return _qd.get_attachment_documents_by_ids(
            document_ids,
            period_start=period_start,
            period_end=period_end,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.get_attachment_documents_by_ids(
        database_url,
        document_ids,
        period_start=period_start,
        period_end=period_end,
    )


def get_attachment_chunks_for_documents(
    database_url: str | None,
    document_ids: Sequence[int],
) -> list[AttachmentSearchHit]:
    if uses_qdrant():
        return _qd.get_attachment_chunks_for_documents(
            document_ids, database_url=database_url
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.get_attachment_chunks_for_documents(database_url, document_ids)


def get_attachment_section(
    database_url: str | None,
    document_id: int,
    chunk_index: int | None,
    *,
    radius: int = 2,
    max_chars: int = 16_000,
) -> tuple[AttachmentDocument | None, str]:
    if uses_qdrant():
        return _qd.get_attachment_section(
            document_id,
            chunk_index,
            radius=radius,
            max_chars=max_chars,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.get_attachment_section(
        database_url,
        document_id,
        chunk_index,
        radius=radius,
        max_chars=max_chars,
    )


def retrieve_attachment_chunks_for_period(
    database_url: str | None,
    *,
    period_start: date,
    period_end: date,
    query_text: str,
    top_k: int = 16,
    document_ids: Sequence[int] | None = None,
) -> list[IndexedChunk]:
    if uses_qdrant():
        return _qd.retrieve_attachment_chunks_for_period(
            period_start=period_start,
            period_end=period_end,
            query_text=query_text,
            top_k=top_k,
            document_ids=document_ids,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.retrieve_attachment_chunks_for_period(
        database_url,
        period_start=period_start,
        period_end=period_end,
        query_text=query_text,
        top_k=top_k,
        document_ids=document_ids,
    )


def retrieve_chunks_for_period(
    database_url: str | None,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str,
    query_text: str,
    top_k: int = 40,
) -> list[IndexedChunk]:
    if uses_qdrant():
        return _qd.retrieve_chunks_for_period(
            period_start=period_start,
            period_end=period_end,
            brief_kind=brief_kind,
            query_text=query_text,
            top_k=top_k,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.retrieve_chunks_for_period(
        database_url,
        period_start=period_start,
        period_end=period_end,
        brief_kind=brief_kind,
        query_text=query_text,
        top_k=top_k,
    )


def count_attachment_documents(
    database_url: str | None,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> int:
    if uses_qdrant():
        return _qd.count_attachment_documents(
            period_start=period_start,
            period_end=period_end,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.count_attachment_documents(
        database_url, period_start=period_start, period_end=period_end
    )


def list_index_coverage(
    database_url: str | None,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str,
) -> list[dict[str, Any]]:
    if uses_qdrant():
        return _qd.list_index_coverage(
            period_start=period_start,
            period_end=period_end,
            brief_kind=brief_kind,
            database_url=database_url,
        )
    if not database_url:
        raise BriefIndexError("DATABASE_URL не задан.")
    return _pg_brief.list_index_coverage(
        database_url,
        period_start=period_start,
        period_end=period_end,
        brief_kind=brief_kind,
    )


__all__ = [
    "COLLECTION_BRIEF",
    "COLLECTION_NEWS",
    "AttachmentDocument",
    "AttachmentSearchHit",
    "BriefIndexError",
    "DuplicateDocumentError",
    "IndexedChunk",
    "NewsDocument",
    "NewsSearchHit",
    "count_attachment_documents",
    "database_url",
    "delete_attachment_document",
    "ensure_collections",
    "fetch_news_documents_for_period",
    "get_attachment_chunks_for_documents",
    "get_attachment_document",
    "get_attachment_documents_by_ids",
    "get_attachment_section",
    "get_news_chunks_for_documents",
    "get_news_document",
    "get_qdrant_client",
    "index_attachment_document",
    "index_news_items",
    "index_news_jsonl",
    "list_attachment_documents",
    "list_index_coverage",
    "list_period_news_sources",
    "period_news_stats",
    "qdrant_configured",
    "retrieve_attachment_chunks_for_period",
    "retrieve_chunks_for_period",
    "search_attachment_chunks",
    "search_news_chunks",
    "uses_qdrant",
    "vector_backend",
]
