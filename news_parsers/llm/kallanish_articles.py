"""Разбор выпуска Kallanish на отдельные статьи для индексации как новостей."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

from ..document_text import extract_document_text
from .kallanish_docx import extract_docx_plain_text

KALLANISH_SOURCE = "Kallanish"

# Маркер границы статьи: RU «09 сен 2026» или EN «25 Jun 2026»
_DATE_LINE_RE = re.compile(
    r"^\d{1,2}\s+"
    r"(?:"
    r"янв(?:аря)?|фев(?:раля)?|мар(?:та)?|апр(?:еля)?|ма[йя]|июн(?:я)?|"
    r"июл(?:я)?|авг(?:уста)?|сен(?:тября)?|окт(?:ября)?|ноя(?:бря)?|дек(?:абря)?|"
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
    r")"
    r"\s+20\d{2}$",
    re.IGNORECASE,
)

_MONTHS_RU = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}

_MONTHS_EN = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_AUTHOR_RE = re.compile(r"https?://www\.kallanish\.com/en/authors/", re.IGNORECASE)
_NOISE_RE = re.compile(
    r"^(News for |<https?://|Disclaim|Copyright|Unsubscribe)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KallanishArticle:
    title: str
    body: str
    news_date: date
    author: str = ""


def parse_kallanish_date_line(value: str, *, fallback: date | None = None) -> date | None:
    raw = (value or "").strip()
    if not _DATE_LINE_RE.match(raw):
        return fallback
    parts = raw.split()
    if len(parts) < 3:
        return fallback
    day_s, month_s, year_s = parts[0], parts[1], parts[2]
    month_key = month_s.lower()[:3]
    month = _MONTHS_RU.get(month_key) or _MONTHS_EN.get(month_key)
    if not month:
        return fallback
    try:
        return date(int(year_s), month, int(day_s))
    except ValueError:
        return fallback


def _is_noise(line: str) -> bool:
    return bool(_NOISE_RE.search(line.strip()))


def _is_author_line(line: str) -> bool:
    return bool(_AUTHOR_RE.search(line))


def split_kallanish_articles(
    text: str,
    *,
    fallback_date: date | None = None,
) -> list[KallanishArticle]:
    """Режет выпуск по строкам-датам: заголовок → текст → автор → дата следующей."""
    lines = [part.strip() for part in re.split(r"\n+", text or "") if part.strip()]
    if not lines:
        return []

    date_indexes = [i for i, line in enumerate(lines) if _DATE_LINE_RE.match(line)]
    if not date_indexes:
        # Нет маркеров даты — один документ целиком (например, .txt котировки).
        body_lines = [line for line in lines if not _is_noise(line)]
        if not body_lines:
            return []
        title = body_lines[0][:180]
        body = "\n\n".join(body_lines)
        return [
            KallanishArticle(
                title=title,
                body=body,
                news_date=fallback_date or date.today(),
            )
        ]

    articles: list[KallanishArticle] = []
    # Блоки: до первой даты, затем между датами, затем хвост после последней даты.
    boundaries = [-1, *date_indexes, len(lines)]
    for start_i, end_i in zip(boundaries, boundaries[1:]):
        # Строка даты в начале интервала — маркер следующей статьи, в тело не берём.
        chunk_start = start_i + 1
        chunk_lines = lines[chunk_start:end_i]
        content = [line for line in chunk_lines if not _is_noise(line)]
        if not content:
            continue

        # Дата стоит ПОСЛЕ статьи (автор → дата → следующий заголовок).
        article_date = fallback_date
        if end_i < len(lines) and _DATE_LINE_RE.match(lines[end_i]):
            article_date = parse_kallanish_date_line(lines[end_i], fallback=fallback_date)
        elif start_i >= 0 and _DATE_LINE_RE.match(lines[start_i]):
            article_date = parse_kallanish_date_line(lines[start_i], fallback=fallback_date)

        author = ""
        if content and _is_author_line(content[-1]):
            author = content[-1]
            body_lines = content[:-1]
        else:
            body_lines = content
        if not body_lines:
            continue

        title = body_lines[0][:220]
        body = "\n\n".join(body_lines)
        if author:
            body = f"{body}\n\n{author}"

        articles.append(
            KallanishArticle(
                title=title,
                body=body,
                news_date=article_date or fallback_date or date.today(),
                author=author,
            )
        )

    return articles


def articles_from_path(
    path: Path,
    *,
    fallback_date: date | None = None,
) -> list[KallanishArticle]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        text = extract_docx_plain_text(path)
    else:
        text = extract_document_text(path)
    return split_kallanish_articles(text, fallback_date=fallback_date)


def _slug(title: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", title, flags=re.IGNORECASE)
    cleaned = cleaned.strip("-").lower()
    return (cleaned or "article")[:80]


def article_to_news_item(article: KallanishArticle, *, filename: str = "") -> dict:
    digest = hashlib.sha1(f"{article.title}\n{article.body}".encode("utf-8")).hexdigest()[:12]
    url = f"kallanish://article/{article.news_date.isoformat()}/{_slug(article.title)}-{digest}"
    summary = article.body[:400]
    return {
        "status": "ok",
        "source": KALLANISH_SOURCE,
        "category": "steel_news",
        "title": article.title,
        "date": article.news_date.isoformat(),
        "url": url,
        "content": article.body,
        "summary": summary,
        "language": "en",
        "keyword_block": "kallanish",
        "keyword_match": filename or "kallanish",
    }


def kallanish_articles_to_news_items(
    articles: Sequence[KallanishArticle],
    *,
    filename: str = "",
) -> list[dict]:
    return [article_to_news_item(article, filename=filename) for article in articles]
