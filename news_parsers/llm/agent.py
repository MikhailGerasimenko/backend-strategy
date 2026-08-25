"""ИИ-агент по новостям: вопрос → семантический поиск → ответ со ссылками.

Поддерживает «дай новость целиком»: для обычных новостей — полный текст,
для Kallanish/PDF — читаемый фрагмент вокруг процитированного куска.
"""

from __future__ import annotations

from html import unescape
import json
import re
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

from ..rag.vector_backend import (
    AttachmentSearchHit,
    NewsSearchHit,
    get_attachment_chunks_for_documents,
    get_attachment_section,
    get_news_chunks_for_documents,
    get_news_document,
    search_attachment_chunks,
    search_news_chunks,
)
from .config import BRIEF_MODEL
from .openrouter import chat_completion

_CHANNELS_FILE = Path(__file__).resolve().parents[2] / "telegram_channels.json"
_DATE_RE = re.compile(
    r"\b(\d{1,2})[./](\d{1,2})[./](\d{2,4})\b|\b(\d{4})-(\d{1,2})-(\d{1,2})\b"
)
_TG_SOURCE_RE = re.compile(r"(?:TG|tg|@)\s*([A-Za-z0-9_]+)")
_CITE_RE = re.compile(r"\[(\d{1,3})\]")
_ATTACHMENT_MARKERS = ("kallanish", "pdf", "отчёт", "отчет", "документ", "word")

AGENT_SYSTEM_PROMPT = (
    "Ты — умный ИИ-ассистент и аналитик по металлургии и сырьевым рынкам для «Северстали». "
    "Работай как обычная нейросеть: отвечай на любые вопросы (объяснения, расчёты, "
    "справки, рассуждения, помощь по тексту), опираясь на свои знания. "
    "Дополнительно тебе могут дать фрагменты из внутренней базы новостей и документов "
    "(Kallanish/PDF) с номерами [n].\n"
    "Правила:\n"
    "1) Если фрагменты из базы релевантны вопросу — используй их в первую очередь для "
    "фактов, цифр, дат и цитат; каждый такой факт помечай ссылкой [n].\n"
    "2) Если фрагментов нет или их недостаточно — отвечай по общим знаниям. "
    "Явно отделяй: что взято из базы ([n]), а что — общий контекст/оценка "
    "(без выдуманных «точных» котировок «из базы»).\n"
    "3) Не выдумывай, что цифры или новости «есть в базе», если их нет во фрагментах.\n"
    "4) Если пользователь просит показать новость/документ целиком — укажи, что полный "
    "текст можно открыть по источнику [n].\n"
    "5) Отвечай на русском, по делу, без воды."
)

AGENT_PLAIN_CHAT_PROMPT = (
    "Ты — умный ИИ-ассистент. Отвечай на русском, по делу, без воды. "
    "Опирайся на свои знания. Не ссылайся на внутреннюю базу новостей и не выдумывай источники."
)

# Слова-триггеры запроса полного текста.
_FULL_TEXT_PATTERNS = (
    "целиком",
    "полностью",
    "полный текст",
    "весь текст",
    "прочитать новость",
    "открой новость",
    "покажи новость",
)

_FOLLOW_UP_MARKERS = (
    "подробн",
    "детальн",
    "ещё",
    "еще",
    "больше",
    "продолж",
    "разверн",
    "уточн",
    "раскрой",
    "поясни",
    "углуб",
    "дополни",
)

_MAX_FOLLOW_UP_DOC_CHARS = 28_000


def _asks_full_text(message: str) -> bool:
    low = (message or "").lower()
    return any(pattern in low for pattern in _FULL_TEXT_PATTERNS)


def wants_full_text(message: str) -> int | None:
    if not _asks_full_text(message):
        return None
    nums = re.findall(r"\b(\d{1,3})\b", (message or "").lower())
    if nums:
        return int(nums[0])
    return None


