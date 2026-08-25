"""Индексация СЫРЫХ новостей (с полным текстом) в pgvector и извлечение для RAG.

- index_news_items / index_news_jsonl — загрузка новостей дня с полным текстом.
- fetch_news_documents_for_period — все новости за период (для генерации брифа, map-reduce).
- search_news_chunks — семантический поиск (для ИИ-агента).
- get_news_document — полный текст одной новости (для «показать целиком»).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from psycopg.types.json import Jsonb

from ..article_text import fetch_full_article_text
from ..database import _connect, apply_schema_with_cursor, register_pgvector
from ..http import HttpClient
from ..llm.embeddings import embed_texts
from .chunking import chunk_text

LogFn = Callable[[str], None]
EMBED_BATCH = 96


class NewsRagError(RuntimeError):
    pass


@dataclass(frozen=True)
class NewsDocument:
    id: int
    news_date: date
    source: str
    category: str
    title: str
    url: str
    summary: str
    full_text: str
    keyword_block: str
    full_text_status: str


@dataclass(frozen=True)
class NewsSearchHit:
    document_id: int
    chunk_index: int
    content: str
    news_date: date
    source: str
    title: str
    url: str
    score: float


def _parse_news_date(value: str, fallback: date | None) -> date | None:
    raw = (value or "").strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return fallback


def load_news_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _embed_all(chunks: Sequence[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), EMBED_BATCH):
        batch = list(chunks[start : start + EMBED_BATCH])
        vectors.extend(embed_texts(batch))
    return vectors


def index_news_items(
    database_url: str,
    items: Iterable[dict[str, Any]],
    *,
    indexed_by: str = "system",
    fallback_date: date | None = None,
    fetch_full: bool = True,
    client: HttpClient | None = None,
    browser_fetcher: Any = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    http = client or HttpClient(timeout=25, retries=2)

    prepared: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    raw_items = [it for it in items if str(it.get("status", "ok")) == "ok"]
    total = len(raw_items)
    _log(f"Новостей к индексации: {total}")

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
        prepared.append(
            {
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
            _log(f"  обработано {position}/{total} (текст загружен для индексации)")

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
    with _connect(database_url) as conn:
        register_pgvector(conn)
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            for doc in prepared:
                doc_chunks = doc["chunks"]
                doc_vectors = vectors[cursor_pos : cursor_pos + len(doc_chunks)]
                cursor_pos += len(doc_chunks)

                cur.execute(
                    """
                    INSERT INTO rag_news_documents (
                        news_date, source, category, title, url, summary,
                        full_text, language, keyword_block, keyword_match,
                        full_text_status, char_count, indexed_by, updated_at, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
                    ON CONFLICT (news_date, url) DO UPDATE SET
                        source = EXCLUDED.source,
                        category = EXCLUDED.category,
                        title = EXCLUDED.title,
                        summary = EXCLUDED.summary,
                        full_text = EXCLUDED.full_text,
                        language = EXCLUDED.language,
                        keyword_block = EXCLUDED.keyword_block,
                        keyword_match = EXCLUDED.keyword_match,
                        full_text_status = EXCLUDED.full_text_status,
                        char_count = EXCLUDED.char_count,
                        updated_at = now()
                    RETURNING id
                    """,
                    (
                        doc["news_date"],
                        doc["source"],
                        doc["category"],
                        doc["title"],
                        doc["url"],
                        doc["summary"],
                        doc["full_text"],
                        doc["language"],
                        doc["keyword_block"],
                        doc["keyword_match"],
                        doc["full_text_status"],
                        len(doc["full_text"]),
                        indexed_by,
                        Jsonb({"chunks": len(doc_chunks)}),
                    ),
                )
                document_id = int(cur.fetchone()[0])
                cur.execute(
                    "DELETE FROM rag_news_chunks WHERE document_id = %s", (document_id,)
                )
                for index, (chunk, vector) in enumerate(zip(doc_chunks, doc_vectors)):
                    cur.execute(
                        """
                        INSERT INTO rag_news_chunks (
                            document_id, chunk_index, content, news_date,
                            source, url, title, embedding
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            document_id,
                            index,
                            chunk,
                            doc["news_date"],
                            doc["source"],
                            doc["url"],
                            doc["title"],
                            vector,
                        ),
                    )
                    inserted_chunks += 1
                inserted_docs += 1
        conn.commit()

    fetched = sum(1 for d in prepared if d["full_text_status"] == "fetched")
    _log(
        f"Готово: {inserted_docs} новостей, {inserted_chunks} чанков "
        f"(полный текст догружен: {fetched})"
    )
    return {
        "documents": inserted_docs,
        "chunks": inserted_chunks,
        "full_text_fetched": fetched,
    }


