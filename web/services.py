from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from news_parsers.docx_brief import write_brief_docx, brief_docx_filename, brief_docx_filenames_for_day
from news_parsers.llm.brief import (
    BriefContext,
    generate_brief_comment,
    get_system_prompt_variant,
    load_brief_input_from_rag,
)
from news_parsers.llm.config import BRIEF_MODEL
from news_parsers.periods import build_period_range
from news_parsers.rag.vector_backend import index_news_items
from news_parsers.runner import (
    load_keyword_filter_config,
    load_relevance_filter_config,
    run_all_sources,
)

from news_parsers.llm.kallanish_docx import get_kallanish_info

from .config import NEWS_DIR, PROJECT_DIR, SOURCES_PATH, load_dotenv


LogFn = Callable[[str], None]

_JSONL_DATE_RE = re.compile(
    r"strategic_navigator_news_.*_custom_(\d{4}-\d{2}-\d{2})_\1\.jsonl$",
    re.IGNORECASE,
)


def jsonl_path_for_day(day: date) -> Path | None:
    day_str = day.isoformat()
    pattern = f"strategic_navigator_news_*_custom_{day_str}_{day_str}.jsonl"
    matches = sorted(NEWS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def count_news_in_jsonl(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                count += 1
    return count


def kallanish_status() -> dict[str, Any]:
    return get_kallanish_info(NEWS_DIR)


def upload_kallanish(
    content: bytes,
    filename: str,
    *,
    brief_date: date | None = None,
    indexed_by: str = "system",
) -> dict[str, Any]:
    from .services_rag import upload_kallanish_with_rag

    return upload_kallanish_with_rag(
        content,
        filename,
        brief_date=brief_date,
        indexed_by=indexed_by,
    )


def day_status(day: date) -> dict[str, Any]:
    jsonl = jsonl_path_for_day(day)
    kallanish = kallanish_status()
    news_count = count_news_in_jsonl(jsonl) if jsonl else 0
    return {
        "date": day.isoformat(),
        "has_news": jsonl is not None,
        "jsonl_path": str(jsonl) if jsonl else None,
        "jsonl_filename": jsonl.name if jsonl else None,
        "news_count": news_count,
        "has_kallanish": kallanish.get("has_file", False),
        "kallanish_file": kallanish.get("filename"),
        "kallanish": kallanish,
        "ready_for_brief": jsonl is not None and news_count > 0,
    }


def list_available_news_dates() -> list[dict[str, Any]]:
    if not NEWS_DIR.is_dir():
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for path in sorted(NEWS_DIR.glob("strategic_navigator_news_*_custom_*_*.jsonl")):
        match = _JSONL_DATE_RE.match(path.name)
        if not match:
            continue
        day_str = match.group(1)
        count = count_news_in_jsonl(path)
        if count <= 0:
            continue
        existing = by_date.get(day_str)
        mtime = path.stat().st_mtime
        if existing is None or mtime > existing["mtime"]:
            by_date[day_str] = {
                "date": day_str,
                "news_count": count,
                "filename": path.name,
                "mtime": mtime,
            }
    items = list(by_date.values())
    items.sort(key=lambda item: item["date"], reverse=True)
    for item in items:
        item.pop("mtime", None)
    return items


def _validate_jsonl_content(raw: bytes) -> tuple[int, list[str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Файл должен быть в кодировке UTF-8.") from exc

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("JSONL пустой.")

    valid = 0
    errors: list[str] = []
    for index, line in enumerate(lines[:500], start=1):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"Строка {index}: не JSON")
            continue
        if row.get("status", "ok") != "ok":
            continue
        if row.get("title") and row.get("url"):
            valid += 1
        else:
            errors.append(f"Строка {index}: нет title или url")

    if valid == 0:
        hint = errors[0] if errors else "нет валидных записей"
        raise ValueError(f"В файле нет пригодных новостей ({hint}).")
    return valid, errors[:3]


def save_news_jsonl_upload(day: date, content: bytes, filename: str) -> dict[str, Any]:
    if not filename.lower().endswith(".jsonl"):
        raise ValueError("Нужен файл с расширением .jsonl")

    valid_count, warnings = _validate_jsonl_content(content)
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    day_str = day.isoformat()

    for path in _files_for_news_day(day, include_briefs=False):
        path.unlink(missing_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = NEWS_DIR / f"strategic_navigator_news_{stamp}_custom_{day_str}_{day_str}.jsonl"
    target.write_bytes(content)

    return {
        "date": day_str,
        "uploaded_as": target.name,
        "source_filename": filename,
        "news_count": valid_count,
        "warnings": warnings,
    }


def _files_for_news_day(day: date, *, include_briefs: bool = True) -> list[Path]:
    if not NEWS_DIR.is_dir():
        return []

    day_str = day.isoformat()
    day_compact = day_str.replace("-", "")
    matched: list[Path] = []

    for path in NEWS_DIR.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if f"_custom_{day_str}_{day_str}." in name:
            matched.append(path)
            continue
        if not include_briefs:
            continue
        if name in brief_docx_filenames_for_day(date.fromisoformat(day_str)):
            matched.append(path)
            continue
        if name.startswith(f"brief_web_{day_str}_"):
            matched.append(path)
        elif name.startswith("brief_") and f"_{day_str}_{day_str}" in name:
            matched.append(path)
        elif name.endswith(".sources.json") and day_compact in name:
            matched.append(path)

    return sorted(matched, key=lambda p: p.name)


def delete_news_data_for_day(day: date, *, include_briefs: bool = True) -> dict[str, Any]:
    paths = _files_for_news_day(day, include_briefs=include_briefs)
    if not paths:
        raise ValueError(f"Нет данных за {day.isoformat()} для удаления.")

    deleted: list[str] = []
    for path in paths:
        path.unlink(missing_ok=True)
        deleted.append(path.name)

    return {
        "date": day.isoformat(),
        "deleted_files": deleted,
        "deleted_count": len(deleted),
        "include_briefs": include_briefs,
    }


def news_data_overview(day: date) -> dict[str, Any]:
    status = day_status(day)
    files = _files_for_news_day(day, include_briefs=True)
    return {
        **status,
        "files": [{"name": p.name, "size_kb": round(p.stat().st_size / 1024, 1)} for p in files],
        "can_delete": bool(files),
    }


def run_parse_for_day(
    day: date,
    log: LogFn,
    *,
    source_names: set[str] | None = None,
) -> dict[str, Any]:
    load_dotenv()
    if source_names:
        log(f"Запуск парсинга выбранных источников: {', '.join(sorted(source_names))}…")
    else:
        log("Запуск парсинга источников…")
    day_str = day.isoformat()
    keyword_cfg = load_keyword_filter_config(SOURCES_PATH)
    relevance_cfg = load_relevance_filter_config(SOURCES_PATH)
    keyword_filter = keyword_cfg.get("enabled", False)
    relevance_filter = relevance_cfg.get("enabled", False) and not keyword_filter

    database_url = None
    log("Сохранение в файлы JSONL/CSV (папка «Новости»).")

    items, health, paths = run_all_sources(
        SOURCES_PATH,
        NEWS_DIR,
        source_names=source_names,
        period="day",
        since=day_str,
        until=day_str,
        database_url=database_url,
        keyword_filter=keyword_filter,
        relevance_filter=relevance_filter,
    )

    ok_count = sum(1 for item in items if item.status == "ok")
    log(f"Собрано записей: {len(items)} (ok: {ok_count})")
    jsonl_path = paths.get("jsonl")
    if jsonl_path and jsonl_path.exists():
        log(f"Файл новостей: {jsonl_path.name}")
    for report in health:
        if report.status not in ("ok", "no_items"):
            log(f"  {report.source}: {report.status}")

    rag_stats: dict[str, Any] | None = None
    rag_db_url = os.getenv("DATABASE_URL", "").strip()
    if rag_db_url and ok_count:
        log("Индексация новостей в RAG-базу (pgvector) с полным текстом…")
        rag_stats = index_news_items(
            rag_db_url,
            [item.to_dict() for item in items],
            indexed_by="web-parse",
            fetch_full=True,
            log=lambda msg: log(f"  {msg}"),
        )
        log(
            f"RAG: новостей={rag_stats['documents']}, чанков={rag_stats['chunks']}, "
            f"полный текст догружен={rag_stats.get('full_text_fetched', 0)}"
        )

    return {
        "items_count": len(items),
        "jsonl_path": str(jsonl_path) if jsonl_path else None,
        "paths": {k: str(v) for k, v in paths.items()},
        "rag": rag_stats,
    }


def run_brief_for_day(
    day: date,
    system_prompt: str,
    *,
    model: str | None = None,
    relevant_only: bool = True,
    include_kallanish: bool = True,
    skip_parse: bool = False,
    brief_kind: str = "full",
    log: LogFn,
) -> dict[str, Any]:
    load_dotenv()
    if brief_kind not in ("full", "market", "corporate"):
        brief_kind = "full"
    if not os.getenv("OPENROUTER_API_KEY", "").strip():
        raise RuntimeError("Не задан OPENROUTER_API_KEY в файле .env")

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL не задан. Дневной бриф берёт новости из RAG-базы (pgvector)."
        )

    if not skip_parse:
        run_parse_for_day(day, log)

    period_range = build_period_range(since=day.isoformat(), until=day.isoformat())
    brief_input = load_brief_input_from_rag(
        database_url,
        period_range,
        relevant_only=relevant_only,
    )
    if not brief_input.news:
        raise ValueError(
            f"В RAG-базе нет новостей за {day.isoformat()}. "
            "Сначала спарсите день (кнопка парсинга наполняет RAG)."
        )

    log(f"Новостей для LLM (из RAG): {len(brief_input.news)}")

    context = BriefContext(
        indicators_path=PROJECT_DIR / "Список показателей.xlsx",
        format_pdf_path=PROJECT_DIR / "news2026-03-19.pdf",
        news_dir=NEWS_DIR,
        include_kallanish=include_kallanish,
    )
    effective_model = model or BRIEF_MODEL
    log(f"Генерация брифа (модель {effective_model})…")

    content, metadata = generate_brief_comment(
        brief_input,
        model=effective_model,
        context=context,
        project_dir=PROJECT_DIR,
        system_prompt=system_prompt,
        brief_kind=brief_kind,
    )

    log(
        f"Готово: тип {brief_kind}, режим {metadata.get('generation_mode')}, "
        f"запросов к API: {metadata.get('api_calls', 1)}, "
        f"символов в ответе: {metadata.get('content_chars', len(content))}"
    )
    if len(content.strip()) < 800:
        log(
            "Предупреждение: ответ LLM подозрительно короткий — "
            "возможен пустой дайджест. Попробуйте перегенерировать."
        )

    docx_path = NEWS_DIR / brief_docx_filename(day, brief_kind)
    metadata["generated_at"] = datetime.now().isoformat(timespec="seconds")
    write_brief_docx(
        docx_path,
        content,
        report_date=period_range.end.strftime("%d.%m.%Y"),
        period_label=(
            f"{period_range.start.strftime('%d.%m.%Y')} — "
            f"{period_range.end.strftime('%d.%m.%Y')}"
        ),
    )
    log(f"Word-документ: {docx_path.name}")

    return {
        "docx_path": str(docx_path),
        "docx_filename": docx_path.name,
        "brief_kind": brief_kind,
        "metadata": metadata,
        "news_count": len(brief_input.news),
    }


def get_default_system_prompt(variant: str = "full") -> str:
    return get_system_prompt_variant(variant)


def list_system_prompt_variants() -> list[dict[str, str]]:
    from news_parsers.llm.brief import list_system_prompt_variants as _list

    return _list()
