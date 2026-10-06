"""ИИ-агент по новостям: вопрос → семантический поиск → ответ со ссылками.

Поддерживает «дай новость целиком»: для обычных новостей — полный текст,
для Kallanish/PDF — читаемый фрагмент вокруг процитированного куска.
"""

from __future__ import annotations

from html import unescape
import json
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from ..rag.store import _join_chunk_texts
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
# «TG»/«@» только как отдельный токен: иначе «mortgage» давал бы фильтр по источнику «age».
_TG_SOURCE_RE = re.compile(r"(?<![A-Za-z0-9_])(?:TG|tg|@)\s*([A-Za-z0-9_]{3,})")
_CITE_RE = re.compile(r"\[(\d{1,3})\]")

# Бюджет контекста для одного запроса к модели (символы). Без него агент
# подтягивал ВСЕ чанки 12 документов Kallanish — сотни тысяч символов.
_MAX_CONTEXT_CHARS = 160_000
_MAX_DOC_CHARS = 40_000
_MAX_HISTORY_ITEM_CHARS = 1_500
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


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
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


@dataclass
class _DocContext:
    """Один документ = одна ссылка [n] в контексте и в списке источников."""

    kind: str  # news | attachment
    document_id: int
    title: str
    source: str
    doc_date: date
    url: str
    score: float
    text: str
    anchor_chunk: int | None = None
    ref: int = 0

    def header(self) -> str:
        label = "источник" if self.kind == "news" else "документ"
        return f"[{self.ref}] ({self.doc_date.isoformat()}, {label}: {self.source or '—'}) {self.title}"

    def to_source(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "ref": self.ref,
            "kind": self.kind,
            "document_id": self.document_id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "news_date": self.doc_date.isoformat(),
            "score": round(float(self.score), 3),
        }
        if self.kind == "attachment":
            item["chunk_index"] = self.anchor_chunk
        return item


def _clip(text: str, limit: int) -> str:
    compact = (text or "").strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _section_around(
    chunks: dict[int, str], anchor: int | None, *, radius: int, limit: int
) -> str:
    """Фрагмент документа вокруг найденного чанка — когда весь документ не влезает."""
    if not chunks:
        return ""
    ordered = sorted(chunks)
    if anchor is None or anchor not in chunks:
        selected = ordered[: radius + 2]
    else:
        selected = [i for i in ordered if anchor - radius <= i <= anchor + radius]
    return _clip(_join_chunk_texts([chunks[i] for i in selected]), limit)


def _group_news_docs(
    seed: list[NewsSearchHit], all_chunks: list[NewsSearchHit]
) -> list[_DocContext]:
    """Документы в порядке лучшего score сида; текст — все чанки, склеенные без дублей."""
    chunks_by_doc: dict[int, dict[int, str]] = defaultdict(dict)
    for hit in all_chunks:
        chunks_by_doc[hit.document_id][hit.chunk_index] = hit.content
    best_seed: dict[int, NewsSearchHit] = {}
    for hit in seed:
        prev = best_seed.get(hit.document_id)
        if prev is None or hit.score > prev.score:
            best_seed[hit.document_id] = hit
    docs: list[_DocContext] = []
    for doc_id in _ranked_document_ids(seed):
        hit = best_seed[doc_id]
        chunks = chunks_by_doc.get(doc_id) or {hit.chunk_index: hit.content}
        text = _join_chunk_texts([chunks[i] for i in sorted(chunks)])
        if len(text) > _MAX_DOC_CHARS:
            text = _section_around(chunks, hit.chunk_index, radius=6, limit=_MAX_DOC_CHARS)
        docs.append(
            _DocContext(
                kind="news",
                document_id=doc_id,
                title=hit.title,
                source=hit.source,
                doc_date=hit.news_date,
                url=hit.url,
                score=hit.score,
                text=text,
                anchor_chunk=hit.chunk_index,
            )
        )
    return docs


def _group_attachment_docs(
    seed: list[AttachmentSearchHit], all_chunks: list[AttachmentSearchHit]
) -> list[_DocContext]:
    chunks_by_doc: dict[int, dict[int, str]] = defaultdict(dict)
    for hit in all_chunks:
        chunks_by_doc[hit.document_id][hit.chunk_index] = hit.content
    best_seed: dict[int, AttachmentSearchHit] = {}
    for hit in seed:
        prev = best_seed.get(hit.document_id)
        if prev is None or hit.score > prev.score:
            best_seed[hit.document_id] = hit
    docs: list[_DocContext] = []
    for doc_id in _ranked_document_ids(seed):
        hit = best_seed[doc_id]
        chunks = chunks_by_doc.get(doc_id) or {hit.chunk_index: hit.content}
        text = _join_chunk_texts([chunks[i] for i in sorted(chunks)])
        if len(text) > _MAX_DOC_CHARS:
            # Многостраничный Kallanish/PDF: берём окрестность найденного места.
            text = _section_around(chunks, hit.chunk_index, radius=6, limit=_MAX_DOC_CHARS)
        docs.append(
            _DocContext(
                kind="attachment",
                document_id=doc_id,
                title=hit.title,
                source=hit.document_type,
                doc_date=hit.brief_date,
                url="",
                score=hit.score,
                text=text,
                anchor_chunk=hit.chunk_index,
            )
        )
    return docs


