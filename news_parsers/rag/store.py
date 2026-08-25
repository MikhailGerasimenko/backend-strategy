"""Хранение и поиск чанков брифов в pgvector."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from psycopg.types.json import Jsonb

from ..database import apply_schema_with_cursor, register_pgvector, _connect
from ..document_text import extract_document_text
from ..llm.embeddings import embed_texts
from .chunking import chunk_text

ATTACHMENT_SOURCE_TYPES = ("pdf_report", "docx_report")


class BriefIndexError(RuntimeError):
    pass


class DuplicateDocumentError(BriefIndexError):
    pass


@dataclass(frozen=True)
class IndexedChunk:
    content: str
    brief_date: date
    brief_kind: str
    source_type: str
    document_type: str
    score: float


@dataclass(frozen=True)
class AttachmentSearchHit:
    document_id: int
    chunk_index: int
    content: str
    brief_date: date
    document_type: str
    title: str
    score: float


@dataclass(frozen=True)
class AttachmentDocument:
    id: int
    brief_date: date
    period_end: date | None
    document_type: str
    title: str
    full_text: str


def _document_period_overlap_sql(document_alias: str = "d") -> str:
    return (
        f"{document_alias}.brief_date <= %s "
        f"AND COALESCE({document_alias}.period_end, {document_alias}.brief_date) >= %s"
    )


def database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        raise BriefIndexError(
            "DATABASE_URL не задан. Для еженедельных брифов и RAG нужен PostgreSQL с pgvector."
        )
    return url


def _content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def find_document_by_content_hash(database_url: str, content_hash: str) -> dict[str, Any] | None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT title, document_type, brief_date::text, indexed_by, created_at::text
                FROM brief_index_documents
                WHERE content_hash = %s
                LIMIT 1
                """,
                (content_hash,),
            )
            row = cur.fetchone()
    if not row:
        return None
    return {
        "title": row[0],
        "document_type": row[1],
        "brief_date": row[2],
        "indexed_by": row[3],
        "created_at": row[4],
    }


