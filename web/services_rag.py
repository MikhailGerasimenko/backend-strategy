"""Веб-сервисы: индексация RAG и еженедельные брифы."""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from news_parsers.docx_brief import (
    weekly_brief_docx_filename,
    write_brief_docx,
)
from news_parsers.llm.agent import AGENT_SYSTEM_PROMPT, answer_question
from news_parsers.llm.brief_cleanup import cleanup_generated_brief
from news_parsers.llm.period_brief import (
    generate_period_brief,
    get_period_system_prompt,
    is_monthly_brief_kind,
    list_period_prompt_variants,
)
from news_parsers.monthly_brief_json import export_monthly_brief_json
from news_parsers.llm.kallanish_articles import (
    KALLANISH_SOURCE,
    articles_from_path,
    kallanish_articles_to_news_items,
)
from news_parsers.llm.kallanish_docx import (
    KALLANISH_CANONICAL_NAME,
    discover_kallanish_docx,
    save_kallanish_upload,
)
from news_parsers.llm.openrouter import OpenRouterError
from news_parsers.rag.attachment_types import (
    ALLOWED_ATTACHMENT_SUFFIXES,
    resolve_attachment_document_type,
)
from news_parsers.rag.vector_backend import (
    BriefIndexError,
    DuplicateDocumentError,
    count_attachment_documents,
    database_url,
    delete_attachment_document,
    index_attachment_document,
    index_news_items,
    list_attachment_documents,
    list_index_coverage,
    list_period_news_sources,
    period_news_stats,
    qdrant_configured,
    upsert_kallanish_catalog_document,
    vector_backend,
)

from .config import BRIEF_CATEGORIES_PATH, NEWS_DIR, SOURCES_PATH, load_dotenv
from .services import LogFn

MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024

_BRIEF_CATEGORY_CACHE: dict[str, Any] | None = None


def load_brief_categories() -> dict[str, Any]:
    """Категории источников по типу брифа (news / market / all)."""
    global _BRIEF_CATEGORY_CACHE
    if _BRIEF_CATEGORY_CACHE is not None:
        return _BRIEF_CATEGORY_CACHE
    import json

    if not BRIEF_CATEGORIES_PATH.exists():
        _BRIEF_CATEGORY_CACHE = {
            "labels": {"news": "Новостной", "market": "Рыночный", "all": "Все типы"},
            "brief_kind_match": {
                "full": ["news", "market", "all"],
                "monthly": ["news", "market", "all"],
                "monthly_news": ["news", "all"],
                "monthly_market": ["market", "all"],
                "monthly_corporate": ["market", "all"],
                "market": ["market", "all"],
                "corporate": ["news", "all"],
            },
            "sources": {},
        }
        return _BRIEF_CATEGORY_CACHE
    with BRIEF_CATEGORIES_PATH.open(encoding="utf-8") as fh:
        _BRIEF_CATEGORY_CACHE = json.load(fh)
    return _BRIEF_CATEGORY_CACHE


def pgvector_configured() -> bool:
    """True, если доступен любой векторный стор (Qdrant или Postgres)."""
    return rag_configured()


def rag_configured() -> bool:
    load_dotenv()
    if qdrant_configured():
        return True
    return bool(os.getenv("DATABASE_URL", "").strip())


def rag_backend_name() -> str:
    load_dotenv()
    return vector_backend()


def rag_period_coverage(
    period_start: date,
    period_end: date,
    brief_kind: str,
    sources: list[str] | None = None,
) -> dict[str, Any]:
    if not pgvector_configured():
        return {"configured": False, "documents": []}
    url = database_url()
    documents = list_index_coverage(
        url,
        period_start=period_start,
        period_end=period_end,
        brief_kind=brief_kind,
    )
    news = period_news_stats(
        url, period_start=period_start, period_end=period_end, sources=sources
    )
    attachments = [d for d in documents if d.get("source_type") in ("pdf_report", "docx_report")]
    return {
        "configured": True,
        "documents": documents,
        "attachments": attachments,
        "period_days": (period_end - period_start).days + 1,
        "news_documents": news.get("documents", 0),
        "news_days": news.get("days", 0),
        "news_full_text_fetched": news.get("full_text_fetched", 0),
    }