def _fit_context_budget(
    docs: list[_DocContext], *, budget: int = _MAX_CONTEXT_CHARS
) -> list[_DocContext]:
    """Укладывает документы в бюджет символов: сначала по рангу целиком,
    последний ужимается до остатка, дальше — отбрасываются."""
    fitted: list[_DocContext] = []
    used = 0
    for doc in docs:
        remaining = budget - used
        if remaining < 2_000:
            break
        if len(doc.text) > remaining:
            doc = replace(doc, text=_clip(doc.text, remaining))
        fitted.append(doc)
        used += len(doc.text)
    return fitted


def _assign_refs(docs: list[_DocContext], *, start: int = 1) -> list[_DocContext]:
    return [replace(doc, ref=start + offset) for offset, doc in enumerate(docs)]


def _format_docs(docs: list[_DocContext]) -> str:
    return "\n\n".join(f"{doc.header()}\n{doc.text}" for doc in docs if doc.text)


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
        return "Kallanish"
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
    if len(result) < top_k:
        # Лимит «≤2 на источник» не должен резать выдачу, если релевантных
        # источников мало (например, всё по теме — только Kallanish).
        chosen = {(h.document_id, h.chunk_index) for h in result}
        for hit in hits:
            if len(result) >= top_k:
                break
            if (hit.document_id, hit.chunk_index) not in chosen:
                result.append(hit)
                chosen.add((hit.document_id, hit.chunk_index))
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
        lines.append(f"{label}: {_clip(content, _MAX_HISTORY_ITEM_CHARS)}")
    return "\n".join(lines)


def _truncate_doc_text(text: str) -> str:
    compact = (text or "").strip()
    if len(compact) <= _MAX_FOLLOW_UP_DOC_CHARS:
        return compact
    return compact[: _MAX_FOLLOW_UP_DOC_CHARS - 1].rstrip() + "…"


def _load_prior_sources_context(
    database_url: str, prior_sources: list[dict[str, Any]]
) -> list[_DocContext]:
    """Документы из прошлого ответа (с их же номерами [n]) — для уточняющих вопросов."""
    docs: list[_DocContext] = []
    seen: set[tuple[str, int]] = set()
    for index, src in enumerate(prior_sources, start=1):
        kind = str(src.get("kind") or "news")
        try:
            document_id = int(src.get("document_id") or 0)
        except (TypeError, ValueError):
            continue
        if not document_id or (kind, document_id) in seen:
            continue
        seen.add((kind, document_id))
        try:
            orig_ref = int(src.get("ref") or index)
        except (TypeError, ValueError):
            orig_ref = index
        try:
            score = float(src.get("score") or 1.0)
        except (TypeError, ValueError):
            score = 1.0
        if kind == "attachment":
            anchor = _chunk_index_from_source(src)
            doc, section = get_attachment_section(
                database_url,
                document_id,
                anchor,
                radius=3,
                max_chars=_MAX_FOLLOW_UP_DOC_CHARS,
            )
            if not doc:
                continue
            docs.append(
                _DocContext(
                    kind="attachment",
                    document_id=doc.id,
                    title=doc.title,
                    source=doc.document_type,
                    doc_date=doc.brief_date,
                    url="",
                    score=score,
                    text=format_document_text_for_display(_truncate_doc_text(section)),
                    anchor_chunk=anchor,
                    ref=orig_ref,
                )
            )
            continue
        doc = get_news_document(database_url, document_id)
        if not doc:
            continue
        docs.append(
            _DocContext(
                kind="news",
                document_id=doc.id,
                title=doc.title,
                source=doc.source,
                doc_date=doc.news_date,
                url=doc.url,
                score=score,
                text=_truncate_doc_text(doc.full_text),
                ref=orig_ref,
            )
        )
    return docs