def list_index_coverage(
    database_url: str,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str,
) -> list[dict[str, Any]]:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT brief_date, brief_kind, source_type, document_type,
                       indexed_by, created_at::text, title, period_end::text
                FROM brief_index_documents d
                WHERE {_document_period_overlap_sql("d")}
                  AND (
                    d.source_type = ANY(%s)
                    OR (d.source_type = 'daily_docx' AND d.brief_kind = %s)
                  )
                ORDER BY brief_date, document_type, source_type
                """,
                (period_end, period_start, list(ATTACHMENT_SOURCE_TYPES), brief_kind),
            )
            rows = cur.fetchall()
    return [
        {
            "brief_date": row[0].isoformat(),
            "brief_kind": row[1],
            "source_type": row[2],
            "document_type": row[3] or row[2],
            "indexed_by": row[4],
            "created_at": row[5],
            "title": row[6],
            "period_end": row[7],
        }
        for row in rows
    ]


def _source_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "pdf_report"
    if suffix in {".docx", ".txt"}:
        # TXT massmail Kallanish индексируем как docx_report — тот же канал вложений.
        return "docx_report"
    raise BriefIndexError(f"Неподдерживаемый формат файла: {path.name}")


def index_attachment_document(
    database_url: str,
    *,
    file_path: Path,
    raw_bytes: bytes,
    brief_date: date,
    period_end: date | None,
    document_type: str,
    indexed_by: str,
    title: str = "",
) -> dict[str, Any]:
    if not file_path.is_file():
        raise BriefIndexError(f"Файл не найден: {file_path}")

    document_type = (document_type or "").strip()
    if len(document_type) < 2:
        raise BriefIndexError("Укажите тип документа (например: PDF отчёт, Kallanish).")

    digest = _content_hash(raw_bytes)
    existing = find_document_by_content_hash(database_url, digest)
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

    with _connect(database_url) as conn:
        register_pgvector(conn)
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                INSERT INTO brief_index_documents (
                    brief_date, period_end, brief_kind, source_type, document_type,
                    title, file_path, full_text, indexed_by, content_hash, metadata
                )
                VALUES (%s, %s, 'full', %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    brief_date,
                    end,
                    source_type,
                    document_type,
                    doc_title,
                    str(file_path),
                    text,
                    indexed_by,
                    digest,
                    Jsonb(
                        {
                            "chars": len(text),
                            "chunks": len(chunks),
                            "filename": file_path.name,
                        }
                    ),
                ),
            )
            document_id = int(cur.fetchone()[0])
            for index, (chunk, vector) in enumerate(zip(chunks, vectors)):
                cur.execute(
                    """
                    INSERT INTO brief_index_chunks (
                        document_id, chunk_index, content,
                        brief_date, period_end, brief_kind, source_type, embedding
                    )
                    VALUES (%s, %s, %s, %s, %s, 'full', %s, %s)
                    """,
                    (
                        document_id,
                        index,
                        chunk,
                        brief_date,
                        end,
                        source_type,
                        vector,
                    ),
                )
        conn.commit()

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


def index_pdf_report(
    database_url: str,
    *,
    pdf_path: Path,
    brief_date: date,
    period_end: date | None,
    brief_kind: str,
    indexed_by: str,
    title: str = "",
) -> dict[str, Any]:
    raw_bytes = pdf_path.read_bytes()
    return index_attachment_document(
        database_url,
        file_path=pdf_path,
        raw_bytes=raw_bytes,
        brief_date=brief_date,
        period_end=period_end,
        document_type="PDF отчёт",
        indexed_by=indexed_by,
        title=title,
    )


def retrieve_chunks_for_period(
    database_url: str,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str,
    query_text: str,
    top_k: int = 40,
) -> list[IndexedChunk]:
    vectors = embed_texts([query_text])
    query_vector = vectors[0]

    with _connect(database_url) as conn:
        register_pgvector(conn)
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT c.content, c.brief_date, c.brief_kind, c.source_type,
                       COALESCE(d.document_type, c.source_type) AS document_type,
                       1 - (c.embedding <=> %s::vector) AS score
                FROM brief_index_chunks c
                JOIN brief_index_documents d ON d.id = c.document_id
                WHERE {_document_period_overlap_sql("d")}
                  AND (
                    c.source_type = ANY(%s)
                    OR (c.source_type = 'daily_docx' AND c.brief_kind = %s)
                  )
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                (
                    query_vector,
                    period_end,
                    period_start,
                    list(ATTACHMENT_SOURCE_TYPES),
                    brief_kind,
                    query_vector,
                    top_k,
                ),
            )
            rows = cur.fetchall()

    return [
        IndexedChunk(
            content=str(row[0]),
            brief_date=row[1],
            brief_kind=str(row[2]),
            source_type=str(row[3]),
            document_type=str(row[4] or row[3]),
            score=float(row[5] or 0.0),
        )
        for row in rows
    ]


def retrieve_attachment_chunks_for_period(
    database_url: str,
    *,
    period_start: date,
    period_end: date,
    query_text: str,
    top_k: int = 16,
    document_ids: Sequence[int] | None = None,
) -> list[IndexedChunk]:
    """Чанки PDF/Word за период. document_ids=None — все; [] — ничего; иначе только выбранные."""
    if document_ids is not None and not document_ids:
        return []

    vectors = embed_texts([query_text])
    query_vector = vectors[0]

    where = [
        _document_period_overlap_sql("d"),
        "c.source_type = ANY(%s)",
    ]
    params: list[Any] = [
        query_vector,
        period_end,
        period_start,
        list(ATTACHMENT_SOURCE_TYPES),
    ]
    if document_ids is not None:
        where.append("d.id = ANY(%s)")
        params.append([int(i) for i in document_ids])
    params.extend([query_vector, top_k])

    with _connect(database_url) as conn:
        register_pgvector(conn)
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT c.content, c.brief_date, c.brief_kind, c.source_type,
                       COALESCE(d.document_type, c.source_type) AS document_type,
                       1 - (c.embedding <=> %s::vector) AS score
                FROM brief_index_chunks c
                JOIN brief_index_documents d ON d.id = c.document_id
                WHERE {" AND ".join(where)}
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

    return [
        IndexedChunk(
            content=str(row[0]),
            brief_date=row[1],
            brief_kind=str(row[2]),
            source_type=str(row[3]),
            document_type=str(row[4] or row[3]),
            score=float(row[5] or 0.0),
        )
        for row in rows
    ]


def search_attachment_chunks(
    database_url: str,
    *,
    query_text: str,
    period_start: date | None = None,
    period_end: date | None = None,
    document_type_contains: str | None = None,
    top_k: int = 8,
    candidate_limit: int | None = None,
) -> list[AttachmentSearchHit]:
    """Семантический поиск по PDF/Word-документам (для ИИ-агента и брифов)."""
    query_vector = embed_texts([query_text])[0]
    where_parts = ["c.source_type = ANY(%s)"]
    params: list[Any] = [query_vector, list(ATTACHMENT_SOURCE_TYPES)]
    if period_start and period_end:
        where_parts.insert(0, _document_period_overlap_sql("d"))
        params.insert(1, period_end)
        params.insert(2, period_start)
    if document_type_contains:
        where_parts.append("d.document_type ILIKE %s")
        params.append(f"%{document_type_contains}%")
    where = " AND ".join(where_parts)
    limit = candidate_limit or top_k
    params.extend([query_vector, limit])
    with _connect(database_url) as conn:
        register_pgvector(conn)
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT c.document_id, c.chunk_index, c.content, c.brief_date,
                       COALESCE(d.document_type, c.source_type) AS document_type,
                       d.title,
                       1 - (c.embedding <=> %s::vector) AS score
                FROM brief_index_chunks c
                JOIN brief_index_documents d ON d.id = c.document_id
                WHERE {where}
                ORDER BY c.embedding <=> %s::vector
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
    return [
        AttachmentSearchHit(
            document_id=int(row[0]),
            chunk_index=int(row[1]),
            content=str(row[2]),
            brief_date=row[3],
            document_type=str(row[4] or ""),
            title=str(row[5] or ""),
            score=float(row[6] or 0.0),
        )
        for row in rows
    ]


def get_attachment_chunks_for_documents(
    database_url: str,
    document_ids: Sequence[int],
) -> list[AttachmentSearchHit]:
    """Все чанки выбранных документов по порядку chunk_index (для агента без top-k)."""
    ids = sorted({int(i) for i in document_ids if int(i) > 0})
    if not ids:
        return []
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT c.document_id, c.chunk_index, c.content, c.brief_date,
                       COALESCE(d.document_type, c.source_type) AS document_type,
                       d.title
                FROM brief_index_chunks c
                JOIN brief_index_documents d ON d.id = c.document_id
                WHERE c.document_id = ANY(%s)
                  AND d.source_type = ANY(%s)
                ORDER BY c.document_id, c.chunk_index
                """,
                (ids, list(ATTACHMENT_SOURCE_TYPES)),
            )
            rows = cur.fetchall()
    return [
        AttachmentSearchHit(
            document_id=int(row[0]),
            chunk_index=int(row[1]),
            content=str(row[2]),
            brief_date=row[3],
            document_type=str(row[4] or ""),
            title=str(row[5] or ""),
            score=1.0,
        )
        for row in rows
        if str(row[2] or "").strip()
    ]