def _chunk_index_from_source(src: dict[str, Any]) -> int | None:
    value = src.get("chunk_index")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_document_text_for_display(text: str) -> str:
    """Делает текст Kallanish/PDF читаемым: HTML, таблицы, лишние пробелы."""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"<[a-zA-Z][^>]*>", raw):
        raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
        raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
        raw = re.sub(r"(?i)</p>", "\n\n", raw)
        raw = re.sub(r"(?i)</tr>", "\n", raw)
        raw = re.sub(r"(?i)</h[1-6]>", "\n\n", raw)
        raw = re.sub(r"(?i)</(td|th)>", " | ", raw)
        raw = re.sub(r"<[^>]+>", " ", raw)
    raw = unescape(raw)
    raw = raw.replace("\xa0", " ").replace("\u200b", "")
    lines: list[str] = []
    for line in raw.split("\n"):
        cleaned = re.sub(r"[ \t]+", " ", line).strip()
        cleaned = re.sub(r"(?:\s*\|\s*){2,}", " | ", cleaned)
        cleaned = cleaned.strip(" |")
        if cleaned:
            lines.append(cleaned)
        elif lines and lines[-1] != "":
            lines.append("")
    compact = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return compact


def _format_hits(hits: list[NewsSearchHit]) -> str:
    lines: list[str] = []
    for index, hit in enumerate(hits, start=1):
        lines.append(
            f"[{index}] ({hit.news_date.isoformat()}, источник: {hit.source or '—'}) "
            f"{hit.title}\n{hit.content}"
        )
    return "\n\n".join(lines)


def _format_attachment_hits(hits: list[AttachmentSearchHit], *, start_index: int) -> str:
    lines: list[str] = []
    for offset, hit in enumerate(hits):
        index = start_index + offset
        lines.append(
            f"[{index}] ({hit.brief_date.isoformat()}, документ: {hit.document_type or '—'}) "
            f"{hit.title}\n{hit.content}"
        )
    return "\n\n".join(lines)


def _hits_to_sources(hits: list[NewsSearchHit]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for index, hit in enumerate(hits, start=1):
        sources.append(
            {
                "ref": index,
                "kind": "news",
                "document_id": hit.document_id,
                "title": hit.title,
                "url": hit.url,
                "source": hit.source,
                "news_date": hit.news_date.isoformat(),
                "score": round(hit.score, 3),
            }
        )
    return sources


def _attachment_hits_to_sources(
    hits: list[AttachmentSearchHit], *, start_index: int
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for offset, hit in enumerate(hits):
        sources.append(
            {
                "ref": start_index + offset,
                "kind": "attachment",
                "document_id": hit.document_id,
                "chunk_index": hit.chunk_index,
                "title": hit.title,
                "url": "",
                "source": hit.document_type,
                "news_date": hit.brief_date.isoformat(),
                "score": round(hit.score, 3),
            }
        )
    return sources


def _ranked_document_ids(hits: Sequence[Any], *, id_attr: str = "document_id") -> list[int]:
    """Уникальные document_id в порядке лучшего score."""
    best: dict[int, float] = {}
    for hit in hits:
        doc_id = int(getattr(hit, id_attr))
        score = float(getattr(hit, "score", 0.0) or 0.0)
        prev = best.get(doc_id)
        if prev is None or score > prev:
            best[doc_id] = score
    return [doc_id for doc_id, _ in sorted(best.items(), key=lambda item: -item[1])]


def _expand_news_hits_to_all_chunks(
    database_url: str,
    hits: list[NewsSearchHit],
) -> list[NewsSearchHit]:
    doc_ids = _ranked_document_ids(hits)
    if not doc_ids:
        return []
    return get_news_chunks_for_documents(database_url, doc_ids)


def _expand_attachment_hits_to_all_chunks(
    database_url: str,
    hits: list[AttachmentSearchHit],
) -> list[AttachmentSearchHit]:
    doc_ids = _ranked_document_ids(hits)
    if not doc_ids:
        return []
    return get_attachment_chunks_for_documents(database_url, doc_ids)


_QUERY_EXPANSIONS: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("гк рулон", "гк-рулон", "горячеката", "hrc", "hot-rolled", "hot rolled", "hotrolled"),
        "HRC hot-rolled coil ГК рулон hot rolled coil prices цена",
    ),
    (
        ("хк рулон", "холодноката", "crc", "cold-rolled", "cold rolled"),
        "CRC cold-rolled coil ХК рулон cold rolled coil prices цена",
    ),
    (
        ("заготовка", "billet", "сляб", "slab"),
        "billet slab заготовка сляб prices цена",
    ),
)