def index_kallanish_articles_as_news(
    path: Path,
    *,
    brief_date: date,
    indexed_by: str,
    period_end: date | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Режет Kallanish на статьи и сохраняет каждую как новость source=Kallanish."""
    if not pgvector_configured():
        raise RuntimeError("DATABASE_URL не задан")
    if not path.is_file():
        raise FileNotFoundError(f"Файл не найден: {path}")

    articles = articles_from_path(path, fallback_date=brief_date)
    if not articles:
        raise ValueError("В файле Kallanish не найдено статей для индексации.")

    items = kallanish_articles_to_news_items(articles, filename=path.name)
    if log:
        log(f"Kallanish: найдено статей {len(items)}, индексация в новостной RAG…")
    result = index_news_items(
        database_url(),
        items,
        indexed_by=indexed_by,
        fallback_date=brief_date,
        fetch_full=False,
        log=log,
    )
    upsert_kallanish_catalog_document(
        database_url(),
        title=path.name,
        brief_date=brief_date,
        period_end=period_end or brief_date,
        indexed_by=indexed_by,
        articles=len(items),
        file_path=str(path),
        content_hash=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    return {
        "indexed": True,
        "storage": "news",
        "document_type": KALLANISH_SOURCE,
        "title": path.name,
        "articles": len(items),
        "documents": int(result.get("documents") or 0),
        "chunks": int(result.get("chunks") or 0),
        "brief_date": brief_date.isoformat(),
        "period_end": brief_date.isoformat(),
        "source": KALLANISH_SOURCE,
    }


def index_kallanish_to_rag(
    *,
    brief_date: date,
    period_end: date | None = None,
    indexed_by: str,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Проиндексировать актуальный kallanish.docx статьями в новостной RAG."""
    if not pgvector_configured():
        return {"indexed": False, "reason": "DATABASE_URL не задан"}
    path = NEWS_DIR / KALLANISH_CANONICAL_NAME
    if not path.is_file():
        discovered = discover_kallanish_docx(NEWS_DIR)
        path = discovered if discovered else path
    if not path.is_file():
        return {"indexed": False, "reason": "Файл Kallanish не найден"}

    try:
        result = index_kallanish_articles_as_news(
            path,
            brief_date=brief_date,
            period_end=period_end,
            indexed_by=indexed_by,
            log=log,
        )
    except Exception as exc:  # noqa: BLE001
        return {"indexed": False, "reason": str(exc)}
    return result


def upload_kallanish_with_rag(
    content: bytes,
    filename: str,
    *,
    brief_date: date | None = None,
    indexed_by: str = "system",
) -> dict[str, Any]:
    info = save_kallanish_upload(NEWS_DIR, content, filename)
    report_date = brief_date or date.today()
    rag = index_kallanish_to_rag(
        brief_date=report_date,
        period_end=report_date,
        indexed_by=indexed_by,
    )
    info["rag"] = rag
    return info


def index_document_upload(
    content: bytes,
    filename: str,
    *,
    brief_date: date,
    period_end: date | None,
    document_type: str,
    indexed_by: str,
    title: str = "",
    log: LogFn | None = None,
) -> dict[str, Any]:
    if not content:
        raise ValueError("Пустой файл.")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Файл слишком большой (максимум 30 МБ).")

    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_SUFFIXES:
        raise ValueError("Поддерживаются PDF (.pdf), Word (.docx) и текст (.txt).")

    document_type = resolve_attachment_document_type(safe_name, document_type)

    reports_dir = NEWS_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = reports_dir / f"{brief_date.strftime('%Y%m%d')}_{stamp}_{safe_name}"
    path.write_bytes(content)

    if log:
        log(f"Документ сохранён: {path.name}")

    try:
        # Kallanish (.docx/.txt) → отдельные новости source=Kallanish, не один blob во вложениях.
        if document_type == KALLANISH_SOURCE or "kallanish" in safe_name.lower():
            if log:
                log("Kallanish: нарезка на статьи и запись в новостной RAG…")
            return index_kallanish_articles_as_news(
                path,
                brief_date=brief_date,
                period_end=period_end,
                indexed_by=indexed_by,
                log=log,
            )

        if log:
            log("Индексация в pgvector…")

        return index_attachment_document(
            database_url(),
            file_path=path,
            raw_bytes=content,
            brief_date=brief_date,
            period_end=period_end or brief_date,
            document_type=document_type,
            indexed_by=indexed_by,
            title=title or safe_name,
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise


def index_documents_upload(
    items: list[tuple[bytes, str]],
    *,
    brief_date: date,
    period_end: date | None,
    document_type: str,
    indexed_by: str,
    log: LogFn | None = None,
) -> dict[str, Any]:
    """Пакетная загрузка PDF/Word/TXT с одним периодом и типом документа."""
    if not items:
        raise ValueError("Выберите хотя бы один файл.")

    uploaded: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for index, (content, filename) in enumerate(items, start=1):
        name = Path(filename or f"file_{index}").name
        doc_type = resolve_attachment_document_type(name, document_type)
        if log:
            log(f"Документ {index}/{len(items)}: {name} ({doc_type})…")
        try:
            result = index_document_upload(
                content,
                filename,
                brief_date=brief_date,
                period_end=period_end,
                document_type=doc_type,
                indexed_by=indexed_by,
                log=log,
            )
            uploaded.append({**result, "filename": name, "document_type": doc_type})
        except DuplicateDocumentError as exc:
            skipped.append({"filename": name, "reason": str(exc)})
        except (ValueError, BriefIndexError) as exc:
            failed.append({"filename": name, "reason": str(exc)})
        except OpenRouterError as exc:
            failed.append({"filename": name, "reason": f"OpenRouter: {exc}"})
        except Exception as exc:  # noqa: BLE001 — один битый файл не должен ронять всю загрузку
            failed.append({"filename": name, "reason": str(exc) or type(exc).__name__})

    if not uploaded and failed and not skipped:
        details = "; ".join(f"{item['filename']}: {item['reason']}" for item in failed[:5])
        raise ValueError(details or "Не удалось обработать файлы.")

    total_chunks = sum(int(item.get("chunks", 0)) for item in uploaded)
    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
        "total": len(items),
        "uploaded_count": len(uploaded),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "chunks": total_chunks,
        "brief_date": brief_date.isoformat(),
        "period_end": (period_end or brief_date).isoformat(),
        "document_type": document_type.strip(),
    }


def index_pdf_upload(
    content: bytes,
    filename: str,
    *,
    brief_date: date,
    period_end: date | None,
    brief_kind: str,
    indexed_by: str,
    title: str = "",
    log: LogFn | None = None,
) -> dict[str, Any]:
    return index_document_upload(
        content,
        filename,
        brief_date=brief_date,
        period_end=period_end,
        document_type="PDF отчёт",
        indexed_by=indexed_by,
        title=title,
        log=log,
    )


def list_rag_attachments(
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict[str, Any]:
    if not pgvector_configured():
        return {"configured": False, "documents": []}
    documents = list_attachment_documents(
        database_url(),
        period_start=period_start,
        period_end=period_end,
    )
    return {
        "configured": True,
        "documents": documents,
        "total": len(documents),
    }


def remove_rag_attachment(document_id: int) -> dict[str, Any]:
    if not pgvector_configured():
        raise RuntimeError("DATABASE_URL не задан.")
    allowed_roots = [NEWS_DIR, NEWS_DIR / "reports"]
    return delete_attachment_document(
        database_url(),
        document_id,
        allowed_roots=allowed_roots,
    )


def list_brief_news_sources(period_start: date, period_end: date) -> dict[str, Any]:
    """Список источников для UI: конфиг + счётчики новостей в RAG за период."""
    from news_parsers.runner import load_sources

    configured = load_sources(SOURCES_PATH, include_web=True, include_telegram=True)
    brief_cfg = load_brief_categories()
    brief_meta = dict(brief_cfg.get("sources") or {})
    labels = brief_cfg.get("labels") or {
        "news": "Новостной",
        "market": "Рыночный",
        "all": "Все типы",
    }
    counts: dict[str, int] = {}
    if pgvector_configured():
        for row in list_period_news_sources(
            database_url(), period_start=period_start, period_end=period_end
        ):
            counts[row["name"]] = int(row["count"])

    custom_meta: dict[str, dict[str, Any]] = {}
    try:
        from news_parsers.custom_telegram import custom_brief_meta

        custom_meta = custom_brief_meta()
    except Exception:
        custom_meta = {}
    for name, extra in custom_meta.items():
        current = dict(brief_meta.get(name) or {})
        current["brief"] = extra.get("brief") or current.get("brief") or "all"
        current["topic_category"] = extra.get("topic_category") or current.get("topic_category")
        current["custom"] = True
        brief_meta[name] = current

    retired_names: set[str] = set()
    try:
        from news_parsers.disabled_sources import archived_brief_meta, disabled_name_set

        retired_names = disabled_name_set()
        for name, extra in archived_brief_meta().items():
            current = dict(brief_meta.get(name) or {})
            current["brief"] = extra.get("brief") or current.get("brief") or "all"
            current["topic_category"] = extra.get("topic_category") or current.get(
                "topic_category"
            )
            if extra.get("custom"):
                current["custom"] = True
            brief_meta[name] = current
    except Exception:
        retired_names = set()

    def enrich(name: str, kind: str, count: int, configured: bool) -> dict[str, Any]:
        meta = brief_meta.get(name) or {}
        brief = meta.get("brief")
        return {
            "name": name,
            "kind": kind,
            "count": count,
            "configured": configured,
            "brief": brief,
            "brief_label": labels.get(brief) if brief else None,
            "topic_category": meta.get("topic_category"),
            "custom": bool(meta.get("custom")),
            "retired": name in retired_names,
        }

    seen: set[str] = set()
    sources: list[dict[str, Any]] = []
    for item in configured:
        name = str(item.get("name", "")).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        kind = "telegram" if item.get("parser") == "telegram" else "web"
        sources.append(enrich(name, kind, counts.get(name, 0), True))

    for name, count in sorted(counts.items()):
        if name in seen:
            continue
        kind = "telegram" if name.startswith("TG ") else "web"
        sources.append(enrich(name, kind, count, False))

    # Скрываем источники без категории / с brief: null («Без категории»).
    sources = [s for s in sources if s.get("brief")]

    kallanish_count = int(counts.get(KALLANISH_SOURCE, 0))
    pdf_count = 0
    pmi_count = 0
    if pgvector_configured():
        try:
            attachments = list_attachment_documents(
                database_url(),
                period_start=period_start,
                period_end=period_end,
            )
            for d in attachments:
                dtype = str(d.get("document_type") or "").lower()
                if "kallanish" in dtype:
                    # Старые blob-вложения Kallanish больше не считаем основным источником.
                    continue
                if "pmi" in dtype:
                    pmi_count += 1
                else:
                    pdf_count += 1
        except Exception:
            pdf_count = 0
            pmi_count = 0

    # Kallanish мог отфильтроваться выше (нет brief в конфиге) — возвращаем явно.
    sources = [s for s in sources if s.get("name") != KALLANISH_SOURCE]
    sources.append(
        {
            "name": KALLANISH_SOURCE,
            "kind": "web",
            "count": kallanish_count,
            "configured": True,
            "brief": "market",
            "brief_label": labels.get("market") or labels.get("all"),
            "topic_category": "Металлургия мира",
        }
    )
    seen.add(KALLANISH_SOURCE)

    for name, count, topic in (
        ("PDF отчёт", pdf_count, "Документы RAG"),
        ("PMI", pmi_count, "Документы RAG"),
    ):
        sources.append(
            {
                "name": name,
                "kind": "document",
                "count": count,
                "configured": True,
                "brief": "all",
                "brief_label": labels.get("all"),
                "topic_category": topic,
            }
        )

    order = {"news": 0, "market": 1, "all": 2}
    sources.sort(
        key=lambda s: (
            order.get(s.get("brief"), 9),
            0 if s["kind"] == "document" else (1 if s["kind"] == "web" else 2),
            s["name"].lower(),
        )
    )
    default_match = {
        "full": ["news", "market", "all"],
        "monthly": ["news", "market", "all"],
        "monthly_news": ["news", "all"],
        "monthly_market": ["market", "all"],
        "monthly_corporate": ["market", "all"],
        "market": ["market", "all"],
        "corporate": ["news", "all"],
    }
    match = dict(default_match)
    match.update(brief_cfg.get("brief_kind_match") or {})
    for key, value in default_match.items():
        match.setdefault(key, value)
    return {
        "configured": pgvector_configured(),
        "sources": sources,
        "total_configured": len(configured),
        "brief_labels": labels,
        "brief_kind_match": match,
    }


def run_weekly_brief(
    period_start: date,
    period_end: date,
    system_prompt: str,
    *,
    map_system_prompt: str | None = None,
    reduce_instructions: str | None = None,
    model: str | None = None,
    brief_kind: str = "full",
    sources: list[str] | None = None,
    attachment_ids: list[int] | None = None,
    should_cancel: Any = None,
    log: LogFn,
) -> dict[str, Any]:
    load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise RuntimeError("Не задан OPENROUTER_API_KEY в файле .env")
    if period_end < period_start:
        raise ValueError("Конец периода раньше начала.")

    cancel_fn = should_cancel if callable(should_cancel) else None

    url = database_url()
    stats = period_news_stats(
        url, period_start=period_start, period_end=period_end, sources=sources
    )
    attachment_count = count_attachment_documents(
        url, period_start=period_start, period_end=period_end
    )
    period_days = (period_end - period_start).days + 1
    log(
        f"Векторная БД (сырые новости): {stats.get('documents', 0)} новостей "
        f"за {stats.get('days', 0)} из {period_days} дней периода; "
        f"полный текст догружен у {stats.get('full_text_fetched', 0)}"
    )
    if attachment_ids is not None:
        log(
            f"Векторная БД (документы PDF/Word/Kallanish): выбрано {len(attachment_ids)} "
            f"из {attachment_count} за период"
        )
    else:
        log(f"Векторная БД (документы PDF/Word/Kallanish): {attachment_count}")

    effective_model = model or os.getenv("BRIEF_MODEL", "google/gemini-3.6-flash")
    log(f"Генерация брифа из сырых новостей ({brief_kind}, модель {effective_model})…")

    if sources is not None:
        if sources:
            log(f"Фильтр источников: {len(sources)} из конфигурации")
        else:
            log("Новостные источники не выбраны — только документы (Kallanish/PDF)")

    content, metadata = generate_period_brief(
        url,
        period_start=period_start,
        period_end=period_end,
        brief_kind=brief_kind,
        system_prompt=system_prompt,
        map_system_prompt=map_system_prompt,
        reduce_instructions=reduce_instructions,
        model=effective_model,
        log=log,
        news_dir=NEWS_DIR,
        sources=sources,
        attachment_ids=attachment_ids,
        should_cancel=cancel_fn,
    )

    log(
        f"Готово: новостей {metadata.get('news_documents')}, "
        f"map-батчей {metadata.get('map_batches')}, "
        f"запросов к API: {metadata.get('api_calls')}, "
        f"clean-up: {'да' if metadata.get('cleanup_pass') else 'нет'}, "
        f"символов в ответе: {len(content)}"
    )
    metadata["generated_at"] = datetime.now().isoformat(timespec="seconds")
    log("Текст брифа готов — можно отредактировать перед экспортом в Word.")

    # Дайджесты батчей отдаём отдельным полем: в metadata они попали бы
    # в JSON/Word-экспорт и раздули бы его.
    map_digests = metadata.pop("map_digests", None) or []

    return {
        "content": content,
        "brief_kind": brief_kind,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "metadata": metadata,
        "map_digests": map_digests,
    }


def cleanup_preview_brief(
    content: str,
    *,
    brief_kind: str,
    system_prompt: str | None = None,
    model: str | None = None,
    log: LogFn | None = None,
) -> dict[str, Any]:
    load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise RuntimeError("Не задан OPENROUTER_API_KEY в файле .env")
    text = (content or "").strip()
    if len(text) < 50:
        raise ValueError("Текст брифа слишком короткий для clean-up.")

    if log:
        log("Запускаем LLM clean-up: удаление дублей и нерелевантного шума…")
    cleaned, applied = cleanup_generated_brief(
        text,
        model=model or os.getenv("BRIEF_MODEL", "google/gemini-3.6-flash"),
        brief_kind=brief_kind,
        system_prompt=system_prompt,
    )
    if log:
        log(
            "Clean-up завершён: "
            + ("текст обновлён." if applied else "оставлен исходный текст (защитный fallback).")
        )
    return {
        "content": cleaned,
        "brief_kind": brief_kind,
        "cleanup_pass": applied,
        "content_chars": len(cleaned),
    }


def export_period_brief_docx(
    content: str,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str = "full",
    storage_key: str = "",
) -> dict[str, Any]:
    """Собрать Word из (возможно отредактированного) текста брифа."""
    text = (content or "").strip()
    if len(text) < 50:
        raise ValueError("Текст брифа слишком короткий для экспорта в Word.")

    filename = weekly_brief_docx_filename(period_start, period_end, brief_kind)
    key = "".join(ch for ch in storage_key if ch.isalnum())[:12]
    disk_name = f"{Path(filename).stem}__{key}.docx" if key else filename
    docx_path = NEWS_DIR / disk_name
    write_brief_docx(
        docx_path,
        text,
        report_date=period_end.strftime("%d.%m.%Y"),
        period_label=(
            f"{period_start.strftime('%d.%m.%Y')} — {period_end.strftime('%d.%m.%Y')}"
        ),
    )
    return {
        "docx_path": str(docx_path),
        "docx_filename": filename,
        "brief_kind": brief_kind,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "content_chars": len(text),
    }


def export_period_brief_json(
    content: str,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str = "monthly",
    metadata: dict[str, Any] | None = None,
    storage_key: str = "",
) -> dict[str, Any]:
    """Собрать JSON месячного брифа из (возможно отредактированного) текста."""
    if not is_monthly_brief_kind(brief_kind):
        raise ValueError("JSON-экспорт доступен для ежемесячных брифов.")
    return export_monthly_brief_json(
        content,
        period_start=period_start,
        period_end=period_end,
        brief_kind=brief_kind,
        metadata=metadata,
        output_dir=NEWS_DIR,
        storage_key=storage_key,
    )


def ask_news_agent(
    question: str,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    prior_sources: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    load_dotenv()
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise RuntimeError("Не задан OPENROUTER_API_KEY в файле .env")
    if not (question or "").strip():
        raise ValueError("Пустой вопрос.")
    return answer_question(
        database_url(),
        question=question.strip(),
        period_start=period_start,
        period_end=period_end,
        prior_sources=prior_sources,
        history=history,
        model=model,
        system_prompt=system_prompt,
    )


def get_default_agent_system_prompt() -> str:
    return AGENT_SYSTEM_PROMPT


def get_default_weekly_system_prompt(
    variant: str = "full",
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> str:
    """Промпт для страницы «Бриф за период» (дневные шаблоны + monthly)."""
    return get_period_system_prompt(
        variant,
        period_start=period_start,
        period_end=period_end,
    )


def list_weekly_system_prompt_variants() -> list[dict[str, str]]:
    return list_period_prompt_variants()