def get_attachment_section(
    database_url: str,
    document_id: int,
    chunk_index: int | None,
    *,
    radius: int = 2,
    max_chars: int = 16_000,
) -> tuple[AttachmentDocument | None, str]:
    """Читаемый фрагмент вложения вокруг процитированного чанка (не весь Kallanish)."""
    doc = get_attachment_document(database_url, document_id)
    if not doc:
        return None, ""
    chunks = get_attachment_chunks_for_documents(database_url, [document_id])
    if not chunks:
        text = (doc.full_text or "").strip()
        if len(text) > max_chars:
            text = text[: max_chars - 1].rstrip() + "…"
        return doc, text
    by_index = {hit.chunk_index: hit.content for hit in chunks}
    if chunk_index is None or chunk_index not in by_index:
        # Нет якоря — первые чанки, не весь многостраничный обзор.
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


def _join_chunk_texts(parts: Sequence[str]) -> str:
    cleaned = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not cleaned:
        return ""
    out = cleaned[0]
    for nxt in cleaned[1:]:
        overlap = 0
        limit = min(len(out), len(nxt), 240)
        for size in range(limit, 39, -1):
            if out.endswith(nxt[:size]):
                overlap = size
                break
        piece = nxt[overlap:].lstrip() if overlap else nxt
        if piece:
            out = f"{out}\n\n{piece}"
    return out.strip()


