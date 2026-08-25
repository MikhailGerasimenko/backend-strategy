"""Генерация брифа за период из СЫРЫХ новостей (RAG, map-reduce).

Принцип:
- берём новости за период из rag_news_documents (с дедупом перепечаток);
- map: фактологический дайджест по батчам;
- reduce: финальный бриф СТРОГО по выбранному системному промпту;
- блок «Источники» и ссылки [n] — только если промпт это разрешает (например, ежемесячный).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Sequence

from ..rag.vector_backend import NewsDocument, fetch_news_documents_for_period
from .brief import SYSTEM_PROMPT_VARIANTS
from .config import BRIEF_CHUNK_SIZE, BRIEF_MODEL, MAX_TOKENS
from .openrouter import GenerationCancelled, chat_completion, _check_cancel
from .weekly_brief import WEEKLY_SYSTEM_PROMPTS, get_weekly_system_prompt

PERIOD_PROMPT_VERSION = "period_brief_v4_clean_map_reduce"

MAP_CHAR_BUDGET = 220_000
# Для ежемесячных — как в дневном: фиксированное число целых новостей на вызов.
# Единый размер map-батча для всех period-брифов (как дневной).
MAP_NEWS_PER_BATCH = BRIEF_CHUNK_SIZE  # по умолчанию 60
PDF_TOP_K = 16

PERIOD_BRIEF_KINDS = (
    "full",
    "market",
    "corporate",
    "monthly",  # legacy, скрыт в UI
    "monthly_news",
    "monthly_market",
    "monthly_corporate",  # legacy alias → monthly_market prompt fallback
)

MONTHLY_BRIEF_KINDS = frozenset(
    {"monthly", "monthly_news", "monthly_market", "monthly_corporate"}
)

# Варианты, которые показываем в меню на странице брифа.
PERIOD_PROMPT_MENU_KINDS = (
    "full",
    "market",
    "corporate",
    "monthly_news",
    "monthly_market",
)


def is_monthly_brief_kind(kind: str) -> bool:
    return kind in MONTHLY_BRIEF_KINDS


def map_char_budget_for_kind(kind: str) -> int:
    # Оставлено для не-monthly period-брифов (полный/рыночный/новостной за период).
    return MAP_CHAR_BUDGET


MAP_SYSTEM_PROMPT = (
    "Ты — фактограф для подготовки брифа. "
    "На вход — пачка материалов с номерами [n] (новости и/или тексты Kallanish/PDF/PMI). "
    "Сделай плотный фактологический дайджест на русском.\n"
    "Правила:\n"
    "• только факты из текста, без воды и без выдумок;\n"
    "• группируй по темам: цены, спрос, предложение, макро, компании, регулирование;\n"
    "• каждый факт — со ссылкой [n]; несколько источников — [12][15];\n"
    "• одинаковые события схлопывай в один пункт с несколькими номерами;\n"
    "• цифры, цены, даты, названия компаний и стран сохраняй дословно;\n"
    "• для длинных обзоров (Kallanish и т.п.) извлеки все существенные ценовые "
    "и рыночные факты по разделам, не ограничивайся одним абзацем.\n"
    "Не пиши финальный бриф и не копируй структуру системного промпта отчёта — "
    "только дайджест фактов."
)

MAP_USER_TEMPLATE = """Период: {period_label}
Батч {batch_index}/{batch_total}

Материалы:
{batch_body}

