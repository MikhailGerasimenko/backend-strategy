from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterable

from .models import compact_text


RU_MONTHS = {
    "января": "01",
    "февраля": "02",
    "марта": "03",
    "апреля": "04",
    "мая": "05",
    "июня": "06",
    "июля": "07",
    "августа": "08",
    "сентября": "09",
    "октября": "10",
    "ноября": "11",
    "декабря": "12",
}


DATE_FORMATS: tuple[str, ...] = (
    "%d.%m.%Y",
    "%Y-%m-%d",
    "%d %m %Y",
    "%d %B %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%d %b %Y %H:%M",
    "%d %B %Y %H:%M",
    "%b %d, %Y %H:%M",
)


def normalize_date(raw: str, formats: Iterable[str] = DATE_FORMATS) -> str:
    text = compact_text(raw)
    if not text:
        return ""

    if "T" in text and len(text) >= 10:
        text = text[:10]

    ru_normalized = _normalize_ru_month(text)
    if ru_normalized:
        text = ru_normalized

    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue

    try:
        return parsedate_to_datetime(text).strftime("%d.%m.%Y")
    except (TypeError, ValueError, IndexError, AttributeError):
        return compact_text(raw)


def _normalize_ru_month(text: str) -> str:
    lowered = text.lower()
    for month_name, month_number in RU_MONTHS.items():
        if month_name not in lowered:
            continue
        parts = lowered.replace(",", " ").split()
        for index, part in enumerate(parts):
            if part == month_name and index > 0 and parts[index - 1].isdigit():
                day = parts[index - 1].zfill(2)
                year = _first_year(parts[index + 1 :])
                if year:
                    return f"{day}.{month_number}.{year}"
    return ""


def _first_year(parts: list[str]) -> str:
    for part in parts:
        if part.isdigit() and len(part) == 4:
            return part
    return ""