def get_attachment_document(
    database_url: str, document_id: int
) -> AttachmentDocument | None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT id, brief_date, period_end, document_type, title, full_text
                FROM brief_index_documents
                WHERE id = %s
                  AND source_type = ANY(%s)
                """,
                (document_id, list(ATTACHMENT_SOURCE_TYPES)),
            )
            row = cur.fetchone()
    if not row:
        return None
    return AttachmentDocument(
        id=int(row[0]),
        brief_date=row[1],
        period_end=row[2],
        document_type=str(row[3] or ""),
        title=str(row[4] or ""),
        full_text=str(row[5] or ""),
    )


def get_attachment_documents_by_ids(
    database_url: str,
    document_ids: Sequence[int],
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[AttachmentDocument]:
    """Полные тексты выбранных PDF/Word/TXT для map-reduce (не top-k чанки).

    Если задан период — оставляем только документы, пересекающие [period_start, period_end]
    (brief_date…period_end), даже если в UI отметили лишние id.
    """
    ids = sorted({int(i) for i in document_ids if int(i) > 0})
    if not ids:
        return []
    where = ["id = ANY(%s)", "source_type = ANY(%s)"]
    params: list[Any] = [ids, list(ATTACHMENT_SOURCE_TYPES)]
    if period_start is not None and period_end is not None:
        where.append("brief_date <= %s AND COALESCE(period_end, brief_date) >= %s")
        params.extend([period_end, period_start])
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT id, brief_date, period_end, document_type, title, full_text
                FROM brief_index_documents
                WHERE {" AND ".join(where)}
                ORDER BY brief_date, id
                """,
                params,
            )
            rows = cur.fetchall()
    return [
        AttachmentDocument(
            id=int(row[0]),
            brief_date=row[1],
            period_end=row[2],
            document_type=str(row[3] or ""),
            title=str(row[4] or ""),
            full_text=str(row[5] or ""),
        )
        for row in rows
        if str(row[5] or "").strip()
    ]


def count_attachment_documents(
    database_url: str,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> int:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            if period_start and period_end:
                cur.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM brief_index_documents d
                    WHERE {_document_period_overlap_sql("d")}
                      AND d.source_type = ANY(%s)
                    """,
                    (period_end, period_start, list(ATTACHMENT_SOURCE_TYPES)),
                )
            else:
                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM brief_index_documents
                    WHERE source_type = ANY(%s)
                    """,
                    (list(ATTACHMENT_SOURCE_TYPES),),
                )
            row = cur.fetchone()
    return int(row[0] or 0)