def _expand_search_query(question: str) -> str:
    """Добавляет англ./отраслевые синонимы — embeddings иначе плохо ловят «ГК рулон»↔HRC."""
    low = (question or "").lower().replace("ё", "е")
    extras: list[str] = []
    for markers, expansion in _QUERY_EXPANSIONS:
        if any(m in low for m in markers):
            extras.append(expansion)
    if not extras:
        return question
    return f"{question}\n{' '.join(extras)}"


def _known_tg_channels() -> tuple[str, ...]:
    names: list[str] = []
    if _CHANNELS_FILE.is_file():
        try:
            data = json.loads(_CHANNELS_FILE.read_text(encoding="utf-8"))
            names.extend(str(ch) for ch in data.get("channels", []) if ch)
        except (OSError, json.JSONDecodeError):
            pass
    try:
        from news_parsers.custom_telegram import list_custom_channels

        names.extend(row["channel"] for row in list_custom_channels() if row.get("channel"))
    except Exception:
        pass
    return tuple(dict.fromkeys(names))


def _parse_date_token(day: str, month: str, year: str) -> date | None:
    y = int(year)
    if y < 100:
        y += 2000
    try:
        return date(y, int(month), int(day))
    except ValueError:
        return None


def _extract_dates_from_question(question: str) -> list[date]:
    found: list[date] = []
    for match in _DATE_RE.finditer(question or ""):
        if match.group(1):
            parsed = _parse_date_token(match.group(1), match.group(2), match.group(3))
        else:
            parsed = _parse_date_token(match.group(6), match.group(5), match.group(4))
        if parsed:
            found.append(parsed)
    return found


def _extract_source_filter(question: str) -> str | None:
    text = question or ""
    low = text.lower()
    if "kallanish" in low:
        return None
    tg_match = _TG_SOURCE_RE.search(text)
    if tg_match:
        return tg_match.group(1)
    for channel in _known_tg_channels():
        if channel.lower() in low:
            return channel
    return None


def _extract_document_type_filter(question: str) -> str | None:
    low = (question or "").lower()
    if "kallanish" in low:
        return "Kallanish"
    if "pdf" in low:
        return "PDF"
    return None


def _is_attachment_focused_query(question: str) -> bool:
    low = (question or "").lower()
    return any(marker in low for marker in _ATTACHMENT_MARKERS)


def _is_narrow_source_query(question: str, source_filter: str | None) -> bool:
    if not source_filter:
        return False
    low = (question or "").lower()
    markers = (
        "что пишет",
        "что написал",
        "что писал",
        "новости из",
        "новости в",
        "канал ",
        "посты ",
        "пост ",
    )
    return bool(_extract_dates_from_question(question)) or any(m in low for m in markers)


def _dedupe_hits_by_document(hits: list[NewsSearchHit]) -> list[NewsSearchHit]:
    best: dict[int, NewsSearchHit] = {}
    for hit in hits:
        prev = best.get(hit.document_id)
        if prev is None or hit.score > prev.score:
            best[hit.document_id] = hit
    return sorted(best.values(), key=lambda item: item.score, reverse=True)


def _diversify_hits(
    hits: list[NewsSearchHit], *, top_k: int, max_per_source: int = 2
) -> list[NewsSearchHit]:
    by_source: dict[str, list[NewsSearchHit]] = defaultdict(list)
    for hit in hits:
        by_source[hit.source].append(hit)
    result: list[NewsSearchHit] = []
    while len(result) < top_k:
        added = False
        for source in sorted(by_source, key=lambda name: -(by_source[name][0].score if by_source[name] else 0)):
            bucket = by_source[source]
            used = sum(1 for item in result if item.source == source)
            if bucket and used < max_per_source:
                result.append(bucket.pop(0))
                added = True
                if len(result) >= top_k:
                    break
        if not added:
            break
    return result