def _compose_search_query(
    question: str,
    history: list[dict[str, Any]] | None,
) -> str:
    """Подмешивает прошлый вопрос в поисковый запрос только для уточнений.

    Раньше это делалось всегда — новый вопрос на другую тему искался вместе
    со старым, и embeddings тянули нерелевантные документы.
    """
    if not history:
        return question
    words = [w for w in re.split(r"\s+", (question or "").strip()) if w]
    if not (_is_follow_up_question(question) or len(words) <= 3):
        return question
    for item in reversed(history):
        if str(item.get("role", "")) == "user":
            prev = str(item.get("content", "")).strip()
            if prev and prev.lower() != question.lower().strip():
                return f"{_clip(prev, 400)}\n{question}"
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
    # «Полностью согласен, а что по ценам в Китае?» — не запрос полного текста:
    # без номера считаем таковым только короткую реплику.
    full_text_request = _asks_full_text(question) and (
        ref is not None or len((question or "").strip()) <= 80
    )
    if use_rag and full_text_request and prior_sources:
        match = next(
            (s for s in prior_sources if ref is not None and _safe_int(s.get("ref")) == ref),
            None,
        )
        if match is None and ref is not None:
            available = ", ".join(
                f"[{_safe_int(s.get('ref'))}]" for s in prior_sources if _safe_int(s.get("ref"))
            )
            return {
                "answer": (
                    f"В предыдущем ответе нет источника [{ref}]. "
                    f"Доступны: {available or '—'}. Укажите номер из этого списка."
                ),
                "sources": prior_sources,
            }
        if match is None:
            match = prior_sources[0]
            ref = _safe_int(match.get("ref")) or 1
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

    prior_docs: list[_DocContext] = []
    if follow_up and prior_sources:
        prior_docs = _load_prior_sources_context(database_url, prior_sources)

    # Свежий поиск делаем всегда — и для уточняющего вопроса тоже: раньше
    # follow-up отвечал только по старым источникам, даже если тема сменилась.
    search_query = _expand_search_query(_compose_search_query(question, history))
    search_start, search_end = _resolve_search_period(question, period_start, period_end)
    source_filter = _extract_source_filter(question)
    document_type_filter = _extract_document_type_filter(question)
    attachment_focused = _is_attachment_focused_query(question) or bool(document_type_filter)
    narrow = _is_narrow_source_query(question, source_filter)
    diverse = not narrow and not source_filter and not attachment_focused
    # Семантика только находит релевантные ДОКУМЕНТЫ; дальше подтягиваем их чанки
    # (в пределах бюджета контекста).
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
    news_docs = _group_news_docs(
        news_seed, _expand_news_hits_to_all_chunks(database_url, news_seed)
    )

    attachment_seed = search_attachment_chunks(
        database_url,
        query_text=search_query,
        period_start=search_start,
        period_end=search_end,
        document_type_contains=document_type_filter,
        top_k=attachment_seed_k,
        candidate_limit=attachment_seed_k * 4,
    )
    attachment_docs = _group_attachment_docs(
        attachment_seed, _expand_attachment_hits_to_all_chunks(database_url, attachment_seed)
    )

    # Один документ — одна ссылка [n]. Источники прошлого ответа сохраняют свои
    # номера, новые документы нумеруются дальше.
    known = {(d.kind, d.document_id) for d in prior_docs}
    fresh: list[_DocContext] = []
    for doc in news_docs + attachment_docs:
        key = (doc.kind, doc.document_id)
        if key in known:
            continue
        known.add(key)
        fresh.append(doc)
    fresh.sort(key=lambda d: -d.score)

    prior_docs = _fit_context_budget(prior_docs, budget=_MAX_CONTEXT_CHARS // 2)
    fresh = _fit_context_budget(
        fresh, budget=_MAX_CONTEXT_CHARS - sum(len(d.text) for d in prior_docs)
    )
    next_ref = max([d.ref for d in prior_docs], default=0) + 1
    fresh = _assign_refs(fresh, start=next_ref)
    docs = prior_docs + fresh

    if not docs:
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

    context = _format_docs(docs)
    all_sources = [doc.to_source() for doc in docs]
    if prior_docs:
        task = (
            "Пользователь уточняет предыдущий ответ. Ниже — материалы из базы: "
            "источники прошлого ответа (с прежними номерами) и новые находки по теме. "
            "Используй их для фактов со ссылками [n]; если нужно — дополни общими "
            "знаниями, явно разделяя источники. Дай развёрнутый ответ."
        )
        label = "Новый вопрос пользователя"
        temperature = 0.2
    else:
        task = (
            "Ответь на вопрос. Факты из базы сопровождай ссылками [n]. "
            "Можешь дополнять общими знаниями, но не приписывай базе то, чего нет во фрагментах."
        )
        label = "Вопрос пользователя"
        temperature = 0.25
    user_prompt = (
        (f"Предыдущий диалог:\n{history_block}\n\n" if history_block else "")
        + f"{label}: {question}\n\n"
        f"Материалы из внутренней базы (новости и документы):\n{context}\n\n"
        f"{task}"
    )
    answer = chat_completion(
        system_prompt=effective_system,
        user_prompt=user_prompt,
        model=model or BRIEF_MODEL,
        temperature=temperature,
    ).strip()
    return {"answer": answer, "sources": _sources_cited_in_answer(all_sources, answer)}
