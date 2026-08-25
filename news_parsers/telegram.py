from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from .dates import normalize_date
from .http import FetchResult, HttpClient
from .models import NewsItem, compact_text

SourceConfig = dict[str, Any]

# t.me может быть недоступен в DNS (serverHold / NXDOMAIN); telegram.me — тот же виджет.
TELEGRAM_WEB_HOST = "telegram.me"


def telegram_public_url(path: str) -> str:
    return f"https://{TELEGRAM_WEB_HOST}/{path.lstrip('/')}"


def telegram_widget_url(source: SourceConfig) -> str:
    url = compact_text(source.get("url", ""))
    if url:
        normalized = url.replace("https://t.me/", f"https://{TELEGRAM_WEB_HOST}/")
        if "/s/" not in normalized:
            channel = normalized.rstrip("/").split("/")[-1]
            return telegram_public_url(f"s/{channel}")
        return normalized
    channel = compact_text(source.get("channel", ""))
    return telegram_public_url(f"s/{channel}")


def fetch_telegram(source: SourceConfig, client: HttpClient) -> list[NewsItem]:
    page_url = telegram_widget_url(source)
    result = client.get(page_url)
    if result.blocked:
        return [blocked_item(source, page_url, result)]
    if not result.ok:
        return [error_item(source, page_url, "network_error", result.error or str(result.status_code))]

    soup = BeautifulSoup(result.text, "html.parser")
    min_text = int(source.get("min_text_length", 30))
    max_posts = int(source.get("max_posts", 40))
    period_start = parse_period_boundary(source.get("period_start"))
    period_end = parse_period_boundary(source.get("period_end"))

    items: list[NewsItem] = []
    for message in soup.select(".tgme_widget_message"):
        post = parse_telegram_message(message, source, min_text, period_start, period_end)
        if not post:
            continue
        items.append(post)
        if len(items) >= max_posts:
            break

    return items


def parse_telegram_message(
    message: Any,
    source: SourceConfig,
    min_text: int,
    period_start: date | None,
    period_end: date | None,
) -> NewsItem | None:
    post_id = compact_text(message.get("data-post", ""))
    if not post_id:
        return None

    time_el = message.select_one(".tgme_widget_message_date time")
    if not time_el or not time_el.get("datetime"):
        return None

    post_dt = parse_telegram_datetime(time_el["datetime"])
    if not post_dt:
        return None
    post_date = post_dt.date()
    if period_start and post_date < period_start:
        return None
    if period_end and post_date > period_end:
        return None

    text_el = message.select_one(".tgme_widget_message_text")
    if not text_el:
        return None
    text = text_el.get_text(separator=" ", strip=True)
    if len(text) < min_text:
        return None

    title = text[:150] + ("…" if len(text) > 150 else "")
    return NewsItem(
        source=source.get("name", "unknown"),
        category=source.get("category", "telegram"),
        title=title,
        date=normalize_date(post_dt.isoformat()),
        url=telegram_public_url(post_id),
        summary=text[:500],
        content=text[:5000],
        language="ru",
    )


def parse_telegram_datetime(raw: str) -> datetime | None:
    value = compact_text(raw)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def parse_period_boundary(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def blocked_item(source: SourceConfig, url: str, result: FetchResult) -> NewsItem:
    return NewsItem(
        source=source.get("name", "unknown"),
        category=source.get("category", "telegram"),
        title="",
        date="",
        url=url,
        status="blocked",
        error=f"Blocked or rate limited. HTTP status: {result.status_code or 'NA'}",
    )


def error_item(source: SourceConfig, url: str, status: str, error: str) -> NewsItem:
    return NewsItem(
        source=source.get("name", "unknown"),
        category=source.get("category", "telegram"),
        title="",
        date="",
        url=url,
        status=status,
        error=error,
    )
