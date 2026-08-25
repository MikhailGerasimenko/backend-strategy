"""Qdrant-backed RAG: новости (`rag_news`) и вложения (`brief_index`).

Один point на чанк + отдельный point на документ (`point_kind`).
`document_id` в payload связывает чанки с документом; id point = стабильный u64-hash.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterable, Sequence

from ..article_text import fetch_full_article_text
from ..document_text import extract_document_text
from ..http import HttpClient
from ..llm.embeddings import EMBEDDING_DIMENSIONS, embed_texts
from .chunking import chunk_text
from .news_store import (
    EMBED_BATCH,
    NewsDocument,
    NewsRagError,
    NewsSearchHit,
    _parse_news_date,
    load_news_jsonl,
)
from .store import (
    ATTACHMENT_SOURCE_TYPES,
    AttachmentDocument,
    AttachmentSearchHit,
    BriefIndexError,
    DuplicateDocumentError,
    IndexedChunk,
    _join_chunk_texts,
    _safe_unlink_attachment,
    _source_type_for_path,
)

if TYPE_CHECKING:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qm

LogFn = Callable[[str], None]

COLLECTION_NEWS = "rag_news"
COLLECTION_BRIEF = "brief_index"

_POINT_DOCUMENT = "document"
_POINT_CHUNK = "chunk"

_client: Any = None


def _qm():
    from qdrant_client.http import models as models

    return models


def qdrant_configured() -> bool:
    return bool(os.getenv("QDRANT_URL", "").strip())


def get_qdrant_client() -> QdrantClient:
    global _client
    if _client is not None:
        return _client
    url = os.getenv("QDRANT_URL", "").strip()
    if not url:
        raise BriefIndexError("QDRANT_URL не задан.")
    try:
        from qdrant_client import QdrantClient as _QdrantClient
    except ImportError as exc:
        raise BriefIndexError(
            "Пакет qdrant-client не установлен. Добавьте его в requirements."
        ) from exc
    api_key = os.getenv("QDRANT_API_KEY", "").strip() or None
    _client = _QdrantClient(url=url, api_key=api_key)
    return _client


def ensure_collections(vector_size: int = EMBEDDING_DIMENSIONS) -> None:
    client = get_qdrant_client()
    qm = _qm()
    existing = {c.name for c in client.get_collections().collections}
    for name in (COLLECTION_NEWS, COLLECTION_BRIEF):
        if name in existing:
            continue
        client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
        )


def _stable_u64(*parts: str) -> int:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _date_ord(value: date) -> int:
    return value.toordinal()


def _parse_payload_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _placeholder_vector(size: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """Единичный вектор для document-point (не участвует в семантическом поиске)."""
    vector = [0.0] * size
    if size:
        vector[0] = 1.0
    return vector


def _embed_all(chunks: Sequence[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = list(chunks[start : start + EMBED_BATCH])
        vectors.extend(embed_texts(batch))
    return vectors


def _match(key: str, value: Any) -> Any:
    qm = _qm()
    return qm.FieldCondition(key=key, match=qm.MatchValue(value=value))


def _match_any(key: str, values: Sequence[Any]) -> Any:
    qm = _qm()
    return qm.FieldCondition(key=key, match=qm.MatchAny(any=list(values)))


def _range_ord(key: str, *, gte: int | None = None, lte: int | None = None) -> Any:
    qm = _qm()
    return qm.FieldCondition(key=key, range=qm.Range(gte=gte, lte=lte))


def _scroll_all(
    collection: str,
    *,
    scroll_filter: Any = None,
    with_vectors: bool = False,
    limit: int = 256,
) -> list[Any]:
    client = get_qdrant_client()
    out: list[Any] = []
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=with_vectors,
        )
        out.extend(records)
        if offset is None:
            break
    return out


def _must(*conditions: Any) -> Any:
    return _qm().Filter(must=list(conditions))


def _period_overlap_filter(period_start: date, period_end: date) -> Any:
    """brief_date <= period_end AND period_end >= period_start."""
    return _must(
        _range_ord("brief_date_ord", lte=_date_ord(period_end)),
        _range_ord("period_end_ord", gte=_date_ord(period_start)),
    )


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------


def index_news_items(
    items: Iterable[dict[str, Any]],
    *,
    indexed_by: str = "system",
    fallback_date: date | None = None,
    fetch_full: bool = True,
    client: HttpClient | None = None,
    browser_fetcher: Any = None,
    log: LogFn | None = None,
    database_url: str | None = None,  # noqa: ARG001 — совместимость с pg API
) -> dict[str, Any]:
    del database_url
    ensure_collections()
    qdrant = get_qdrant_client()

    def _log(msg: str) -> None:
        if log:
            log(msg)

    http = client or HttpClient(timeout=25, retries=2)
    prepared: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    raw_items = [it for it in items if str(it.get("status", "ok")) == "ok"]
    total = len(raw_items)
    _log(f"Новостей к индексации (Qdrant): {total}")

    for position, item in enumerate(raw_items, start=1):
        url = str(item.get("url", "")).strip()
        title = str(item.get("title", "")).strip()
        if not url or not title:
            continue
        news_date = _parse_news_date(str(item.get("date", "")), fallback_date)
        if news_date is None:
            continue
        key = (news_date.isoformat(), url)
        if key in seen:
            continue
        seen.add(key)

        parsed_content = str(item.get("content", "")).strip()
        if fetch_full:
            full_text, status = fetch_full_article_text(
                url, http, browser_fetcher=browser_fetcher, parsed_content=parsed_content
            )
        else:
            full_text, status = parsed_content, "parsed"

        full_text = (full_text or parsed_content or title).strip()
        chunks = chunk_text(full_text) or [full_text]
        document_id = _stable_u64("news-doc", news_date.isoformat(), url)
        prepared.append(
            {
                "document_id": document_id,
                "news_date": news_date,
                "source": str(item.get("source", "")),
                "category": str(item.get("category", "")),
                "title": title,
                "url": url,
                "summary": str(item.get("summary", "")),
                "full_text": full_text,
                "language": str(item.get("language", "und")),
                "keyword_block": str(item.get("keyword_block", "")),
                "keyword_match": str(item.get("keyword_match", "")),
                "full_text_status": status,
                "chunks": chunks,
            }
        )
        if position % 25 == 0 or position == total:
            _log(f"  обработано {position}/{total}")

    if not prepared:
        return {"documents": 0, "chunks": 0}

    all_chunks: list[str] = []
    for doc in prepared:
        all_chunks.extend(doc["chunks"])
    _log(f"Эмбеддинги: {len(all_chunks)} чанков из {len(prepared)} новостей…")
    vectors = _embed_all(all_chunks)

    inserted_docs = 0
    inserted_chunks = 0
    cursor_pos = 0
    qm = _qm()
    points: list[Any] = []

    for doc in prepared:
        doc_chunks: list[str] = doc["chunks"]
        doc_vectors = vectors[cursor_pos : cursor_pos + len(doc_chunks)]
        cursor_pos += len(doc_chunks)
        document_id = int(doc["document_id"])
        news_date: date = doc["news_date"]

        # Удаляем старые чанки этого документа (upsert документа + новые чанки).
        old_chunks = _scroll_all(
            COLLECTION_NEWS,
            scroll_filter=_must(
                _match("point_kind", _POINT_CHUNK),
                _match("document_id", document_id),
            ),
        )
        if old_chunks:
            qdrant.delete(
                collection_name=COLLECTION_NEWS,
                points_selector=qm.PointIdsList(points=[r.id for r in old_chunks]),
            )

        points.append(
            qm.PointStruct(
                id=document_id,
                vector=_placeholder_vector(),
                payload={
                    "point_kind": _POINT_DOCUMENT,
                    "document_id": document_id,
                    "news_date": news_date.isoformat(),
                    "news_date_ord": _date_ord(news_date),
                    "source": doc["source"],
                    "category": doc["category"],
                    "title": doc["title"],
                    "url": doc["url"],
                    "summary": doc["summary"],
                    "full_text": doc["full_text"],
                    "language": doc["language"],
                    "keyword_block": doc["keyword_block"],
                    "keyword_match": doc["keyword_match"],
                    "full_text_status": doc["full_text_status"],
                    "char_count": len(doc["full_text"]),
                    "indexed_by": indexed_by,
                    "chunks": len(doc_chunks),
                },
            )
        )
        for index, (chunk, vector) in enumerate(zip(doc_chunks, doc_vectors)):
            chunk_id = _stable_u64("news-chunk", str(document_id), str(index))
            points.append(
                qm.PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload={
                        "point_kind": _POINT_CHUNK,
                        "document_id": document_id,
                        "chunk_index": index,
                        "content": chunk,
                        "news_date": news_date.isoformat(),
                        "news_date_ord": _date_ord(news_date),
                        "source": doc["source"],
                        "title": doc["title"],
                        "url": doc["url"],
                    },
                )
            )
            inserted_chunks += 1
        inserted_docs += 1

    for start in range(0, len(points), 64):
        qdrant.upsert(collection_name=COLLECTION_NEWS, points=points[start : start + 64])

    fetched = sum(1 for d in prepared if d["full_text_status"] == "fetched")
    _log(
        f"Готово (Qdrant): {inserted_docs} новостей, {inserted_chunks} чанков "
        f"(полный текст догружен: {fetched})"
    )
    return {
        "documents": inserted_docs,
        "chunks": inserted_chunks,
        "full_text_fetched": fetched,
    }


def index_news_jsonl(
    jsonl_path: Path,
    *,
    indexed_by: str = "system",
    fallback_date: date | None = None,
    fetch_full: bool = True,
    client: HttpClient | None = None,
    browser_fetcher: Any = None,
    log: LogFn | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    if not jsonl_path.is_file():
        raise NewsRagError(f"JSONL не найден: {jsonl_path}")
    rows = load_news_jsonl(jsonl_path)
    return index_news_items(
        rows,
        indexed_by=indexed_by,
        fallback_date=fallback_date,
        fetch_full=fetch_full,
        client=client,
        browser_fetcher=browser_fetcher,
        log=log,
        database_url=database_url,
    )


def _payload_to_news_document(payload: dict[str, Any]) -> NewsDocument | None:
    news_date = _parse_payload_date(payload.get("news_date"))
    if news_date is None:
        return None
    return NewsDocument(
        id=int(payload.get("document_id") or 0),
        news_date=news_date,
        source=str(payload.get("source") or ""),
        category=str(payload.get("category") or ""),
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or ""),
        summary=str(payload.get("summary") or ""),
        full_text=str(payload.get("full_text") or ""),
        keyword_block=str(payload.get("keyword_block") or ""),
        full_text_status=str(payload.get("full_text_status") or ""),
    )


def fetch_news_documents_for_period(
    *,
    period_start: date,
    period_end: date,
    keyword_block: str | None = None,
    sources: Sequence[str] | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> list[NewsDocument]:
    del database_url
    ensure_collections()
    conditions: list[Any] = [
        _match("point_kind", _POINT_DOCUMENT),
        _range_ord("news_date_ord", gte=_date_ord(period_start), lte=_date_ord(period_end)),
    ]
    if keyword_block:
        conditions.append(_match("keyword_block", keyword_block))
    if sources is not None:
        if not sources:
            return []
        conditions.append(_match_any("source", list(sources)))

    records = _scroll_all(COLLECTION_NEWS, scroll_filter=_must(*conditions))
    docs: list[NewsDocument] = []
    for record in records:
        doc = _payload_to_news_document(record.payload or {})
        if doc:
            docs.append(doc)
    docs.sort(key=lambda d: (d.news_date, d.source, d.id))
    return docs


def search_news_chunks(
    *,
    query_text: str | None = None,
    query_vector: Sequence[float] | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    source_contains: str | None = None,
    top_k: int = 12,
    candidate_limit: int | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> list[NewsSearchHit]:
    del database_url
    ensure_collections()
    if query_vector is None:
        if not query_text:
            raise NewsRagError("Нужен query_text или query_vector")
        query_vector = embed_texts([query_text])[0]

    conditions: list[Any] = [_match("point_kind", _POINT_CHUNK)]
    if period_start and period_end:
        conditions.append(
            _range_ord(
                "news_date_ord",
                gte=_date_ord(period_start),
                lte=_date_ord(period_end),
            )
        )
    # MatchText требует текстовый индекс; фильтруем substring в Python.
    fetch_limit = candidate_limit or top_k
    if source_contains:
        fetch_limit = max(fetch_limit * 5, 50)

    results = get_qdrant_client().search(
        collection_name=COLLECTION_NEWS,
        query_vector=list(query_vector),
        query_filter=_must(*conditions),
        limit=fetch_limit,
        with_payload=True,
    )
    needle = (source_contains or "").lower()
    hits: list[NewsSearchHit] = []
    for point in results:
        payload = point.payload or {}
        source = str(payload.get("source") or "")
        if needle and needle not in source.lower():
            continue
        news_date = _parse_payload_date(payload.get("news_date"))
        if news_date is None:
            continue
        hits.append(
            NewsSearchHit(
                document_id=int(payload.get("document_id") or 0),
                chunk_index=int(payload.get("chunk_index") or 0),
                content=str(payload.get("content") or ""),
                news_date=news_date,
                source=source,
                title=str(payload.get("title") or ""),
                url=str(payload.get("url") or ""),
                score=float(point.score or 0.0),
            )
        )
        if len(hits) >= (candidate_limit or top_k):
            break
    return hits


def get_news_document(
    document_id: int,
    *,
    database_url: str | None = None,  # noqa: ARG001
) -> NewsDocument | None:
    del database_url
    ensure_collections()
    records = _scroll_all(
        COLLECTION_NEWS,
        scroll_filter=_must(
            _match("point_kind", _POINT_DOCUMENT),
            _match("document_id", int(document_id)),
        ),
    )
    if not records:
        # fallback: retrieve by point id
        points = get_qdrant_client().retrieve(
            collection_name=COLLECTION_NEWS,
            ids=[int(document_id)],
            with_payload=True,
        )
        records = [p for p in points if (p.payload or {}).get("point_kind") == _POINT_DOCUMENT]
    if not records:
        return None
    return _payload_to_news_document(records[0].payload or {})


def get_news_chunks_for_documents(
    document_ids: Sequence[int],
    *,
    database_url: str | None = None,  # noqa: ARG001
) -> list[NewsSearchHit]:
    del database_url
    ids = sorted({int(i) for i in document_ids if int(i) > 0})
    if not ids:
        return []
    ensure_collections()
    records = _scroll_all(
        COLLECTION_NEWS,
        scroll_filter=_must(
            _match("point_kind", _POINT_CHUNK),
            _match_any("document_id", ids),
        ),
    )
    hits: list[NewsSearchHit] = []
    for record in records:
        payload = record.payload or {}
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        news_date = _parse_payload_date(payload.get("news_date"))
        if news_date is None:
            continue
        hits.append(
            NewsSearchHit(
                document_id=int(payload.get("document_id") or 0),
                chunk_index=int(payload.get("chunk_index") or 0),
                content=content,
                news_date=news_date,
                source=str(payload.get("source") or ""),
                title=str(payload.get("title") or ""),
                url=str(payload.get("url") or ""),
                score=1.0,
            )
        )
    hits.sort(key=lambda h: (h.document_id, h.chunk_index))
    return hits


def period_news_stats(
    *,
    period_start: date,
    period_end: date,
    sources: Sequence[str] | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    del database_url
    docs = fetch_news_documents_for_period(
        period_start=period_start,
        period_end=period_end,
        sources=sources,
    )
    days = {d.news_date for d in docs}
    return {
        "documents": len(docs),
        "days": len(days),
        "full_text_fetched": sum(1 for d in docs if d.full_text_status == "fetched"),
    }


def list_period_news_sources(
    *,
    period_start: date,
    period_end: date,
    database_url: str | None = None,  # noqa: ARG001
) -> list[dict[str, Any]]:
    del database_url
    docs = fetch_news_documents_for_period(
        period_start=period_start,
        period_end=period_end,
    )
    counts: dict[str, int] = {}
    for doc in docs:
        counts[doc.source] = counts.get(doc.source, 0) + 1
    return [{"name": name, "count": counts[name]} for name in sorted(counts)]


# ---------------------------------------------------------------------------
# Attachments / brief_index
# ---------------------------------------------------------------------------


def find_document_by_content_hash(content_hash: str) -> dict[str, Any] | None:
    ensure_collections()
    records = _scroll_all(
        COLLECTION_BRIEF,
        scroll_filter=_must(
            _match("point_kind", _POINT_DOCUMENT),
            _match("content_hash", content_hash),
        ),
    )
    if not records:
        return None
    payload = records[0].payload or {}
    return {
        "title": payload.get("title"),
        "document_type": payload.get("document_type"),
        "brief_date": payload.get("brief_date"),
        "indexed_by": payload.get("indexed_by"),
        "created_at": payload.get("created_at"),
    }


def index_attachment_document(
    *,
    file_path: Path,
    raw_bytes: bytes,
    brief_date: date,
    period_end: date | None,
    document_type: str,
    indexed_by: str,
    title: str = "",
    database_url: str | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    del database_url
    ensure_collections()
    if not file_path.is_file():
        raise BriefIndexError(f"Файл не найден: {file_path}")

    document_type = (document_type or "").strip()
    if len(document_type) < 2:
        raise BriefIndexError("Укажите тип документа (например: PDF отчёт, Kallanish).")

    digest = _content_hash(raw_bytes)
    existing = find_document_by_content_hash(digest)
    if existing:
        raise DuplicateDocumentError(
            "Этот документ уже есть в RAG "
            f"(тип: {existing.get('document_type') or '—'}, "
            f"дата: {existing.get('brief_date')}, "
            f"загрузил: {existing.get('indexed_by') or '—'})."
        )

    text = extract_document_text(file_path)
    if len(text) < 50:
        raise BriefIndexError("Слишком мало текста в документе для индексации.")

    chunks = chunk_text(text)
    vectors = embed_texts(chunks)
    end = period_end or brief_date
    doc_title = (title or file_path.name).strip()
    source_type = _source_type_for_path(file_path)
    document_id = _stable_u64("brief-doc", digest)
    created_at = datetime.now(timezone.utc).isoformat()

    qm = _qm()
    points: list[Any] = [
        qm.PointStruct(
            id=document_id,
            vector=_placeholder_vector(),
            payload={
                "point_kind": _POINT_DOCUMENT,
                "document_id": document_id,
                "brief_date": brief_date.isoformat(),
                "brief_date_ord": _date_ord(brief_date),
                "period_end": end.isoformat(),
                "period_end_ord": _date_ord(end),
                "brief_kind": "full",
                "source_type": source_type,
                "document_type": document_type,
                "title": doc_title,
                "file_path": str(file_path),
                "full_text": text,
                "indexed_by": indexed_by,
                "content_hash": digest,
                "created_at": created_at,
                "chars": len(text),
                "chunks": len(chunks),
            },
        )
    ]
    for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
        chunk_id = _stable_u64("brief-chunk", str(document_id), str(index))
        points.append(
            qm.PointStruct(
                id=chunk_id,
                vector=vector,
                payload={
                    "point_kind": _POINT_CHUNK,
                    "document_id": document_id,
                    "chunk_index": index,
                    "content": chunk,
                    "brief_date": brief_date.isoformat(),
                    "brief_date_ord": _date_ord(brief_date),
                    "period_end": end.isoformat(),
                    "period_end_ord": _date_ord(end),
                    "brief_kind": "full",
                    "source_type": source_type,
                    "document_type": document_type,
                    "title": doc_title,
                },
            )
        )

    get_qdrant_client().upsert(collection_name=COLLECTION_BRIEF, points=points)
    return {
        "document_id": document_id,
        "brief_date": brief_date.isoformat(),
        "period_end": end.isoformat(),
        "document_type": document_type,
        "source_type": source_type,
        "chunks": len(chunks),
        "chars": len(text),
        "title": doc_title,
    }


def list_attachment_documents(
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> list[dict[str, Any]]:
    del database_url
    ensure_collections()
    conditions: list[Any] = [
        _match("point_kind", _POINT_DOCUMENT),
        _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
    ]
    if period_start and period_end:
        conditions.extend(_period_overlap_filter(period_start, period_end).must or [])

    records = _scroll_all(COLLECTION_BRIEF, scroll_filter=_must(*conditions))
    rows: list[dict[str, Any]] = []
    for record in records:
        payload = record.payload or {}
        brief = _parse_payload_date(payload.get("brief_date"))
        pend = _parse_payload_date(payload.get("period_end")) or brief
        if brief is None:
            continue
        rows.append(
            {
                "id": int(payload.get("document_id") or record.id),
                "brief_date": brief.isoformat(),
                "period_end": pend.isoformat() if pend else brief.isoformat(),
                "document_type": str(payload.get("document_type") or ""),
                "source_type": str(payload.get("source_type") or ""),
                "title": str(payload.get("title") or ""),
                "indexed_by": str(payload.get("indexed_by") or ""),
                "created_at": str(payload.get("created_at") or ""),
                "file_path": str(payload.get("file_path") or ""),
                "chunks": int(payload.get("chunks") or 0),
            }
        )
    rows.sort(key=lambda r: (r.get("created_at") or "", r["id"]), reverse=True)
    return rows


def delete_attachment_document(
    document_id: int,
    *,
    allowed_roots: Sequence[Path] | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> dict[str, Any]:
    del database_url
    ensure_collections()
    qdrant = get_qdrant_client()
    docs = _scroll_all(
        COLLECTION_BRIEF,
        scroll_filter=_must(
            _match("point_kind", _POINT_DOCUMENT),
            _match("document_id", int(document_id)),
            _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
        ),
    )
    if not docs:
        raise BriefIndexError("Документ не найден или это не PDF/Kallanish.")
    payload = docs[0].payload or {}
    file_path = str(payload.get("file_path") or "")

    related = _scroll_all(
        COLLECTION_BRIEF,
        scroll_filter=_must(_match("document_id", int(document_id))),
    )
    if related:
        qdrant.delete(
            collection_name=COLLECTION_BRIEF,
            points_selector=_qm().PointIdsList(points=[r.id for r in related]),
        )

    file_deleted = False
    if allowed_roots and file_path:
        file_deleted = _safe_unlink_attachment(file_path, allowed_roots=allowed_roots)

    return {
        "deleted": True,
        "document_id": document_id,
        "title": str(payload.get("title") or ""),
        "document_type": str(payload.get("document_type") or ""),
        "file_deleted": file_deleted,
    }


def search_attachment_chunks(
    *,
    query_text: str | None = None,
    query_vector: Sequence[float] | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
    document_type_contains: str | None = None,
    top_k: int = 8,
    candidate_limit: int | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> list[AttachmentSearchHit]:
    del database_url
    ensure_collections()
    if query_vector is None:
        if not query_text:
            raise BriefIndexError("Нужен query_text или query_vector")
        query_vector = embed_texts([query_text])[0]

    conditions: list[Any] = [
        _match("point_kind", _POINT_CHUNK),
        _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
    ]
    if period_start and period_end:
        conditions.extend(_period_overlap_filter(period_start, period_end).must or [])
    fetch_limit = candidate_limit or top_k
    if document_type_contains:
        fetch_limit = max(fetch_limit * 5, 50)

    results = get_qdrant_client().search(
        collection_name=COLLECTION_BRIEF,
        query_vector=list(query_vector),
        query_filter=_must(*conditions),
        limit=fetch_limit,
        with_payload=True,
    )
    needle = (document_type_contains or "").lower()
    hits: list[AttachmentSearchHit] = []
    for point in results:
        payload = point.payload or {}
        document_type = str(payload.get("document_type") or "")
        if needle and needle not in document_type.lower():
            continue
        brief_date = _parse_payload_date(payload.get("brief_date"))
        if brief_date is None:
            continue
        hits.append(
            AttachmentSearchHit(
                document_id=int(payload.get("document_id") or 0),
                chunk_index=int(payload.get("chunk_index") or 0),
                content=str(payload.get("content") or ""),
                brief_date=brief_date,
                document_type=document_type,
                title=str(payload.get("title") or ""),
                score=float(point.score or 0.0),
            )
        )
        if len(hits) >= (candidate_limit or top_k):
            break
    return hits


def get_attachment_document(
    document_id: int,
    *,
    database_url: str | None = None,  # noqa: ARG001
) -> AttachmentDocument | None:
    del database_url
    ensure_collections()
    records = _scroll_all(
        COLLECTION_BRIEF,
        scroll_filter=_must(
            _match("point_kind", _POINT_DOCUMENT),
            _match("document_id", int(document_id)),
            _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
        ),
    )
    if not records:
        return None
    payload = records[0].payload or {}
    brief_date = _parse_payload_date(payload.get("brief_date"))
    if brief_date is None:
        return None
    return AttachmentDocument(
        id=int(payload.get("document_id") or document_id),
        brief_date=brief_date,
        period_end=_parse_payload_date(payload.get("period_end")),
        document_type=str(payload.get("document_type") or ""),
        title=str(payload.get("title") or ""),
        full_text=str(payload.get("full_text") or ""),
    )


def get_attachment_documents_by_ids(
    document_ids: Sequence[int],
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> list[AttachmentDocument]:
    del database_url
    ids = sorted({int(i) for i in document_ids if int(i) > 0})
    if not ids:
        return []
    ensure_collections()
    conditions: list[Any] = [
        _match("point_kind", _POINT_DOCUMENT),
        _match_any("document_id", ids),
        _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
    ]
    if period_start is not None and period_end is not None:
        conditions.extend(_period_overlap_filter(period_start, period_end).must or [])

    records = _scroll_all(COLLECTION_BRIEF, scroll_filter=_must(*conditions))
    docs: list[AttachmentDocument] = []
    for record in records:
        payload = record.payload or {}
        full_text = str(payload.get("full_text") or "").strip()
        if not full_text:
            continue
        brief_date = _parse_payload_date(payload.get("brief_date"))
        if brief_date is None:
            continue
        docs.append(
            AttachmentDocument(
                id=int(payload.get("document_id") or 0),
                brief_date=brief_date,
                period_end=_parse_payload_date(payload.get("period_end")),
                document_type=str(payload.get("document_type") or ""),
                title=str(payload.get("title") or ""),
                full_text=full_text,
            )
        )
    docs.sort(key=lambda d: (d.brief_date, d.id))
    return docs


def get_attachment_chunks_for_documents(
    document_ids: Sequence[int],
    *,
    database_url: str | None = None,  # noqa: ARG001
) -> list[AttachmentSearchHit]:
    del database_url
    ids = sorted({int(i) for i in document_ids if int(i) > 0})
    if not ids:
        return []
    ensure_collections()
    records = _scroll_all(
        COLLECTION_BRIEF,
        scroll_filter=_must(
            _match("point_kind", _POINT_CHUNK),
            _match_any("document_id", ids),
            _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
        ),
    )
    hits: list[AttachmentSearchHit] = []
    for record in records:
        payload = record.payload or {}
        content = str(payload.get("content") or "").strip()
        if not content:
            continue
        brief_date = _parse_payload_date(payload.get("brief_date"))
        if brief_date is None:
            continue
        hits.append(
            AttachmentSearchHit(
                document_id=int(payload.get("document_id") or 0),
                chunk_index=int(payload.get("chunk_index") or 0),
                content=content,
                brief_date=brief_date,
                document_type=str(payload.get("document_type") or ""),
                title=str(payload.get("title") or ""),
                score=1.0,
            )
        )
    hits.sort(key=lambda h: (h.document_id, h.chunk_index))
    return hits


def get_attachment_section(
    document_id: int,
    chunk_index: int | None,
    *,
    radius: int = 2,
    max_chars: int = 16_000,
    database_url: str | None = None,
) -> tuple[AttachmentDocument | None, str]:
    doc = get_attachment_document(document_id, database_url=database_url)
    if not doc:
        return None, ""
    chunks = get_attachment_chunks_for_documents([document_id], database_url=database_url)
    if not chunks:
        text = (doc.full_text or "").strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return doc, text
    by_index = {hit.chunk_index: hit.content for hit in chunks}
    if chunk_index is None or chunk_index not in by_index:
        ordered = sorted(by_index)
        selected = ordered[: max(3, radius + 2)]
        text = "\n\n".join(by_index[i] for i in selected).strip()
        if len(doc.full_text or "") > max_chars:
            text = text[:max_chars].rstrip() + "…"
        return doc, text
    start = max(min(by_index), chunk_index - radius)
    end = min(max(by_index), chunk_index + radius)
    parts = [by_index[i] for i in range(start, end + 1) if i in by_index]
    text = _join_chunk_texts(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return doc, text


def retrieve_attachment_chunks_for_period(
    *,
    period_start: date,
    period_end: date,
    query_text: str,
    top_k: int = 16,
    document_ids: Sequence[int] | None = None,
    database_url: str | None = None,  # noqa: ARG001
) -> list[IndexedChunk]:
    del database_url
    if document_ids is not None and not document_ids:
        return []
    ensure_collections()
    query_vector = embed_texts([query_text])[0]
    conditions: list[Any] = [
        _match("point_kind", _POINT_CHUNK),
        _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
    ]
    conditions.extend(_period_overlap_filter(period_start, period_end).must or [])
    if document_ids is not None:
        conditions.append(_match_any("document_id", [int(i) for i in document_ids]))

    results = get_qdrant_client().search(
        collection_name=COLLECTION_BRIEF,
        query_vector=query_vector,
        query_filter=_must(*conditions),
        limit=top_k,
        with_payload=True,
    )
    out: list[IndexedChunk] = []
    for point in results:
        payload = point.payload or {}
        brief_date = _parse_payload_date(payload.get("brief_date"))
        if brief_date is None:
            continue
        source_type = str(payload.get("source_type") or "")
        out.append(
            IndexedChunk(
                content=str(payload.get("content") or ""),
                brief_date=brief_date,
                brief_kind=str(payload.get("brief_kind") or "full"),
                source_type=source_type,
                document_type=str(payload.get("document_type") or source_type),
                score=float(point.score or 0.0),
            )
        )
    return out


def retrieve_chunks_for_period(
    *,
    period_start: date,
    period_end: date,
    brief_kind: str,
    query_text: str,
    top_k: int = 40,
    database_url: str | None = None,  # noqa: ARG001
) -> list[IndexedChunk]:
    del database_url
    ensure_collections()
    query_vector = embed_texts([query_text])[0]
    # Вложения ИЛИ daily_docx нужного brief_kind
    qm = _qm()
    query_filter = qm.Filter(
        must=[
            _match("point_kind", _POINT_CHUNK),
            *(_period_overlap_filter(period_start, period_end).must or []),
        ],
        should=[
            _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
            qm.Filter(
                must=[
                    _match("source_type", "daily_docx"),
                    _match("brief_kind", brief_kind),
                ]
            ),
        ],
        min_should=qm.MinShould(min_count=1),
    )
    results = get_qdrant_client().search(
        collection_name=COLLECTION_BRIEF,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=top_k,
        with_payload=True,
    )
    out: list[IndexedChunk] = []
    for point in results:
        payload = point.payload or {}
        brief_date = _parse_payload_date(payload.get("brief_date"))
        if brief_date is None:
            continue
        source_type = str(payload.get("source_type") or "")
        out.append(
            IndexedChunk(
                content=str(payload.get("content") or ""),
                brief_date=brief_date,
                brief_kind=str(payload.get("brief_kind") or brief_kind),
                source_type=source_type,
                document_type=str(payload.get("document_type") or source_type),
                score=float(point.score or 0.0),
            )
        )
    return out


def count_attachment_documents(
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    database_url: str | None = None,
) -> int:
    return len(
        list_attachment_documents(
            period_start=period_start,
            period_end=period_end,
            database_url=database_url,
        )
    )


def list_index_coverage(
    *,
    period_start: date,
    period_end: date,
    brief_kind: str,
    database_url: str | None = None,  # noqa: ARG001
) -> list[dict[str, Any]]:
    del database_url
    ensure_collections()
    qm = _qm()
    query_filter = qm.Filter(
        must=[
            _match("point_kind", _POINT_DOCUMENT),
            *(_period_overlap_filter(period_start, period_end).must or []),
        ],
        should=[
            _match_any("source_type", list(ATTACHMENT_SOURCE_TYPES)),
            qm.Filter(
                must=[
                    _match("source_type", "daily_docx"),
                    _match("brief_kind", brief_kind),
                ]
            ),
        ],
        min_should=qm.MinShould(min_count=1),
    )
    records = _scroll_all(COLLECTION_BRIEF, scroll_filter=query_filter)
    rows: list[dict[str, Any]] = []
    for record in records:
        payload = record.payload or {}
        brief = _parse_payload_date(payload.get("brief_date"))
        if brief is None:
            continue
        rows.append(
            {
                "brief_date": brief.isoformat(),
                "brief_kind": str(payload.get("brief_kind") or ""),
                "source_type": str(payload.get("source_type") or ""),
                "document_type": str(
                    payload.get("document_type") or payload.get("source_type") or ""
                ),
                "indexed_by": str(payload.get("indexed_by") or ""),
                "created_at": str(payload.get("created_at") or ""),
                "title": str(payload.get("title") or ""),
                "period_end": str(payload.get("period_end") or brief.isoformat()),
            }
        )
    rows.sort(key=lambda r: (r["brief_date"], r["document_type"], r["source_type"]))
    return rows