def list_attachment_documents(
    database_url: str,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[dict[str, Any]]:
    repair_misclassified_attachments(database_url)

    params: list[Any] = [list(ATTACHMENT_SOURCE_TYPES)]
    where = "d.source_type = ANY(%s)"
    if period_start and period_end:
        where += f" AND {_document_period_overlap_sql('d')}"
        params.extend([period_end, period_start])

    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT d.id, d.brief_date, d.period_end, d.document_type, d.source_type,
                       d.title, d.indexed_by, d.created_at::text, d.file_path,
                       COALESCE(c.chunk_count, 0)
                FROM brief_index_documents d
                LEFT JOIN (
                    SELECT document_id, COUNT(*) AS chunk_count
                    FROM brief_index_chunks
                    GROUP BY document_id
                ) c ON c.document_id = d.id
                WHERE {where}
                ORDER BY d.created_at DESC, d.id DESC
                """,
                params,
            )
            rows = cur.fetchall()

    return [
        {
            "id": int(row[0]),
            "brief_date": row[1].isoformat(),
            "period_end": row[2].isoformat() if row[2] else row[1].isoformat(),
            "document_type": str(row[3] or ""),
            "source_type": str(row[4] or ""),
            "title": str(row[5] or ""),
            "indexed_by": str(row[6] or ""),
            "created_at": str(row[7] or ""),
            "file_path": str(row[8] or ""),
            "chunks": int(row[9] or 0),
        }
        for row in rows
    ]


def repair_misclassified_attachments(database_url: str) -> int:
    """Исправляет Word/Kallanish, ошибочно сохранённые как «PDF отчёт»."""
    from .attachment_types import resolve_attachment_document_type

    fixed = 0
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT id, title, file_path, document_type, source_type
                FROM brief_index_documents
                WHERE source_type = 'docx_report'
                   OR lower(COALESCE(title, '')) LIKE '%%.docx'
                   OR lower(COALESCE(file_path, '')) LIKE '%%.docx'
                """
            )
            rows = cur.fetchall()
            for row in rows:
                doc_id, title, file_path, document_type, source_type = row
                name = Path(str(file_path or "")).name or Path(str(title or "")).name
                if not name:
                    continue
                try:
                    correct = resolve_attachment_document_type(name, str(document_type or ""))
                except ValueError:
                    continue
                updates: list[str] = []
                params: list[Any] = []
                if str(document_type or "") != correct:
                    updates.append("document_type = %s")
                    params.append(correct)
                if source_type != "docx_report" and name.lower().endswith(".docx"):
                    updates.append("source_type = %s")
                    params.append("docx_report")
                if not updates:
                    continue
                params.append(int(doc_id))
                cur.execute(
                    f"UPDATE brief_index_documents SET {', '.join(updates)} WHERE id = %s",
                    params,
                )
                fixed += int(cur.rowcount or 0)
        conn.commit()
    return fixed


def _safe_unlink_attachment(file_path: str, *, allowed_roots: Sequence[Path]) -> bool:
    if not file_path:
        return False
    try:
        target = Path(file_path).resolve()
    except OSError:
        return False
    for root in allowed_roots:
        try:
            base = root.resolve()
        except OSError:
            continue
        if target == base or base in target.parents:
            try:
                target.unlink(missing_ok=True)
                return True
            except OSError:
                return False
    return False


def delete_attachment_document(
    database_url: str,
    document_id: int,
    *,
    allowed_roots: Sequence[Path] | None = None,
) -> dict[str, Any]:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT id, title, document_type, source_type, file_path
                FROM brief_index_documents
                WHERE id = %s AND source_type = ANY(%s)
                """,
                (document_id, list(ATTACHMENT_SOURCE_TYPES)),
            )
            row = cur.fetchone()
            if not row:
                raise BriefIndexError("Документ не найден или это не PDF/Kallanish.")
            file_path = str(row[4] or "")
            cur.execute(
                "DELETE FROM brief_index_documents WHERE id = %s",
                (document_id,),
            )
            if cur.rowcount != 1:
                raise BriefIndexError("Не удалось удалить документ из RAG.")
        conn.commit()

    file_deleted = False
    if allowed_roots and file_path:
        file_deleted = _safe_unlink_attachment(file_path, allowed_roots=allowed_roots)

    return {
        "deleted": True,
        "document_id": document_id,
        "title": str(row[1] or ""),
        "document_type": str(row[2] or ""),
        "file_deleted": file_deleted,
    }
