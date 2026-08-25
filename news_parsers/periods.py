from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .models import NewsItem


PERIOD_DAYS = {
    "day": 1,
    "week": 7,
    "month": 30,
}


@dataclass(frozen=True)
class PeriodRange:
    name: str
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, value: date) -> bool:
        return self.start <= value <= self.end


def build_period_range(
    period: str = "day",
    since: str | None = None,
    until: str | None = None,
    days: int | None = None,
) -> PeriodRange:
    end = parse_iso_date(until) if until else date.today()
    if since:
        start = parse_iso_date(since)
        name = "custom"
    elif days:
        if days < 1:
            raise ValueError("--days must be a positive integer")
        start = end - timedelta(days=days - 1)
        name = f"{days}d"
    else:
        days = PERIOD_DAYS[period]
        start = end - timedelta(days=days - 1)
        name = period
    if start > end:
        raise ValueError("--since must be earlier than or equal to --until")
    return PeriodRange(name=name, start=start, end=end)


def apply_period_to_source(
    source: dict,
    period_range: PeriodRange,
    smoke: bool = False,
    max_pages: int | None = None,
) -> dict:
    adjusted = dict(source)
    adjusted["period_start"] = period_range.start.isoformat()
    adjusted["period_end"] = period_range.end.isoformat()

    if "days_back" in adjusted:
        adjusted["days_back"] = period_range.days

    if "max_pages" in adjusted:
        base_pages = int(adjusted["max_pages"])
        adjusted["max_pages"] = pages_for_period(base_pages, period_range)
        if max_pages:
            adjusted["max_pages"] = min(int(adjusted["max_pages"]), max_pages)

    if adjusted.get("parser") == "generic_html":
        adjusted["max_items"] = items_for_period(period_range)

    if adjusted.get("parser") == "telegram":
        adjusted["max_posts"] = telegram_posts_for_period(period_range)

    if smoke:
        if "max_pages" in adjusted:
            adjusted["max_pages"] = min(int(adjusted["max_pages"]), 1)
        if "days_back" in adjusted:
            adjusted["days_back"] = min(int(adjusted["days_back"]), 2)
        adjusted["max_items"] = min(int(adjusted.get("max_items", 5)), 5)
        if adjusted.get("parser") == "telegram":
            adjusted["max_posts"] = 5
            adjusted["min_text_length"] = 20

    return adjusted


def filter_items_by_period(items: list[NewsItem], period_range: PeriodRange) -> list[NewsItem]:
    filtered: list[NewsItem] = []
    for item in items:
        if item.status != "ok":
            filtered.append(item)
            continue
        item_date = parse_item_date(item.date)
        if item_date is None or period_range.contains(item_date):
            filtered.append(item)
    return filtered


def parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_item_date(value: str) -> date | None:
    if not value:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def pages_for_period(base_pages: int, period_range: PeriodRange) -> int:
    if period_range.days <= 1:
        return 1
    if period_range.days <= 7:
        return base_pages
    return max(base_pages, min(base_pages * 3, 12))


def items_for_period(period_range: PeriodRange) -> int:
    if period_range.days <= 1:
        return 10
    if period_range.days <= 7:
        return 40
    return 120


def telegram_posts_for_period(period_range: PeriodRange) -> int:
    if period_range.days <= 1:
        return 25
    if period_range.days <= 7:
        return 40
    return 60
