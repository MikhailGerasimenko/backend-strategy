"""Структурированный JSON ежемесячного брифа — заготовка под генерацию презентаций."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .docx_brief import period_brief_display_name, period_brief_json_filename
from .llm.period_brief import is_monthly_brief_kind

MONTHLY_BRIEF_JSON_SCHEMA = "monthly_brief_v1"

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")
_NEWS_ITEM_RE = re.compile(r"^(\d+)\.\s+(.*)$")
_BULLET_RE = re.compile(r"^[-•*—–]\s+(.+)$")
_SECTION_NUM_RE = re.compile(r"^(\d+)[.)]\s+(.+)$")

_SLIDE_TITLES = {"варианты заголовка слайда", "заголовки слайда", "варианты заголовка"}
_SLIDE_BULLETS = {"буллит-пойнты для слайда", "буллит-пойнты", "буллиты для слайда"}
_SPEAKER = {"комментарий для докладчика", "комментарий докладчика", "заметки для докладчика"}
_DYNAMICS = {"динамика за месяц"}
_FORECAST = {
    "прогноз на 4 месяца",
    "прогноз на ближайшие 3–4 месяца",
    "прогноз на ближайшие 3-4 месяца",
    "прогноз на 3–4 месяца",
    "прогноз на 3-4 месяца",
}


def export_monthly_brief_json(
    content: str,
    *,
    period_start: date,
    period_end: date,
    brief_kind: str,
    metadata: dict[str, Any] | None = None,
    output_dir: Path,
) -> dict[str, Any]:
    """Разобрать текст месячного брифа, записать JSON и вернуть метаданные файла."""
    text = (content or "").strip()
    if len(text) < 50:
        raise ValueError("Текст брифа слишком короткий для экспорта в JSON.")
    kind = (brief_kind or "monthly").strip() or "monthly"
    if not is_monthly_brief_kind(kind):
        raise ValueError("JSON-экспорт доступен для ежемесячных брифов.")

    payload = parse_monthly_brief(
        text,
        brief_kind=kind,
        period_start=period_start,
        period_end=period_end,
        metadata=metadata,
    )
    filename = period_brief_json_filename(period_start, period_end, kind)
    path = output_dir / filename
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "json_path": str(path),
        "json_filename": path.name,
        "schema": MONTHLY_BRIEF_JSON_SCHEMA,
        "slides": len(payload.get("slides") or []),
        "brief_kind": kind,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "content_chars": len(text),
    }


def parse_monthly_brief(
    content: str,
    *,
    brief_kind: str,
    period_start: date,
    period_end: date,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Превращает markdown месячного брифа в слайды для презентации."""
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    chunks = _split_heading_chunks(text)
    title = _document_title(chunks, brief_kind, period_start, period_end)
    summary = ""
    summary_bullets: list[str] = []
    slides: list[dict[str, Any]] = []
    current_part = ""

    for chunk in chunks:
        heading = chunk["title"]
        level = chunk["level"]
        body = chunk["body"]
        if _is_document_title(heading):
            if body.strip():
                parsed = _parse_section_body(body)
                if parsed["paragraphs"] and not summary:
                    summary = "\n\n".join(parsed["paragraphs"])
            continue
        if _is_part_heading(heading, level):
            current_part = heading
            continue
        if _is_summary_heading(heading):
            parsed = _parse_section_body(body)
            summary_bullets = parsed["bullets"] or parsed.get("slide_bullets") or []
            if parsed["paragraphs"]:
                summary = "\n\n".join(parsed["paragraphs"])
            elif parsed["speaker_notes"]:
                summary = parsed["speaker_notes"]
            continue

        slides.append(_chunk_to_slide(chunk, part=current_part))

    return {
        "schema": MONTHLY_BRIEF_JSON_SCHEMA,
        "brief_kind": brief_kind,
        "title": title,
        "period": {
            "start": period_start.isoformat(),
            "end": period_end.isoformat(),
            "label": _period_label(period_start, period_end),
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "display_name": period_brief_display_name(period_start, period_end, brief_kind),
        "summary": summary,
        "summary_bullets": summary_bullets,
        "slides": slides,
        "source": _source_meta(metadata),
        "raw_markdown": text,
    }


def _source_meta(metadata: dict[str, Any] | None) -> dict[str, Any]:
    meta = metadata or {}
    keys = (
        "news_documents",
        "map_batches",
        "model",
        "prompt_version",
        "generated_at",
    )
    return {key: meta[key] for key in keys if key in meta}


def _period_label(period_start: date, period_end: date) -> str:
    months = (
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    )
    if period_start.month == period_end.month and period_start.year == period_end.year:
        return f"{months[period_end.month - 1]} {period_end.year}"
    return f"{period_start.strftime('%d.%m.%Y')} — {period_end.strftime('%d.%m.%Y')}"


def _split_heading_chunks(text: str) -> list[dict[str, Any]]:
    lines = text.split("\n")
    chunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    preamble: list[str] = []

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        current["body"] = "\n".join(current["body_lines"]).strip()
        del current["body_lines"]
        chunks.append(current)
        current = None

    for line in lines:
        match = _HEADING_RE.match(line)
        if match:
            flush()
            current = {
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
                "body_lines": [],
            }
            continue
        if current is None:
            preamble.append(line)
        else:
            current["body_lines"].append(line)
    flush()
    if preamble and any(part.strip() for part in preamble) and chunks:
        extra = "\n".join(preamble).strip()
        if extra:
            chunks[0]["body"] = f"{extra}\n\n{chunks[0]['body']}".strip()
    elif preamble and not chunks:
        chunks.append({"level": 2, "title": "", "body": "\n".join(preamble).strip()})
    return chunks


def _document_title(
    chunks: list[dict[str, Any]],
    brief_kind: str,
    period_start: date,
    period_end: date,
) -> str:
    for chunk in chunks:
        if _is_document_title(chunk["title"]):
            return chunk["title"]
    return period_brief_display_name(period_start, period_end, brief_kind)


def _is_document_title(title: str) -> bool:
    low = title.lower()
    return "ежемесячный" in low and "бриф" in low


def _is_part_heading(title: str, level: int) -> bool:
    return title.upper().startswith("ЧАСТЬ") or (level == 1 and title.upper().startswith("PART"))


def _is_summary_heading(title: str) -> bool:
    low = re.sub(r"^#+\s*", "", title).strip().lower()
    low = re.sub(r"^\d+[.)]\s*", "", low)
    return low.startswith("резюме")


def _normalize_label(text: str) -> str:
    cleaned = re.sub(r"[*#_]+", "", text or "").strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.rstrip(":")


def _label_kind(label: str) -> str | None:
    key = _normalize_label(label)
    if key in _SLIDE_TITLES:
        return "title_options"
    if key in _SLIDE_BULLETS:
        return "slide_bullets"
    if key in _SPEAKER:
        return "speaker_notes"
    if key in _DYNAMICS:
        return "dynamics"
    if key in _FORECAST:
        return "forecast"
    return None


def _parse_section_body(body: str) -> dict[str, Any]:
    title_options: list[str] = []
    slide_bullets: list[str] = []
    speaker_notes: list[str] = []
    dynamics: list[str] = []
    forecast: list[str] = []
    bullets: list[str] = []
    paragraphs: list[str] = []
    blocks: list[dict[str, Any]] = []
    news_items: list[dict[str, Any]] = []

    mode = "body"
    current_block: dict[str, Any] | None = None
    current_news: dict[str, Any] | None = None
    para_buf: list[str] = []

    def flush_para() -> None:
        nonlocal para_buf
        text = " ".join(part.strip() for part in para_buf if part.strip()).strip()
        para_buf = []
        if not text:
            return
        if mode == "speaker_notes":
            speaker_notes.append(text)
        elif current_news is not None and not current_news.get("summary"):
            current_news["summary"] = text
        elif current_block is not None:
            current_block.setdefault("paragraphs", []).append(text)
        elif mode == "body":
            paragraphs.append(text)

    def flush_news() -> None:
        nonlocal current_news
        if current_news is None:
            return
        if current_news.get("headline") or current_news.get("summary") or current_news.get("bullets"):
            news_items.append(current_news)
        current_news = None

    def start_block(title: str, kind: str) -> None:
        nonlocal current_block
        flush_para()
        current_block = {"kind": kind, "title": title, "bullets": [], "paragraphs": []}
        blocks.append(current_block)

    def add_bullet(text: str) -> None:
        item = text.strip()
        if not item:
            return
        if mode == "title_options":
            title_options.append(item)
        elif mode == "slide_bullets":
            slide_bullets.append(item)
        elif mode == "dynamics":
            dynamics.append(item)
        elif mode == "forecast":
            forecast.append(item)
        elif current_news is not None:
            current_news.setdefault("bullets", []).append(item)
        elif current_block is not None:
            current_block.setdefault("bullets", []).append(item)
        else:
            bullets.append(item)

    for raw in (body or "").split("\n"):
        line = raw.strip()
        if not line:
            flush_para()
            continue

        bold = _BOLD_LINE_RE.match(line)
        if bold:
            flush_para()
            flush_news()
            label = bold.group(1).strip()
            kind = _label_kind(label)
            if kind:
                mode = kind
                current_block = None
                if kind in {"dynamics", "forecast"}:
                    start_block(label, kind)
                    mode = kind
            else:
                mode = "body"
                start_block(label, "metric")
            continue

        news = _NEWS_ITEM_RE.match(line)
        if news and (mode in {"body", "slide_bullets"} or current_news is not None):
            flush_para()
            flush_news()
            mode = "body"
            current_block = None
            rest = news.group(2).strip()
            headline, leftover = _split_headline(rest)
            current_news = {
                "index": int(news.group(1)),
                "headline": headline,
                "summary": leftover,
                "bullets": [],
            }
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            flush_para()
            add_bullet(bullet.group(1).strip())
            continue
        if line[:1] in {"↑", "↓", "="} and len(line) > 1:
            flush_para()
            add_bullet(line)
            continue

        para_buf.append(line)

    flush_para()
    flush_news()

    cleaned_blocks = []
    for block in blocks:
        item = {
            "kind": block["kind"],
            "title": block["title"],
            "bullets": block.get("bullets") or [],
            "text": "\n\n".join(block.get("paragraphs") or []).strip(),
        }
        if item["bullets"] or item["text"]:
            cleaned_blocks.append(item)

    return {
        "title_options": title_options,
        "slide_bullets": slide_bullets,
        "speaker_notes": "\n\n".join(speaker_notes).strip(),
        "dynamics": dynamics,
        "forecast": forecast,
        "bullets": bullets,
        "paragraphs": paragraphs,
        "blocks": cleaned_blocks,
        "news_items": news_items,
    }


def _split_headline(rest: str) -> tuple[str, str]:
    match = re.match(r"\*\*(.+?)\*\*\s*(.*)$", rest)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return rest.strip(), ""


def _chunk_to_slide(chunk: dict[str, Any], *, part: str) -> dict[str, Any]:
    heading = chunk["title"]
    parsed = _parse_section_body(chunk["body"])
    number = None
    title = heading
    numbered = _SECTION_NUM_RE.match(heading)
    if numbered:
        number = int(numbered.group(1))
        title = numbered.group(2).strip()

    bullets = parsed["slide_bullets"] or parsed["bullets"]
    if not bullets and parsed["blocks"]:
        bullets = [
            item
            for block in parsed["blocks"]
            for item in (block.get("bullets") or [])
        ]
    if not bullets and parsed["news_items"]:
        bullets = [
            item["headline"] or item["summary"]
            for item in parsed["news_items"]
            if item.get("headline") or item.get("summary")
        ]
    preferred = (parsed["title_options"][0] if parsed["title_options"] else title)

    return {
        "id": str(number) if number is not None else _slug(title),
        "number": number,
        "part": part,
        "section_title": title,
        "heading": heading,
        "title_options": parsed["title_options"],
        "preferred_title": preferred,
        "bullets": bullets,
        "speaker_notes": parsed["speaker_notes"],
        "dynamics": parsed["dynamics"],
        "forecast": parsed["forecast"],
        "blocks": parsed["blocks"],
        "news_items": parsed["news_items"],
        "text": "\n\n".join(parsed["paragraphs"]).strip(),
    }


def _slug(title: str) -> str:
    compact = re.sub(r"[^\wа-яА-ЯёЁ]+", "-", title, flags=re.UNICODE).strip("-").lower()
    return compact[:48] or "section"