def _prepare_hits(
    hits: list[NewsSearchHit],
    *,
    top_k: int,
    diverse: bool,
) -> list[NewsSearchHit]:
    unique = _dedupe_hits_by_document(hits)
    if diverse:
        return _diversify_hits(unique, top_k=top_k)
    return unique[:top_k]


def _extract_cited_refs(answer: str) -> set[int]:
    return {int(value) for value in _CITE_RE.findall(answer or "")}


def _sources_cited_in_answer(
    sources: list[dict[str, Any]], answer: str
) -> list[dict[str, Any]]:
    cited = _extract_cited_refs(answer)
    if not cited:
        return sources[:3]
    return [source for source in sources if int(source.get("ref", 0)) in cited]


def _resolve_search_period(
    question: str,
    period_start: date | None,
    period_end: date | None,
) -> tuple[date | None, date | None]:
    dates = _extract_dates_from_question(question)
    if len(dates) == 1:
        return dates[0], dates[0]
    if len(dates) >= 2:
        return min(dates), max(dates)
    return period_start, period_end


def _is_follow_up_question(question: str) -> bool:
    low = (question or "").lower().strip()
    if not low:
        return False
    if len(low) > 120:
        return False
    return any(marker in low for marker in _FOLLOW_UP_MARKERS)


def _format_chat_history(history: list[dict[str, Any]] | None) -> str:
    if not history:
        return ""
    lines: list[str] = []
    for item in history[-6:]:
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        label = "Пользователь" if role == "user" else "Ассистент"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


def _truncate_doc_text(text: str) -> str:
    compact = (text or "").strip()
    if len(compact) <= _MAX_FOLLOW_UP_DOC_CHARS:
        return compact
    return compact[: _MAX_FOLLOW_UP_DOC_CHARS - 1].rstrip() + "…"