Составь фактологический дайджест по правилам system-промпта."""


def _period_label(period_start: date, period_end: date) -> str:
    if period_start == period_end:
        return period_start.strftime("%d.%m.%Y")
    return f"{period_start.strftime('%d.%m.%Y')} — {period_end.strftime('%d.%m.%Y')}"


def _build_map_user(
    *,
    period_start: date,
    period_end: date,
    batch_index: int,
    batch_total: int,
    batch_body: str,
) -> str:
    return MAP_USER_TEMPLATE.format(
        period_label=_period_label(period_start, period_end),
        batch_index=batch_index,
        batch_total=batch_total,
        batch_body=batch_body,
    )


def _split_text_parts(text: str, *, max_chars: int) -> list[str]:
    """Режет длинный документ на части по абзацам, не превышая max_chars."""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]
    paragraphs = re.split(r"\n\s*\n", cleaned)
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        piece = para.strip()
        if not piece:
            continue
        add_len = len(piece) + (2 if current else 0)
        if current and current_len + add_len > max_chars:
            parts.append("\n\n".join(current))
            current = [piece]
            current_len = len(piece)
            continue
        if not current and len(piece) > max_chars:
            # Один гигантский абзац — режем жёстко.
            for start in range(0, len(piece), max_chars):
                parts.append(piece[start : start + max_chars])
            current = []
            current_len = 0
            continue
        current.append(piece)
        current_len += add_len
    if current:
        parts.append("\n\n".join(current))
    return parts


def _attachments_to_news_documents(
    database_url: str,
    attachment_ids: Sequence[int],
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    part_char_budget: int,
    log: Any = None,
) -> list[NewsDocument]:
    """Полные тексты выбранных вложений → «новости» для map-reduce."""
    from ..rag.vector_backend import get_attachment_documents_by_ids

    requested = len({int(i) for i in attachment_ids if int(i) > 0})
    attachments = get_attachment_documents_by_ids(
        database_url,
        attachment_ids,
        period_start=period_start,
        period_end=period_end,
    )
    if log and period_start and period_end and len(attachments) < requested:
        log(
            f"Документы вне периода {period_start.isoformat()}—{period_end.isoformat()} "
            f"отброшены: осталось {len(attachments)} из {requested}"
        )
    if not attachments:
        return []
    docs: list[NewsDocument] = []
    for att in attachments:
        parts = _split_text_parts(att.full_text, max_chars=part_char_budget)
        total_parts = len(parts)
        if log:
            log(
                f"  документ «{att.title or att.document_type}»: "
                f"{len(att.full_text) // 1000}k символов → {total_parts} частей для map"
            )
        for index, part in enumerate(parts, start=1):
            title = att.title or att.document_type or f"document-{att.id}"
            if total_parts > 1:
                title = f"{title} (часть {index}/{total_parts})"
            docs.append(
                NewsDocument(
                    id=att.id * 1000 + index,
                    news_date=att.brief_date,
                    source=f"DOC {att.document_type or 'attachment'}",
                    category="attachment",
                    title=title,
                    url="",
                    summary="",
                    full_text=part,
                    keyword_block="attachment",
                    full_text_status="fetched",
                )
            )
    return docs


def list_period_prompt_variants() -> list[dict[str, str]]:
    variants: list[dict[str, str]] = []
    for key in PERIOD_PROMPT_MENU_KINDS:
        if key in SYSTEM_PROMPT_VARIANTS:
            variants.append({"id": key, "label": SYSTEM_PROMPT_VARIANTS[key]["label"]})
            continue
        item = WEEKLY_SYSTEM_PROMPTS.get(key)
        if item:
            variants.append({"id": key, "label": item["label"]})
    return variants


def citations_allowed(system_prompt: str, brief_kind: str = "full") -> bool:
    """Нужны ли [n] и блок «Источники» в финальном брифе."""
    # Ежемесячные брифы — без ссылок и без блока «Источники».
    if is_monthly_brief_kind(brief_kind):
        return False
    low = (system_prompt or "").lower()
    forbids = (
        "не добавляй строки «источник" in low
        or "не добавляй строки «источники" in low
        or "нигде в брифе не добавляй" in low
        or "запрещены ссылки на источники" in low
        or "не ссылайся на источники в скобках" in low
        or "без ссылок" in low
        or "не ставь ссылки [n]" in low
    )
    if forbids:
        return False
    if "[n]" in system_prompt or "ссылкой [n]" in low or "каждый факт — со ссылкой" in low:
        return True
    return False


_DATE_RANGE_RE = re.compile(
    r"\d{2}\.\d{2}\.\d{4}(?:\s*[—\-–]\s*\d{2}\.\d{2}\.\d{4})?"
)


def adapt_prompt_for_period(
    prompt: str,
    *,
    period_start: date,
    period_end: date,
) -> str:
    """Подставляет актуальные даты периода (всегда перезаписывает старые)."""
    start_fmt = period_start.strftime("%d.%m.%Y")
    end_fmt = period_end.strftime("%d.%m.%Y")
    period_label = start_fmt if period_start == period_end else f"{start_fmt} — {end_fmt}"
    text = (prompt or "").strip()

    # Убираем старый преамбул, чтобы не залипали даты с прошлой загрузки шаблона.
    if text.startswith("Период брифа:"):
        parts = text.split("\n\n", 1)
        text = parts[1].strip() if len(parts) > 1 else ""

    text = text.replace("{текущая дата}", period_label)
    text = text.replace("{дата}", period_label)
    text = text.replace("{месяц и год}", period_start.strftime("%m.%Y"))

    # Обновляем уже подставленные даты в заголовках шаблона
    # (например после смены периода в UI без перезагрузки промпта).
    text = re.sub(
        r"(##\s*Ежедневный[^\n(]*\()\s*"
        + _DATE_RANGE_RE.pattern
        + r"(\s*\))",
        lambda m: f"{m.group(1)}{period_label}{m.group(2)}",
        text,
    )
    text = re.sub(
        r"(##\s*РЕЗЮМЕ\s*\()\s*" + _DATE_RANGE_RE.pattern + r"(\s*\))",
        lambda m: f"{m.group(1)}{period_label}{m.group(2)}",
        text,
    )

    preface = (
        f"Период брифа: {period_label}.\n"
        "Соблюдай структуру и правила системного промпта ниже ДОСЛОВНО. "
        "Не меняй обязательную структуру разделов. "
        "В заголовках используй ТОЛЬКО этот период — не подставляй другие даты. "
        "Если в промпте написано «ежедневный», а период — один день, используй этот день; "
        "если период длиннее одного дня — сохрани ту же структуру разделов, "
        "но пиши сводку за весь период (не выдумывай отдельный формат).\n\n"
    )
    return preface + text


def get_period_system_prompt(
    variant: str,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> str:
    """Промпт для страницы «Бриф за период»: дневные шаблоны + monthly*."""
    key = variant if variant in PERIOD_BRIEF_KINDS else "full"
    if key == "monthly_corporate":
        key = "monthly_market"
    if is_monthly_brief_kind(key):
        prompt = get_weekly_system_prompt(key)
    else:
        prompt = SYSTEM_PROMPT_VARIANTS[key]["prompt"]
    if period_start and period_end:
        return adapt_prompt_for_period(
            prompt, period_start=period_start, period_end=period_end
        )
    return prompt


def _normalize_fingerprint(text: str, *, limit: int = 220) -> str:
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"[#*_`]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned[:limit]


def _dedupe_documents(docs: Sequence[NewsDocument]) -> list[NewsDocument]:
    """Схлопывает перепечатки (один пост в нескольких TG-каналах)."""
    best_by_key: dict[str, NewsDocument] = {}
    order: list[str] = []

    for doc in docs:
        url = (doc.url or "").strip().lower().replace("https://t.me/", "https://telegram.me/")
        body = _normalize_fingerprint(doc.full_text or doc.summary or doc.title)
        title = _normalize_fingerprint(doc.title or "", limit=120)
        # Сначала текст: репосты в TG имеют разные URL, но один body.
        if len(body) >= 80:
            key = f"body:{body}"
        elif url:
            key = f"url:{url}"
        else:
            key = f"title:{doc.news_date.isoformat()}:{title}"

        prev = best_by_key.get(key)
        if prev is None:
            best_by_key[key] = doc
            order.append(key)
            continue
        prev_len = len(prev.full_text or "")
        cur_len = len(doc.full_text or "")
        prev_tg = (prev.source or "").startswith("TG ")
        cur_tg = (doc.source or "").startswith("TG ")
        if cur_len > prev_len + 40 or (prev_tg and not cur_tg and cur_len >= prev_len * 0.8):
            best_by_key[key] = doc

    return [best_by_key[key] for key in order]


def _batch_documents(
    docs: Sequence[NewsDocument], *, char_budget: int
) -> list[list[tuple[int, NewsDocument]]]:
    """Батчи по бюджету символов. Каждая новость целиком — не режется на части."""
    batches: list[list[tuple[int, NewsDocument]]] = []
    current: list[tuple[int, NewsDocument]] = []
    current_chars = 0
    for ref, doc in enumerate(docs, start=1):
        doc_chars = len(doc.full_text) + len(doc.title) + 80
        if current and current_chars + doc_chars > char_budget:
            batches.append(current)
            current = []
            current_chars = 0
        current.append((ref, doc))
        current_chars += doc_chars
    if current:
        batches.append(current)
    return batches


def _batch_documents_by_count(
    docs: Sequence[NewsDocument], *, size: int
) -> list[list[tuple[int, NewsDocument]]]:
    """Батчи по числу новостей целиком (как дневной бриф: ~60 штук на вызов)."""
    if size < 1:
        size = 1
    numbered = list(enumerate(docs, start=1))
    return [
        numbered[i : i + size]
        for i in range(0, len(numbered), size)
    ]


def _render_batch(batch: Sequence[tuple[int, NewsDocument]]) -> str:
    parts: list[str] = []
    for ref, doc in batch:
        parts.append(
            f"[{ref}] ({doc.news_date.isoformat()}, источник: {doc.source or '—'})\n"
            f"Заголовок: {doc.title}\n"
            f"Текст: {doc.full_text}"
        )
    return "\n\n".join(parts)


def _collect_kallanish_file_context(news_dir: Path | None) -> str:
    if news_dir is None:
        return ""
    try:
        from ..llm.kallanish_docx import build_kallanish_block

        block, path = build_kallanish_block(explicit_path=None, news_dir=news_dir, include=True)
    except Exception:
        return ""
    if not path or not block or block.startswith("("):
        return ""
    return block


def _collect_pdf_context(
    database_url: str,
    *,
    period_start: date,
    period_end: date,
    kind: str,
    query: str,
    news_dir: Path | None = None,
    attachment_ids: Sequence[int] | None = None,
) -> str:
    # Явный пустой список — пользователь отключил все документы.
    if attachment_ids is not None and not attachment_ids:
        return ""
    try:
        from ..rag.vector_backend import retrieve_attachment_chunks_for_period

        chunks = retrieve_attachment_chunks_for_period(
            database_url,
            period_start=period_start,
            period_end=period_end,
            query_text=query,
            top_k=PDF_TOP_K,
            document_ids=attachment_ids,
        )
    except Exception:
        return ""
    # Файловый Kallanish из папки — только если документы не фильтровали явно.
    allow_file_fallback = attachment_ids is None
    if not chunks:
        return _collect_kallanish_file_context(news_dir) if allow_file_fallback else ""
    lines = [
        f"### {c.document_type} ({c.brief_date.isoformat()})\n{c.content}" for c in chunks
    ]
    text = "\n\n".join(lines)
    if allow_file_fallback:
        kallanish_block = _collect_kallanish_file_context(news_dir)
        if kallanish_block and "kallanish" not in text.lower():
            return text + "\n\n### Kallanish (файл)\n" + kallanish_block
    return text


def _referenced_numbers(text: str) -> list[int]:
    found = {int(m) for m in re.findall(r"\[(\d+)\]", text)}
    return sorted(found)


def _clean_source_title(title: str, *, max_len: int = 120) -> str:
    value = re.sub(r"\s+", " ", (title or "").strip())
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "…"


def _build_sources_block(text: str, ref_map: dict[int, NewsDocument]) -> str:
    used = _referenced_numbers(text)
    if not used:
        return ""
    lines = ["", "## Источники"]
    for ref in used:
        doc = ref_map.get(ref)
        if not doc:
            continue
        title = _clean_source_title(doc.title)
        lines.append(f"[{ref}] {title} — {doc.url} ({doc.news_date.isoformat()})")
    return "\n".join(lines)


def _strip_citation_artifacts(text: str) -> str:
    """Убирает [n] и блок «Источники», если промпт их запрещает."""
    cleaned = re.split(r"\n##\s*Источники\b", text or "", maxsplit=1)[0]
    cleaned = re.sub(r"\[(?:\d+)\]", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" +\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _build_reduce_user(
    *,
    period_start: date,
    period_end: date,
    kind: str,
    combined_digests: str,
    pdf_context: str,
    allow_citations: bool,
    attachments_only: bool = False,
) -> str:
    period_label = _period_label(period_start, period_end)
    if attachments_only:
        material_note = (
            "Ниже — фактодайджесты по полным текстам документов "
            "(Kallanish/PDF/PMI). Это основной материал брифа."
        )
    else:
        material_note = (
            "Ниже — фактодайджесты по новостям и документам периода. "
            "Номера [n] внутри дайджестов — служебные, для сборки фактов."
        )

    rules = [
        "Собери ОДИН итоговый бриф строго по системному промпту.",
        "Правила:",
        "• структура разделов, тон и формат — только из системного промпта;",
        "• не выдумывай факты, цифры, компании и события;",
        "• одинаковые события/перепечатки схлопни в один факт;",
        "• если в дайджестах уже есть цены, котировки или события — перенеси их "
        "в нужные разделы; не пиши «Релевантных новостей нет», когда факты есть;",
    ]
    if allow_citations:
        rules.extend(
            [
                "• сохраняй ссылки [n] рядом с фактами (номера не меняй);",
                "• при объединении событий ставь несколько ссылок, например [12][15];",
                "• блок «Источники» не пиши — система добавит его отдельно.",
            ]
        )
    else:
        rules.extend(
            [
                "• в финальный текст не выноси [n], «Источник», «Источники», URL и названия СМИ;",
                "• внутренние [n] из дайджестов используй только чтобы не потерять факты.",
            ]
        )

    parts = [
        f"Период брифа: {period_label}",
        f"Тип брифа: {kind}",
        "",
        material_note,
        "",
        *rules,
        "",
        combined_digests,
    ]
    if pdf_context:
        parts.extend(
            [
                "",
                "Дополнительный контекст из документов (фон, не подменяй им дайджесты):",
                pdf_context,
            ]
        )
    return "\n".join(parts)


def generate_period_brief(
    database_url: str,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str = "full",
    system_prompt: str | None = None,
    model: str | None = None,
    log: Any = None,
    news_dir: Path | None = None,
    sources: Sequence[str] | None = None,
    attachment_ids: Sequence[int] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> tuple[str, dict[str, Any]]:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    _check_cancel(should_cancel)
    kind = brief_kind if brief_kind in PERIOD_BRIEF_KINDS else "full"
    if kind == "monthly_corporate":
        kind = "monthly_market"
    raw_system = (system_prompt or "").strip() or get_period_system_prompt(
        kind, period_start=period_start, period_end=period_end
    )
    effective_system = adapt_prompt_for_period(
        raw_system, period_start=period_start, period_end=period_end
    )
    allow_citations = citations_allowed(effective_system, kind)
    effective_model = model or BRIEF_MODEL

    if sources is not None:
        _log(
            f"Фильтр источников: {len(sources)} "
            f"({'новости отключены' if not sources else f'новостей: ожидание…'})"
        )
    docs = fetch_news_documents_for_period(
        database_url,
        period_start=period_start,
        period_end=period_end,
        sources=sources,
    )
    if sources is not None:
        _log(f"После фильтра источников новостей: {len(docs)}")

    char_budget = map_char_budget_for_kind(kind)
    # Явно выбранные документы — в map по ПОЛНОМУ тексту (как DeepSeek с целым файлом),
    # а не top-k семантических чанков.
    attachment_docs: list[NewsDocument] = []
    if attachment_ids:
        _log(f"Загрузка полных текстов выбранных документов: {len(attachment_ids)} шт.…")
        attachment_docs = _attachments_to_news_documents(
            database_url,
            attachment_ids,
            period_start=period_start,
            period_end=period_end,
            part_char_budget=char_budget,
            log=_log,
        )
        if attachment_docs:
            _log(
                f"Документы для map: {len(attachment_docs)} частей "
                f"(~{sum(len(d.full_text) for d in attachment_docs) // 1000}k символов)"
            )
        else:
            _log("Выбранные document_id не найдены или без текста")

    before_dedupe = len(docs)
    if docs:
        docs = _dedupe_documents(docs)
        if len(docs) < before_dedupe:
            _log(f"Дедуп перепечаток: {before_dedupe} → {len(docs)} новостей")

    combined_docs = list(docs) + attachment_docs
    if not combined_docs:
        raise ValueError(
            f"Нет материалов для брифа за {period_start.isoformat()} — "
            f"{period_end.isoformat()}: ни новостей, ни полных текстов документов. "
            "Выберите источники и/или отметьте Kallanish/PDF в библиотеке."
        )

    attachments_only = not docs and bool(attachment_docs)
    if attachments_only:
        _log("Режим только документы (Kallanish/PDF): map-reduce по полному тексту")

    ref_map: dict[int, NewsDocument] = {
        ref: doc for ref, doc in enumerate(combined_docs, start=1)
    }
    total_chars = sum(len(d.full_text) for d in combined_docs)
    _log(
        f"Материалов в map: {len(combined_docs)} "
        f"(новости: {len(docs)}, частей документов: {len(attachment_docs)}, "
        f"~{total_chars // 1000}k символов); "
        f"цитаты в финале: {'да' if allow_citations else 'нет (по промпту)'}"
    )

    batch_size = MAP_NEWS_PER_BATCH
    batches = _batch_documents_by_count(combined_docs, size=batch_size)
    _log(
        f"Map-этап: {len(batches)} батч(ей) по ~{batch_size} материалов "
        f"(новости/части документов; материал не режется между вызовами)"
    )

    map_batches = len(batches)
    digests: list[str] = []
    for index, batch in enumerate(batches, start=1):
        _check_cancel(should_cancel)
        _log(f"  обработка батча {index}/{len(batches)} ({len(batch)} материалов)…")
        digest = chat_completion(
            system_prompt=MAP_SYSTEM_PROMPT,
            user_prompt=_build_map_user(
                period_start=period_start,
                period_end=period_end,
                batch_index=index,
                batch_total=len(batches),
                batch_body=_render_batch(batch),
            ),
            model=effective_model,
            max_tokens=MAX_TOKENS,
            temperature=0.15,
            should_cancel=should_cancel,
        )
        digests.append(digest.strip())

    # Семантический top-k больше не нужен, если полные тексты уже прошли map.
    # Оставляем fallback только когда вложения не заданы явно (legacy).
    pdf_context = ""
    if attachment_ids is None:
        label = (
            WEEKLY_SYSTEM_PROMPTS.get(kind, {}).get("label")
            or SYSTEM_PROMPT_VARIANTS.get(kind, {}).get("label")
            or kind
        )
        pdf_context = _collect_pdf_context(
            database_url,
            period_start=period_start,
            period_end=period_end,
            kind=kind,
            query=label,
            news_dir=news_dir,
            attachment_ids=None,
        )
        if pdf_context:
            _log("Дополнительно подмешан семантический контекст вложений (без явного выбора ID)")

    _check_cancel(should_cancel)
    _log("Reduce-этап: сборка финального брифа строго по системному промпту…")
    combined_digests = "\n\n".join(
        f"=== Дайджест {i} ===\n{d}" for i, d in enumerate(digests, start=1)
    )
    reduce_user = _build_reduce_user(
        period_start=period_start,
        period_end=period_end,
        kind=kind,
        combined_digests=combined_digests,
        pdf_context=pdf_context,
        allow_citations=allow_citations,
        attachments_only=attachments_only,
    )

    content = chat_completion(
        system_prompt=effective_system,
        user_prompt=reduce_user,
        model=effective_model,
        max_tokens=MAX_TOKENS,
        temperature=0.25,
        should_cancel=should_cancel,
    ).strip()

    if allow_citations:
        sources_block = _build_sources_block(content, ref_map)
        if sources_block:
            content = content.rstrip() + "\n\n" + sources_block
    else:
        content = _strip_citation_artifacts(content)

    metadata: dict[str, Any] = {
        "prompt_version": PERIOD_PROMPT_VERSION,
        "brief_kind": kind,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "generation_mode": "rag_raw_news_map_reduce",
        "news_documents": len(docs),
        "news_before_dedupe": before_dedupe,
        "attachment_parts": len(attachment_docs),
        "map_batches": map_batches,
        "pdf_context": bool(pdf_context),
        "citations_in_output": allow_citations,
        "model": effective_model,
        "custom_system_prompt": bool(system_prompt and system_prompt.strip()),
        "sources_filter": list(sources) if sources is not None else None,
        "attachment_ids": list(attachment_ids) if attachment_ids is not None else None,
        "attachments_only": attachments_only,
    }
    return content, metadata
