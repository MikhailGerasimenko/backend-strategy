"""Извлечение ПОЛНОГО текста новости по URL (для RAG-индексации сырых новостей).

Стратегия «без потерь»:
1. Telegram (t.me) — текст поста уже полный в выгрузке парсера, повторно не качаем.
2. Иначе качаем HTML (HttpClient, при блокировке — headless-браузер) и извлекаем
   основной текст через trafilatura; фолбэк — BeautifulSoup-селекторы; затем —
   уже сохранённый при парсинге content.
Возвращаем самый длинный валидный вариант, чтобы не потерять текст.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .html_utils import extract_article_meta, soup_from_html
from .http import HttpClient
from .models import compact_text

MIN_USEFUL_LEN = 200


def is_telegram_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in {"t.me", "telegram.me", "telegram.org"} or host.endswith(".t.me")


def extract_full_text_from_html(html: str, url: str = "") -> str:
    """Главный текст страницы: trafilatura → BeautifulSoup-фолбэк."""
    text = ""
    try:
        import trafilatura

        text = (
            trafilatura.extract(
                html,
                url=url or None,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            or ""
        )
    except Exception:
        text = ""

    text = (text or "").strip()
    if len(text) >= MIN_USEFUL_LEN:
        return text

    try:
        meta = extract_article_meta(soup_from_html(html))
        fallback = compact_text(meta.get("content", ""))
    except Exception:
        fallback = ""
    return fallback if len(fallback) > len(text) else text


def fetch_full_article_text(
    url: str,
    client: HttpClient,
    *,
    browser_fetcher: Any = None,
    parsed_content: str = "",
) -> tuple[str, str]:
    """Возвращает (текст, статус). Статус: telegram | fetched | parsed | failed.

    parsed_content — текст, полученный при первичном парсинге (фолбэк).
    """
    parsed_content = (parsed_content or "").strip()

    if not url or is_telegram_url(url):
        return parsed_content, "telegram" if url else "parsed"

    result = client.get(url)
    if getattr(result, "blocked", False) and browser_fetcher is not None:
        try:
            result = browser_fetcher.get(url)
        except Exception:
            result = result

    html = getattr(result, "text", "") or ""
    if not getattr(result, "ok", False) or not html:
        return parsed_content, "failed" if not parsed_content else "parsed"

    extracted = extract_full_text_from_html(html, url)
    # Берём самый длинный валидный текст — «без потерь».
    best = max((extracted, parsed_content), key=len)
    if len(best) < MIN_USEFUL_LEN and parsed_content:
        return parsed_content, "parsed"
    status = "fetched" if best == extracted and len(extracted) > len(parsed_content) else "parsed"
    return best, status