def _load_prior_sources_context(
    database_url: str, prior_sources: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Читаемые фрагменты документов из прошлого ответа — для уточняющих вопросов."""
    parts: list[str] = []
    sources: list[dict[str, Any]] = []
    for index, src in enumerate(prior_sources, start=1):
        kind = str(src.get("kind") or "news")
        document_id = int(src.get("document_id", 0))
        if not document_id:
            continue
        orig_ref = int(src.get("ref") or index)
        if kind == "attachment":
            doc, section = get_attachment_section(
                database_url,
                document_id,
                _chunk_index_from_source(src),
                radius=3,
                max_chars=_MAX_FOLLOW_UP_DOC_CHARS,
            )
            if not doc:
                continue
            text = format_document_text_for_display(_truncate_doc_text(section))
            parts.append(
                f"[{orig_ref}] ({doc.brief_date.isoformat()}, документ: {doc.document_type}) "
                f"{doc.title}\n{text}"
            )
            sources.append(
                {
                    "ref": orig_ref,
                    "kind": "attachment",
                    "document_id": doc.id,
                    "chunk_index": src.get("chunk_index"),
                    "title": doc.title,
                    "url": "",
                    "source": doc.document_type,
                    "news_date": doc.brief_date.isoformat(),
                    "score": src.get("score", 1.0),
                }
            )
            continue
        doc = get_news_document(database_url, document_id)
        if not doc:
            continue
        text = _truncate_doc_text(doc.full_text)
        parts.append(
            f"[{orig_ref}] ({doc.news_date.isoformat()}, источник: {doc.source or '—'}) "
            f"{doc.title}\n{text}"
        )
        sources.append(
            {
                "ref": orig_ref,
                "kind": "news",
                "document_id": doc.id,
                "title": doc.title,
                "url": doc.url,
                "source": doc.source,
                "news_date": doc.news_date.isoformat(),
                "score": src.get("score", 1.0),
            }
        )
    return "\n\n".join(parts), sources


def _compose_search_query(
    question: str,
    history: list[dict[str, Any]] | None,
) -> str:
    if not history:
        return question
    for item in reversed(history):
        if str(item.get("role", "")) == "user":
            prev = str(item.get("content", "")).strip()
            if prev and prev.lower() != question.lower().strip():
                return f"{prev}\n{question}"
            break
    return question


def resolve_agent_system_prompt(system_prompt: str | None = None) -> str:
    prompt, _ = resolve_agent_runtime(system_prompt)
    return prompt


def resolve_agent_runtime(system_prompt: str | None = None) -> tuple[str, bool]:
    """Промпт и флаг поиска по базе.

    None — дефолт агента с RAG.
    Пустая строка — пользователь явно очистил поле: обычный чат без источников.
    """
    if system_prompt is None:
        return AGENT_SYSTEM_PROMPT, True
    custom = system_prompt.strip()
    if not custom:
        return AGENT_PLAIN_CHAT_PROMPT, False
    return custom, True


def answer_question(
    database_url: str,
    *,
    question: str,
    period_start: date | None = None,
    period_end: date | None = None,
    top_k: int = 12,
    model: str | None = None,
    prior_sources: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
) -> dict[str, Any]:
    """Возвращает {answer, sources, full_text?}.

    Если пользователь просит «новость целиком» с номером и есть prior_sources —
    отдаём полный текст соответствующей новости без обращения к LLM.
    """
    effective_system, use_rag = resolve_agent_runtime(system_prompt)
    ref = wants_full_text(question)
    if use_rag and _asks_full_text(question) and prior_sources:
        match = next(
            (s for s in prior_sources if ref is not None and int(s.get("ref", 0)) == ref),
            None,
        )
        if match is None:
            match = prior_sources[0]
            ref = int(match.get("ref", 1) or 1)
        if match:
            kind = str(match.get("kind") or "news")
            if kind == "attachment":
                doc, section = get_attachment_section(
                    database_url,
                    int(match["document_id"]),
                    _chunk_index_from_source(match),
                    radius=2,
                    max_chars=16_000,
                )
                readable = format_document_text_for_display(section)
                if doc and readable:
                    return {
                        "answer": (
                            f"Фрагмент документа [{ref}] «{doc.title}» "
                            f"({doc.brief_date.isoformat()}, {doc.document_type}). "
                            "Это читаемый кусок обзора вокруг цитаты, не весь файл целиком."
                        ),
                        "full_text": {
                            "document_id": doc.id,
                            "title": doc.title,
                            "url": "",
                            "source": doc.document_type,
                            "news_date": doc.brief_date.isoformat(),
                            "text": readable,
                        },
                        "sources": prior_sources,
                    }
            else:
                doc = get_news_document(database_url, int(match["document_id"]))
                if doc:
                    return {
                        "answer": (
                            f"Полный текст новости [{ref}] «{doc.title}» "
                            f"({doc.news_date.isoformat()}, {doc.source}):"
                        ),
                        "full_text": {
                            "document_id": doc.id,
                            "title": doc.title,
                            "url": doc.url,
                            "source": doc.source,
                            "news_date": doc.news_date.isoformat(),
                            "text": format_document_text_for_display(doc.full_text),
                        },
                        "sources": prior_sources,
                    }

    follow_up = use_rag and _is_follow_up_question(question) and bool(prior_sources)
    history_block = _format_chat_history(history)

    if not use_rag:
        user_prompt = (
            (f"Предыдущий диалог:\n{history_block}\n\n" if history_block else "")
            + f"Вопрос пользователя: {question}\n\n"
            "Ответь как обычная нейросеть по своим знаниям. "
            "Не ссылайся на внутреннюю базу новостей и не добавляй источники."
        )
        answer = chat_completion(
            system_prompt=effective_system,
            user_prompt=user_prompt,
            model=model or BRIEF_MODEL,
            temperature=0.35,
        )
        return {"answer": answer.strip(), "sources": []}

    if follow_up and prior_sources:
        context, all_sources = _load_prior_sources_context(database_url, prior_sources)
        if not context:
            follow_up = False
        else:
            user_prompt = (
                (f"Предыдущий диалог:\n{history_block}\n\n" if history_block else "")
                + f"Новый вопрос пользователя: {question}\n\n"
                "Пользователь уточняет предыдущий ответ. Ниже — материалы из базы "
                "(те же источники). Используй их для фактов со ссылками [n]; "
                "если нужно — дополни общими знаниями, явно разделяя источники.\n\n"
                f"Материалы из базы:\n{context}\n\n"
                "Дай развёрнутый ответ."
            )
            answer = chat_completion(
                system_prompt=effective_system,
                user_prompt=user_prompt,
                model=model or BRIEF_MODEL,
                temperature=0.2,
            )
            sources = _sources_cited_in_answer(all_sources, answer.strip())
            return {"answer": answer.strip(), "sources": sources}

    search_query = _expand_search_query(_compose_search_query(question, history))
    search_start, search_end = _resolve_search_period(question, period_start, period_end)
    source_filter = _extract_source_filter(question)
    document_type_filter = _extract_document_type_filter(question)
    attachment_focused = _is_attachment_focused_query(question) or bool(document_type_filter)
    narrow = _is_narrow_source_query(question, source_filter)
    diverse = not narrow and not source_filter and not attachment_focused
    # Семантика только находит релевантные ДОКУМЕНТЫ; дальше подтягиваем ВСЕ их чанки.
    news_seed_k = (
        4
        if attachment_focused and not source_filter
        else (top_k if narrow else max(top_k, 12))
    )
    attachment_seed_k = 12 if attachment_focused else 8
    candidate_limit = max(news_seed_k * 4, 24)

    news_seed = search_news_chunks(
        database_url,
        query_text=search_query,
        period_start=search_start,
        period_end=search_end,
        source_contains=source_filter,
        top_k=news_seed_k,
        candidate_limit=candidate_limit,
    )
    news_seed = _prepare_hits(news_seed, top_k=news_seed_k, diverse=diverse)
    news_hits = _expand_news_hits_to_all_chunks(database_url, news_seed)

    attachment_seed = search_attachment_chunks(
        database_url,
        query_text=search_query,
        period_start=search_start,
        period_end=search_end,
        document_type_contains=document_type_filter,
        top_k=attachment_seed_k,
        candidate_limit=attachment_seed_k * 4,
    )
    attachment_hits = _expand_attachment_hits_to_all_chunks(database_url, attachment_seed)

    if not news_hits and not attachment_hits:
        # Нет хитов в RAG — обычный ответ нейросети без принудительного «в базе пусто».
        user_prompt = (
            (f"Предыдущий диалог:\n{history_block}\n\n" if history_block else "")
            + f"Вопрос пользователя: {question}\n\n"
            "Во внутренней базе новостей/документов по этому запросу подходящих "
            "фрагментов не нашлось (или период/фильтр слишком узкий). "
            "Ответь как обычная нейросеть по своим знаниям. "
            "Если вопрос явно про свежие цифры из нашей базы — скажи, что в базе "
            "сейчас нет релевантных материалов, и дай общий контекст без выдуманных "
            "«точных» котировок якобы из базы."
        )
        answer = chat_completion(
            system_prompt=effective_system,
            user_prompt=user_prompt,
            model=model or BRIEF_MODEL,
            temperature=0.35,
        )
        return {"answer": answer.strip(), "sources": []}

    context_parts: list[str] = []
    all_sources: list[dict[str, Any]] = []
    next_ref = 1
    if news_hits:
        context_parts.append(_format_hits(news_hits))
        all_sources.extend(_hits_to_sources(news_hits))
        next_ref = len(news_hits) + 1
    if attachment_hits:
        if context_parts:
            context_parts.append("")
        context_parts.append(_format_attachment_hits(attachment_hits, start_index=next_ref))
        all_sources.extend(
            _attachment_hits_to_sources(attachment_hits, start_index=next_ref)
        )

    context = "\n\n".join(part for part in context_parts if part)
    user_prompt = (
        (f"Предыдущий диалог:\n{history_block}\n\n" if history_block else "")
        + f"Вопрос пользователя: {question}\n\n"
        f"Фрагменты из внутренней базы (новости и документы):\n{context}\n\n"
        "Ответь на вопрос. Факты из базы сопровождай ссылками [n]. "
        "Можешь дополнять общими знаниями, но не приписывай базе то, чего нет во фрагментах."
    )
    answer = chat_completion(
        system_prompt=effective_system,
        user_prompt=user_prompt,
        model=model or BRIEF_MODEL,
        temperature=0.25,
    )
    sources = _sources_cited_in_answer(all_sources, answer.strip())
    return {"answer": answer.strip(), "sources": sources}