def index_news_jsonl(
    database_url: str,
    jsonl_path: Path,
    *,
    indexed_by: str = "system",
    fallback_date: date | None = None,
    fetch_full: bool = True,
    client: HttpClient | None = None,
    browser_fetcher: Any = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    if not jsonl_path.is_file():
        raise NewsRagError(f"JSONL не найден: {jsonl_path}")
    rows = load_news_jsonl(jsonl_path)
    return index_news_items(
        database_url,
        rows,
        indexed_by=indexed_by,
        fallback_date=fallback_date,
        fetch_full=fetch_full,
        client=client,
        browser_fetcher=browser_fetcher,
        log=log,
    )


def fetch_news_documents_for_period(
    database_url: str,
    *,
    period_start: date,
    period_end: date,
    keyword_block: str | None = None,
    sources: Sequence[str] | None = None,
) -> list[NewsDocument]:
    """Все новости за период (для генерации брифа map-reduce)."""
    params: list[Any] = [period_start, period_end]
    where = "news_date >= %s AND news_date <= %s"
    if keyword_block:
        where += " AND keyword_block = %s"
        params.append(keyword_block)
    # None = все источники; [] = ни одного (новости не брать).
    if sources is not None:
        where += " AND source = ANY(%s)"
        params.append(list(sources))
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT id, news_date, source, category, title, url, summary,
                       full_text, keyword_block, full_text_status
                FROM rag_news_documents
                WHERE {where}
                ORDER BY news_date, source, id
                """,
                params,
            )
            rows = cur.fetchall()
    return [
        NewsDocument(
            id=int(r[0]), news_date=r[1], source=str(r[2]), category=str(r[3]),
            title=str(r[4]), url=str(r[5]), summary=str(r[6]), full_text=str(r[7]),
            keyword_block=str(r[8]), full_text_status=str(r[9]),
        )
        for r in rows
    ]


def _rows_to_hits(rows: Sequence[Any]) -> list[NewsSearchHit]:
    return [
        NewsSearchHit(
            document_id=int(r[0]),
            chunk_index=int(r[1]),
            content=str(r[2]),
            news_date=r[3],
            source=str(r[4]),
            title=str(r[5]),
            url=str(r[6]),
            score=float(r[7] or 0.0),
        )
        for r in rows
    ]


def search_news_chunks(
    database_url: str,
    *,
    query_text: str,
    period_start: date | None = None,
    period_end: date | None = None,
    source_contains: str | None = None,
    top_k: int = 12,
    candidate_limit: int | None = None,
) -> list[NewsSearchHit]:
    """Семантический поиск по новостям (для ИИ-агента)."""
    query_vector = embed_texts([query_text])[0]
    where_parts = ["TRUE"]
    params: list[Any] = [query_vector]
    if period_start and period_end:
        where_parts.append("news_date >= %s AND news_date <= %s")
        params.extend([period_start, period_end])
    if source_contains:
        where_parts.append("source ILIKE %s")
        params.append(f"%{source_contains}%")
    where = " AND ".join(where_parts)
    limit = candidate_limit or top_k
    params.extend([query_vector, limit])
    with _connect(database_url) as conn:
        register_pgvector(conn)
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT document_id, chunk_index, content, news_date, source, title, url,
                       1 - (embedding <=> %s::vector) AS score
                FROM rag_news_chunks
                WHERE {where}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
    return _rows_to_hits(rows)


def get_news_document(database_url: str, document_id: int) -> NewsDocument | None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT id, news_date, source, category, title, url, summary,
                       full_text, keyword_block, full_text_status
                FROM rag_news_documents WHERE id = %s
                """,
                (document_id,),
            )
            r = cur.fetchone()
    if not r:
        return None
    return NewsDocument(
        id=int(r[0]), news_date=r[1], source=str(r[2]), category=str(r[3]),
        title=str(r[4]), url=str(r[5]), summary=str(r[6]), full_text=str(r[7]),
        keyword_block=str(r[8]), full_text_status=str(r[9]),
    )


def get_news_chunks_for_documents(
    database_url: str,
    document_ids: Sequence[int],
) -> list[NewsSearchHit]:
    """Все чанки выбранных новостей по порядку chunk_index."""
    ids = sorted({int(i) for i in document_ids if int(i) > 0})
    if not ids:
        return []
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT c.document_id, c.chunk_index, c.content, c.news_date,
                       c.source, d.title, d.url
                FROM rag_news_chunks c
                JOIN rag_news_documents d ON d.id = c.document_id
                WHERE c.document_id = ANY(%s)
                ORDER BY c.document_id, c.chunk_index
                """,
                (ids,),
            )
            rows = cur.fetchall()
    return [
        NewsSearchHit(
            document_id=int(r[0]),
            chunk_index=int(r[1]),
            content=str(r[2]),
            news_date=r[3],
            source=str(r[4] or ""),
            title=str(r[5] or ""),
            url=str(r[6] or ""),
            score=1.0,
        )
        for r in rows
        if str(r[2] or "").strip()
    ]


def period_news_stats(
    database_url: str,
    *,
    period_start: date,
    period_end: date,
    sources: Sequence[str] | None = None,
) -> dict[str, Any]:
    params: list[Any] = [period_start, period_end]
    where = "news_date >= %s AND news_date <= %s"
    if sources is not None:
        where += " AND source = ANY(%s)"
        params.append(list(sources))
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                f"""
                SELECT COUNT(*), COUNT(DISTINCT news_date),
                       COALESCE(SUM(CASE WHEN full_text_status = 'fetched' THEN 1 ELSE 0 END), 0)
                FROM rag_news_documents
                WHERE {where}
                """,
                params,
            )
            row = cur.fetchone()
    return {
        "documents": int(row[0] or 0),
        "days": int(row[1] or 0),
        "full_text_fetched": int(row[2] or 0),
    }


def list_period_news_sources(
    database_url: str, *, period_start: date, period_end: date
) -> list[dict[str, Any]]:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                SELECT source, COUNT(*)
                FROM rag_news_documents
                WHERE news_date >= %s AND news_date <= %s
                GROUP BY source
                ORDER BY source
                """,
                (period_start, period_end),
            )
            rows = cur.fetchall()
    return [{"name": str(row[0]), "count": int(row[1] or 0)} for row in rows]
