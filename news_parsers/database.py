from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import NewsItem, ParserHealth
from .periods import PeriodRange, parse_item_date
from .topics import derive_topic, load_topic_labels, news_text_body


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS parser_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    period_name TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    items_count INTEGER NOT NULL DEFAULT 0,
    health_count INTEGER NOT NULL DEFAULT 0,
    relevance_filter_enabled BOOLEAN NOT NULL DEFAULT false,
    output_paths JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS news_items (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES parser_runs(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    published_date DATE,
    topic TEXT NOT NULL DEFAULT '',
    news_text TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'und',
    relevance_match TEXT NOT NULL DEFAULT '',
    keyword_block TEXT NOT NULL DEFAULT '',
    keyword_match TEXT NOT NULL DEFAULT '',
    fetched_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'ok',
    error TEXT NOT NULL DEFAULT '',
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS parser_health (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES parser_runs(id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    items INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    fetched_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS briefs (
    id BIGSERIAL PRIMARY KEY,
    run_id BIGINT REFERENCES parser_runs(id) ON DELETE SET NULL,
    period_name TEXT NOT NULL DEFAULT '',
    period_start DATE,
    period_end DATE,
    model TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT 'v1',
    news_count INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_news_items_published_date ON news_items(published_date);
CREATE INDEX IF NOT EXISTS idx_news_items_topic ON news_items(topic);
CREATE INDEX IF NOT EXISTS idx_news_items_source ON news_items(source);
CREATE INDEX IF NOT EXISTS idx_news_items_run_id ON news_items(run_id);
CREATE INDEX IF NOT EXISTS idx_news_items_relevance ON news_items(relevance_match)
    WHERE relevance_match <> '';
CREATE INDEX IF NOT EXISTS idx_parser_health_run_id ON parser_health(run_id);
CREATE INDEX IF NOT EXISTS idx_briefs_run_id ON briefs(run_id);
CREATE INDEX IF NOT EXISTS idx_briefs_period ON briefs(period_start, period_end);
"""

MIGRATION_SQL = """
ALTER TABLE parser_runs ADD COLUMN IF NOT EXISTS relevance_filter_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS language TEXT NOT NULL DEFAULT 'und';
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS relevance_match TEXT NOT NULL DEFAULT '';
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS keyword_block TEXT NOT NULL DEFAULT '';
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS keyword_match TEXT NOT NULL DEFAULT '';
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS run_id BIGINT REFERENCES parser_runs(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_news_items_keyword_block ON news_items(keyword_block)
    WHERE keyword_block <> '';
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS topic TEXT NOT NULL DEFAULT '';
ALTER TABLE news_items ADD COLUMN IF NOT EXISTS news_text TEXT NOT NULL DEFAULT '';

CREATE OR REPLACE VIEW daily_news AS
SELECT
    published_date AS news_date,
    title,
    topic,
    news_text,
    url AS link,
    source,
    keyword_block,
    keyword_match,
    run_id,
    id
FROM news_items
WHERE status = 'ok';
"""

VECTOR_MIGRATION_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS brief_index_documents (
    id BIGSERIAL PRIMARY KEY,
    brief_date DATE NOT NULL,
    period_end DATE,
    brief_kind TEXT NOT NULL CHECK (brief_kind IN ('full', 'market', 'corporate')),
    source_type TEXT NOT NULL CHECK (source_type IN ('daily_docx', 'pdf_report')),
    title TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    full_text TEXT NOT NULL DEFAULT '',
    indexed_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_brief_index_daily_unique
    ON brief_index_documents (brief_date, brief_kind)
    WHERE source_type = 'daily_docx';

CREATE TABLE IF NOT EXISTS brief_index_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES brief_index_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    brief_date DATE NOT NULL,
    period_end DATE,
    brief_kind TEXT NOT NULL,
    source_type TEXT NOT NULL,
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_brief_index_chunks_date_kind
    ON brief_index_chunks (brief_date, brief_kind);

CREATE INDEX IF NOT EXISTS idx_brief_index_chunks_embedding
    ON brief_index_chunks USING hnsw (embedding vector_cosine_ops);
"""

ATTACHMENT_MIGRATION_SQL = """
ALTER TABLE brief_index_documents
    ADD COLUMN IF NOT EXISTS document_type TEXT NOT NULL DEFAULT '';
ALTER TABLE brief_index_documents
    ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_brief_index_content_hash_unique
    ON brief_index_documents (content_hash)
    WHERE content_hash <> '';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'brief_index_documents_source_type_check'
    ) THEN
        ALTER TABLE brief_index_documents
            DROP CONSTRAINT brief_index_documents_source_type_check;
    END IF;
    ALTER TABLE brief_index_documents
        ADD CONSTRAINT brief_index_documents_source_type_check
        CHECK (source_type IN ('daily_docx', 'pdf_report', 'docx_report'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;
"""

# Хранилище СЫРЫХ новостей с полным текстом для RAG-брифов и ИИ-агента.
NEWS_RAG_MIGRATION_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_news_documents (
    id BIGSERIAL PRIMARY KEY,
    news_date DATE NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    full_text TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT 'und',
    keyword_block TEXT NOT NULL DEFAULT '',
    keyword_match TEXT NOT NULL DEFAULT '',
    full_text_status TEXT NOT NULL DEFAULT 'parsed',
    char_count INTEGER NOT NULL DEFAULT 0,
    indexed_by TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (news_date, url)
);

CREATE INDEX IF NOT EXISTS idx_rag_news_documents_date
    ON rag_news_documents (news_date);

CREATE TABLE IF NOT EXISTS rag_news_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES rag_news_documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    news_date DATE NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    embedding vector(1536) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_rag_news_chunks_date
    ON rag_news_chunks (news_date);

CREATE INDEX IF NOT EXISTS idx_rag_news_chunks_embedding
    ON rag_news_chunks USING hnsw (embedding vector_cosine_ops);
"""


@dataclass(frozen=True)
class NewsRow:
    id: int
    source: str
    category: str
    title: str
    published_date: date | None
    topic: str
    news_text: str
    url: str
    summary: str
    content: str
    language: str
    relevance_match: str
    keyword_block: str
    keyword_match: str


def apply_schema(database_url: str) -> None:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
            cur.execute(MIGRATION_SQL)
        conn.commit()


def save_to_postgres(
    database_url: str,
    period_range: PeriodRange,
    items: list[NewsItem],
    health: list[ParserHealth],
    output_paths: Mapping[str, Path],
    *,
    relevance_filter_enabled: bool = False,
) -> int:
    from psycopg.types.json import Jsonb

    output_payload = {name: str(path) for name, path in output_paths.items()}
    db_items = [item for item in items if should_store_news_item(item)]
    topic_labels = load_topic_labels()

    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                INSERT INTO parser_runs (
                    period_name, period_start, period_end,
                    items_count, health_count, relevance_filter_enabled, output_paths
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    period_range.name,
                    period_range.start,
                    period_range.end,
                    len(db_items),
                    len(health),
                    relevance_filter_enabled,
                    Jsonb(output_payload),
                ),
            )
            run_id = cur.fetchone()[0]

            for item in db_items:
                row = item.to_dict()
                topic = derive_topic(item, topic_labels)
                body = news_text_body(item)
                cur.execute(
                    """
                    INSERT INTO news_items (
                        run_id, source, category, title, published_date,
                        topic, news_text, url,
                        summary, content, language, relevance_match,
                        keyword_block, keyword_match,
                        fetched_at, status, error, raw, last_seen_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (url) DO UPDATE SET
                        run_id = EXCLUDED.run_id,
                        source = EXCLUDED.source,
                        category = EXCLUDED.category,
                        title = EXCLUDED.title,
                        published_date = EXCLUDED.published_date,
                        topic = EXCLUDED.topic,
                        news_text = EXCLUDED.news_text,
                        summary = EXCLUDED.summary,
                        content = EXCLUDED.content,
                        language = EXCLUDED.language,
                        relevance_match = EXCLUDED.relevance_match,
                        keyword_block = EXCLUDED.keyword_block,
                        keyword_match = EXCLUDED.keyword_match,
                        fetched_at = EXCLUDED.fetched_at,
                        status = EXCLUDED.status,
                        error = EXCLUDED.error,
                        raw = EXCLUDED.raw,
                        last_seen_at = now()
                    """,
                    (
                        run_id,
                        item.source,
                        item.category,
                        item.title,
                        parse_item_date(item.date),
                        topic,
                        body,
                        item.url,
                        item.summary,
                        item.content,
                        item.language,
                        item.relevance_match,
                        item.keyword_block,
                        item.keyword_match,
                        item.fetched_at or None,
                        item.status,
                        item.error,
                        Jsonb(row),
                    ),
                )

            for report in health:
                cur.execute(
                    """
                    INSERT INTO parser_health (run_id, source, status, items, errors, message, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        run_id,
                        report.source,
                        report.status,
                        report.items,
                        report.errors,
                        report.message,
                        report.fetched_at or None,
                    ),
                )

        conn.commit()
    return int(run_id)


def fetch_news_for_period(
    database_url: str,
    period_start: date,
    period_end: date,
    *,
    relevant_only: bool = False,
    keyword_block: str | None = None,
    sources: Sequence[str] | None = None,
    limit: int = 500,
) -> list[NewsRow]:
    """Новости за период для промпта LLM (бриф)."""
    query = """
        SELECT id, source, category, title, published_date, topic, news_text, url,
               summary, content, language, relevance_match, keyword_block, keyword_match
        FROM news_items
        WHERE status = 'ok'
          AND published_date IS NOT NULL
          AND published_date BETWEEN %s AND %s
    """
    params: list[Any] = [period_start, period_end]

    if relevant_only:
        query += " AND relevance_match <> ''"
    if keyword_block:
        query += " AND keyword_block = %s"
        params.append(keyword_block)
    if sources:
        query += " AND source = ANY(%s)"
        params.append(list(sources))
    query += " ORDER BY published_date DESC, source ASC LIMIT %s"
    params.append(limit)

    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        NewsRow(
            id=row[0],
            source=row[1],
            category=row[2],
            title=row[3],
            published_date=row[4],
            topic=row[5] or "",
            news_text=row[6] or "",
            url=row[7],
            summary=row[8],
            content=row[9],
            language=row[10],
            relevance_match=row[11] or "",
            keyword_block=row[12] or "",
            keyword_match=row[13] or "",
        )
        for row in rows
    ]


def fetch_daily_news(
    database_url: str,
    news_date: date,
    *,
    topic: str | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Новости за день: дата, заголовок, тема, текст, ссылка (view daily_news)."""
    query = """
        SELECT news_date, title, topic, news_text, link
        FROM daily_news
        WHERE news_date = %s
    """
    params: list[Any] = [news_date]
    if topic:
        query += " AND topic ILIKE %s"
        params.append(f"%{topic}%")
    query += " ORDER BY topic, title LIMIT %s"
    params.append(limit)

    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        {
            "news_date": row[0],
            "title": row[1],
            "topic": row[2],
            "news_text": row[3],
            "link": row[4],
        }
        for row in rows
    ]


def count_news_by_date(database_url: str, news_date: date) -> int:
    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                "SELECT COUNT(*) FROM daily_news WHERE news_date = %s",
                (news_date,),
            )
            return int(cur.fetchone()[0])


def save_brief(
    database_url: str,
    *,
    content: str,
    period_range: PeriodRange,
    run_id: int | None = None,
    model: str = "",
    prompt_version: str = "v1",
    news_count: int = 0,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    from psycopg.types.json import Jsonb

    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            apply_schema_with_cursor(cur)
            cur.execute(
                """
                INSERT INTO briefs (
                    run_id, period_name, period_start, period_end,
                    model, prompt_version, news_count, content, metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    run_id,
                    period_range.name,
                    period_range.start,
                    period_range.end,
                    model,
                    prompt_version,
                    news_count,
                    content,
                    Jsonb(dict(metadata or {})),
                ),
            )
            brief_id = cur.fetchone()[0]
        conn.commit()
    return int(brief_id)


def should_store_news_item(item: NewsItem) -> bool:
    return item.status == "ok" and bool(item.title) and bool(item.url)


def apply_schema_with_cursor(cur) -> None:
    cur.execute(SCHEMA_SQL)
    cur.execute(MIGRATION_SQL)
    cur.execute(VECTOR_MIGRATION_SQL)
    cur.execute(ATTACHMENT_MIGRATION_SQL)
    cur.execute(NEWS_RAG_MIGRATION_SQL)


def register_pgvector(conn) -> None:
    try:
        from pgvector.psycopg import register_vector
    except ImportError as exc:
        raise RuntimeError("Install pgvector package for vector search.") from exc
    register_vector(conn)


def _connect(database_url: str):
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary] to enable PostgreSQL output.") from exc
    conn = psycopg.connect(database_url)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
    except Exception:
        conn.rollback()
    try:
        register_pgvector(conn)
    except RuntimeError:
        pass
    return conn
