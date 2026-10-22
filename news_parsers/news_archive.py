"""Выгрузка новостей старше года в архивный бакет и удаление из БД.

В объектное хранилище уходит текст новости (JSONL gzip), без векторов:
эмбеддинги при необходимости пересчитываются. Удаление только после успешной загрузки.
"""

from __future__ import annotations

import gzip
import json
import os
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable

from .object_storage import BucketConfig, load_storage_settings, object_exists, put_bytes

_RAG_COLUMNS = (
    "id, news_date, source, category, title, url, summary, full_text, "
    "language, keyword_block, keyword_match, full_text_status, char_count"
)
_ITEM_COLUMNS = (
    "id, source, category, title, published_date, topic, news_text, url, "
    "summary, content, language, keyword_block, keyword_match, status"
)


def archive_cutoff(today: date | None = None, days: int | None = None) -> date:
    """Новости строго старше этого дня уходят в архив."""
    today = today or date.today()
    span = days if days is not None else int(os.getenv("NEWS_ARCHIVE_AFTER_DAYS", "365"))
    if span < 1:
        raise ValueError("NEWS_ARCHIVE_AFTER_DAYS должен быть >= 1")
    return today - timedelta(days=span)


def archive_object_key(kind: str, news_day: date) -> str:
    return f"news/{kind}/{news_day.year}/{news_day.year}-{news_day.month:02d}.jsonl.gz"


def _json_default(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _gzip_jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    raw = "".join(
        json.dumps(row, ensure_ascii=False, default=_json_default) + "\n" for row in rows
    )
    return gzip.compress(raw.encode("utf-8"))


def _upload_months(
    bucket: BucketConfig,
    kind: str,
    rows: list[dict[str, Any]],
    date_field: str,
) -> list[str]:
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        day = row.get(date_field)
        if not isinstance(day, date):
            continue
        grouped[date(day.year, day.month, 1)].append(row)
    uploaded: list[str] = []
    for month, month_rows in sorted(grouped.items()):
        key = archive_object_key(kind, month)
        uri = put_bytes(bucket, key, _gzip_jsonl(month_rows), "application/gzip")
        if not object_exists(bucket, key):
            raise RuntimeError(f"Объект не появился после загрузки: {uri}")
        uploaded.append(uri)
    return uploaded


def _fetch_dicts(database_url: str, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    from .database import _connect

    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def _execute(database_url: str, sql: str, params: tuple[Any, ...]) -> int:
    from .database import _connect

    with _connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            deleted = cur.rowcount
        conn.commit()
    return deleted


def archive_postgres(database_url: str, cutoff: date, *, apply: bool) -> dict[str, Any]:
    settings = load_storage_settings()
    if settings.archive is None:
        raise RuntimeError(
            "Архивный бакет не настроен: S3_ARCHIVE_BUCKET / S3_ARCHIVE_ACCESS_KEY / "
            "S3_ARCHIVE_SECRET_KEY"
        )
    rag_rows = _fetch_dicts(
        database_url,
        f"SELECT {_RAG_COLUMNS} FROM rag_news_documents WHERE news_date < %s ORDER BY news_date, id",
        (cutoff,),
    )
    item_rows = _fetch_dicts(
        database_url,
        f"SELECT {_ITEM_COLUMNS} FROM news_items "
        "WHERE published_date IS NOT NULL AND published_date < %s ORDER BY published_date, id",
        (cutoff,),
    )
    result: dict[str, Any] = {
        "cutoff": cutoff.isoformat(),
        "rag_documents": len(rag_rows),
        "news_items": len(item_rows),
        "uploaded": [],
        "deleted": {},
        "applied": apply,
    }
    if not apply:
        return result
    result["uploaded"].extend(
        _upload_months(settings.archive, "rag", rag_rows, "news_date")
    )
    result["uploaded"].extend(
        _upload_months(settings.archive, "items", item_rows, "published_date")
    )
    if rag_rows:
        result["deleted"]["rag_news_documents"] = _execute(
            database_url,
            "DELETE FROM rag_news_documents WHERE news_date < %s",
            (cutoff,),
        )
    if item_rows:
        result["deleted"]["news_items"] = _execute(
            database_url,
            "DELETE FROM news_items WHERE published_date IS NOT NULL AND published_date < %s",
            (cutoff,),
        )
    return result


def archive_qdrant(cutoff: date, *, apply: bool) -> dict[str, Any]:
    from .rag.qdrant_store import (
        COLLECTION_NEWS,
        _date_ord,
        _must,
        _range_ord,
        _scroll_all,
        get_qdrant_client,
        qdrant_configured,
    )

    if not qdrant_configured():
        return {"skipped": "QDRANT_URL не задан"}
    settings = load_storage_settings()
    if settings.archive is None:
        raise RuntimeError("Архивный бакет не настроен")
    scroll_filter = _must(_range_ord("news_date_ord", lte=_date_ord(cutoff) - 1))
    points = _scroll_all(COLLECTION_NEWS, scroll_filter=scroll_filter, with_vectors=False)
    documents = []
    for point in points:
        payload = dict(point.payload or {})
        if payload.get("point_kind") != "document":
            continue
        documents.append(payload)
    result: dict[str, Any] = {
        "cutoff": cutoff.isoformat(),
        "qdrant_documents": len(documents),
        "qdrant_points": len(points),
        "uploaded": [],
        "applied": apply,
    }
    if not apply:
        return result
    rows = []
    for payload in documents:
        raw_day = str(payload.get("news_date") or "")[:10]
        try:
            payload = {**payload, "news_date": date.fromisoformat(raw_day)}
        except ValueError:
            continue
        rows.append(payload)
    result["uploaded"] = _upload_months(settings.archive, "qdrant", rows, "news_date")
    if points:
        client = get_qdrant_client()
        client.delete(collection_name=COLLECTION_NEWS, points_selector=scroll_filter)
        result["deleted_points"] = len(points)
    return result
