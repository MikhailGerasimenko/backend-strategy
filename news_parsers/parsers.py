from __future__ import annotations

import xml.etree.ElementTree as ET
import re
from datetime import datetime, timedelta
from typing import Any, Callable, TYPE_CHECKING
from urllib.parse import parse_qsl, parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from .dates import normalize_date
from .html_utils import (
    absolute_url,
    clean_node_text,
    extract_article_meta,
    extract_json_ld_articles,
    same_domain,
    soup_from_html,
)
from .http import HttpClient
from .models import NewsItem, ParserHealth, compact_text
from .telegram import fetch_telegram

if TYPE_CHECKING:
    from .browser_fetch import BrowserFetcher

SourceConfig = dict[str, Any]
ParserFunc = Callable[[SourceConfig, HttpClient], list[NewsItem]]


def parse_source(
    source: SourceConfig,
    client: HttpClient,
    *,
    browser_fetcher: BrowserFetcher | None = None,
) -> tuple[list[NewsItem], ParserHealth]:
    parser_name = source.get("parser", "")
    parser = PARSERS.get(parser_name)
    if not parser:
        return [], ParserHealth(
            source=source.get("name", "unknown"),
            status="parse_changed",
            message=f"Unknown parser: {parser_name}",
        )

    try:
        if parser_name in BROWSER_CAPABLE_PARSERS:
            raw_items = parser(source, client, browser_fetcher=browser_fetcher)
        else:
            raw_items = parser(source, client)
        items = validate_items(deduplicate_items(raw_items))
    except Exception as exc:  # noqa: BLE001
        return [], ParserHealth(
            source=source.get("name", "unknown"),
            status="parse_changed",
            errors=1,
            message=compact_text(exc),
        )

    status = "ok" if items else "no_items"
    errors = sum(1 for item in items if item.status != "ok" or item.error)
    if items and all(item.status == "blocked" for item in items):
        status = "blocked"
    elif items and all(item.status == "network_error" for item in items):
        status = "network_error"
    elif items and all(item.status == "parse_changed" for item in items):
        status = "parse_changed"
    return items, ParserHealth(
        source=source.get("name", "unknown"),
        status=status,
        items=len(items),
        errors=errors,
        message="",
    )


def _fetch_with_browser_fallback(
    url: str,
    client: HttpClient,
    *,
    headers: dict[str, str] | None = None,
    browser_fetcher=None,
    wait_selector: str | None = None,
    label: str = "",
) -> Any:
    """HTTP-запрос; при блокировке повторяет через headless Chromium (если доступен)."""
    result = client.get(url, headers=headers)
    if not result.blocked or browser_fetcher is None:
        return result
    print(
        f"  {label or url}: HTTP {result.status_code or 'block'}, повтор через браузер…",
        flush=True,
    )
    return browser_fetcher.get(url, headers=headers, wait_selector=wait_selector)


def _metalinfo_page_ok(result) -> bool:
    return result.ok and "news-block" in (result.text or "")


def _fetch_metalinfo_page(url: str, client: HttpClient, browser_fetcher) -> object:
    headers = {"Referer": "https://www.metalinfo.ru/"}
    result = client.get(url, headers=headers)
    if _metalinfo_page_ok(result):
        return result
    if browser_fetcher is None:
        return result
    print(
        f"  MetalInfo: HTTP {result.status_code or 'error'}, повтор через браузер…",
        flush=True,
    )
    return browser_fetcher.get(url, headers=headers, wait_selector="div.news-block")


def fetch_metalinfo(
    source: SourceConfig,
    client: HttpClient,
    *,
    browser_fetcher=None,
) -> list[NewsItem]:
    items: list[NewsItem] = []
    base_url = source["url"]
    for page_number in range(1, int(source.get("max_pages", 1)) + 1):
        page_url = base_url if page_number == 1 else build_query_url(base_url, {"pn": str(page_number)})
        result = _fetch_metalinfo_page(page_url, client, browser_fetcher)
        if result.blocked:
            return [blocked_item(source, page_url, result)]
        if not result.ok:
            if page_number == 1:
                return [error_item(source, page_url, "network_error", result.error or str(result.status_code))]
            break

        soup = soup_from_html(result.content)
        articles = soup.find_all("div", class_="news-block clearfix")
        if not articles:
            break

        for article in articles:
            title_tag = article.find("h2", class_="news-title")
            link_tag = title_tag.find("a") if title_tag else None
            if not link_tag and title_tag and title_tag.parent and title_tag.parent.name == "a":
                link_tag = title_tag.parent
            if not link_tag or not link_tag.get("href"):
                continue
            summary_tag = article.find("div", class_="news-annotation")
            date_tag = article.find("small", class_="news-date")
            items.append(
                NewsItem(
                    source=source["name"],
                    category=source.get("category", ""),
                    title=clean_node_text(title_tag),
                    date=normalize_date(clean_node_text(date_tag)),
                    url=absolute_url(base_url, link_tag.get("href")),
                    summary=clean_node_text(summary_tag),
                )
            )
    return items


def fetch_eurometal(
    source: SourceConfig,
    client: HttpClient,
    *,
    browser_fetcher=None,
) -> list[NewsItem]:
    items: list[NewsItem] = []
    base_url = source["url"].rstrip("/")
    if base_url.endswith("/news"):
        news_base = base_url
    else:
        news_base = base_url[: base_url.find("/news") + len("/news")] if "/news" in base_url else base_url

    wait_selector = source.get("browser_wait_selector")
    for page_number in range(1, int(source.get("max_pages", 1)) + 1):
        page_url = f"{news_base}/page/{page_number}/"
        result = _fetch_with_browser_fallback(
            page_url, client,
            browser_fetcher=browser_fetcher,
            wait_selector=wait_selector,
            label=source.get("name", ""),
        )
        if result.blocked:
            return [blocked_item(source, page_url, result)]
        if not result.ok:
            if page_number == 1:
                return [error_item(source, page_url, "network_error", result.error or str(result.status_code))]
            break
        soup = soup_from_html(result.content)
        links = [
            absolute_url(page_url, link.get("href"))
            for link in soup.select("a")
            if link.get("href") and clean_node_text(link).lower() == "read more"
        ]
        if not links:
            break
        for link in links:
            meta = fetch_article_meta(link, client, browser_fetcher=browser_fetcher)
            if meta.get("status") == "blocked":
                items.append(blocked_item(source, link, meta["result"]))
                continue
            if not meta.get("title"):
                continue
            items.append(
                NewsItem(
                    source=source["name"],
                    category=source.get("category", ""),
                    title=meta["title"],
                    date=meta["date"],
                    url=link,
                    summary=meta["summary"],
                    content=meta["content"],
                )
            )
    return items


def fetch_yieh(
    source: SourceConfig,
    client: HttpClient,
    *,
    browser_fetcher=None,
) -> list[NewsItem]:
    items: list[NewsItem] = []
    base_url = source["url"]
    wait_selector = source.get("browser_wait_selector", "div.each")
    for page_index in range(int(source.get("max_pages", 1))):
        page_url = base_url if page_index == 0 else build_query_url(base_url, {"Page": str(page_index)})
        result = _fetch_with_browser_fallback(
            page_url, client,
            headers={"Referer": "https://yieh.com/en/News"},
            browser_fetcher=browser_fetcher,
            wait_selector=wait_selector,
            label=source.get("name", ""),
        )
        if result.blocked:
            return [blocked_item(source, page_url, result)]
        if not result.ok:
            if page_index == 0:
                return [error_item(source, page_url, "network_error", result.error or str(result.status_code))]
            break
        soup = soup_from_html(result.content)
        cards = soup.find_all("div", class_="each")
        if not cards:
            break
        page_items = 0
        for card in cards:
            link_tag = card.find("a", href=True)
            if not link_tag or str(link_tag.get("href", "")).startswith("javascript"):
                continue
            link = absolute_url(base_url, link_tag.get("href"))
            article = fetch_article_meta(link, client, browser_fetcher=browser_fetcher)
            content = article.get("content") or ""
            items.append(
                NewsItem(
                    source=source["name"],
                    category=source.get("category", ""),
                    title=clean_node_text(link_tag),
                    date=normalize_date(clean_node_text(card.find("li", class_="date"))),
                    url=link,
                    summary=article.get("summary", ""),
                    content=content,
                )
            )
            page_items += 1
        if page_items == 0:
            break
    return items


def fetch_mysteel(source: SourceConfig, client: HttpClient) -> list[NewsItem]:
    items: list[NewsItem] = []
    if source.get("period_start") and source.get("period_end"):
        start = datetime.strptime(source["period_start"], "%Y-%m-%d").date()
        end = datetime.strptime(source["period_end"], "%Y-%m-%d").date()
        dates = [(start + timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range((end - start).days + 1)]
    else:
        today = datetime.today()
        dates = [(today - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(int(source.get("days_back", 10)))]

    for offset, date_str in enumerate(dates):
        page_url = (
            f"{source['url']}?startTime={date_str}&endTime={date_str}"
            "&productItem=01&channelItem=6946"
        )
        result = client.get(page_url, headers={"Referer": "https://www.mysteel.net/"})
        if result.blocked:
            return [blocked_item(source, page_url, result)]
        if not result.ok:
            if offset == 0:
                return [error_item(source, page_url, "network_error", result.error or str(result.status_code))]
            continue
        soup = soup_from_html(result.content)
        for article in soup.find_all("li", class_="m-list-item"):
            link_tag = article.find("a", href=True)
            title = clean_node_text(article.find("p", class_="m-title"))
            if not link_tag or not title:
                continue
            link = absolute_url(source["url"], link_tag.get("href"))
            article_meta = fetch_article_meta(link, client)
            items.append(
                NewsItem(
                    source=source["name"],
                    category=source.get("category", ""),
                    title=title,
                    date=normalize_date(clean_node_text(article.find("p", class_="date"))),
                    url=link,
                    summary=clean_node_text(article.find("p", class_="description")) or article_meta.get("summary", ""),
                    content=article_meta.get("content", ""),
                )
            )
    return items


def fetch_generic_html(
    source: SourceConfig,
    client: HttpClient,
    *,
    browser_fetcher=None,
) -> list[NewsItem]:
    wait_selector = source.get("browser_wait_selector")
    result = _fetch_with_browser_fallback(
        source["url"], client,
        browser_fetcher=browser_fetcher,
        wait_selector=wait_selector,
        label=source.get("name", ""),
    )
    if result.blocked:
        return [blocked_item(source, source["url"], result)]
    if not result.ok:
        return [error_item(source, source["url"], "network_error", result.error or str(result.status_code))]

    soup = soup_from_html(result.content)
    candidates = collect_listing_links(source["url"], soup)
    max_items = int(source.get("max_items", 30))
    items: list[NewsItem] = []

    for candidate in candidates[:max_items]:
        article = fetch_article_meta(candidate["url"], client, browser_fetcher=browser_fetcher)
        if article.get("status") == "blocked":
            items.append(blocked_item(source, candidate["url"], article["result"]))
            continue
        title = article.get("title") or candidate["title"]
        if not title:
            continue
        items.append(
            NewsItem(
                source=source["name"],
                category=source.get("category", ""),
                title=title,
                date=article.get("date", "") or candidate.get("date", ""),
                url=candidate["url"],
                summary=article.get("summary", ""),
                content=article.get("content", ""),
            )
        )
    return items


def fetch_reuters_sitemap(source: SourceConfig, client: HttpClient) -> list[NewsItem]:
    sitemap_url = source.get("sitemap_url", "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml")
    result = client.get(sitemap_url, headers={"Accept": "application/xml,text/xml,*/*"})
    if result.blocked:
        return [blocked_item(source, sitemap_url, result)]
    if not result.ok:
        return [error_item(source, sitemap_url, "network_error", result.error or str(result.status_code))]

    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
    }
    try:
        root = ET.fromstring(result.content)
    except ET.ParseError as exc:
        return [error_item(source, sitemap_url, "parse_changed", str(exc))]

    path_prefixes = tuple(source.get("path_prefixes", ["/business/", "/markets/"]))
    exclude_path_fragments = tuple(source.get("exclude_path_fragments", []))
    title_keywords = tuple(keyword.lower() for keyword in source.get("title_keywords", []))
    max_items = int(source.get("max_items", 30))
    items: list[NewsItem] = []

    for url_node in root.findall("sm:url", ns):
        loc = compact_text(url_node.findtext("sm:loc", default="", namespaces=ns))
        title = compact_text(url_node.findtext("news:news/news:title", default="", namespaces=ns))
        if not loc or not reuters_item_matches(loc, title, path_prefixes, exclude_path_fragments, title_keywords):
            continue

        published_at = compact_text(
            url_node.findtext("news:news/news:publication_date", default="", namespaces=ns)
        )
        if not title:
            continue

        items.append(
            NewsItem(
                source=source["name"],
                category=source.get("category", ""),
                title=title,
                date=normalize_date(published_at),
                url=loc,
                summary="",
                content="",
                language="en",
            )
        )
        if len(items) >= max_items:
            break

    return items


def reuters_item_matches(
    loc: str,
    title: str,
    path_prefixes: tuple[str, ...],
    exclude_path_fragments: tuple[str, ...],
    title_keywords: tuple[str, ...],
) -> bool:
    path = urlparse(loc).path
    lowered_title = title.lower()
    if any(fragment in path for fragment in exclude_path_fragments):
        return False
    path_matches = any(path.startswith(prefix) for prefix in path_prefixes)
    keyword_matches = bool(title_keywords) and any(keyword_in_title(keyword, lowered_title) for keyword in title_keywords)
    return path_matches or keyword_matches


def keyword_in_title(keyword: str, lowered_title: str) -> bool:
    escaped = re.escape(keyword)
    if " " in keyword:
        return keyword in lowered_title
    return re.search(rf"\b{escaped}\b", lowered_title) is not None


def collect_listing_links(base_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()

    for item in extract_json_ld_articles(soup):
        url = absolute_url(base_url, item.get("url"))
        if is_article_url(base_url, url) and url not in seen:
            seen.add(url)
            candidates.append(
                {
                    "url": url,
                    "title": item.get("title", ""),
                    "date": item.get("date", ""),
                }
            )

    for link in soup.find_all("a", href=True):
        url = absolute_url(base_url, link.get("href"))
        title = clean_node_text(link)
        if not title or len(title) < 12:
            continue
        if not is_article_url(base_url, url) or url in seen:
            continue
        seen.add(url)
        candidates.append({"url": url, "title": title, "date": ""})

    return candidates


def is_article_url(base_url: str, url: str) -> bool:
    if not url or not same_domain(base_url, url):
        return False
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(skip in path for skip in ("/tag/", "/authors/", "/video/", "/photo/", "/search")):
        return False
    if any(marker in path for marker in ("/doc/", "/news/", "/business/", "/economics/", "/markets/")):
        return True
    return any(part.isdigit() and len(part) >= 5 for part in path.replace("-", "/").split("/"))


def fetch_article_meta(
    url: str,
    client: HttpClient,
    *,
    browser_fetcher=None,
) -> dict[str, Any]:
    result = _fetch_with_browser_fallback(url, client, browser_fetcher=browser_fetcher)
    if result.blocked:
        return {"status": "blocked", "result": result}
    if not result.ok:
        return {"status": "network_error", "title": "", "date": "", "summary": "", "content": ""}
    return extract_article_meta(soup_from_html(result.content))


def build_query_url(url: str, params: dict[str, str]) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [value]
    new_query = urlencode({key: values[0] for key, values in query.items()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def blocked_item(source: SourceConfig, url: str, result: Any) -> NewsItem:
    return NewsItem(
        source=source.get("name", "unknown"),
        category=source.get("category", ""),
        title="",
        date="",
        url=url,
        status="blocked",
        error=f"Blocked or rate limited. HTTP status: {getattr(result, 'status_code', 'NA')}",
    )


def error_item(source: SourceConfig, url: str, status: str, error: str) -> NewsItem:
    return NewsItem(
        source=source.get("name", "unknown"),
        category=source.get("category", ""),
        title="",
        date="",
        url=url,
        status=status,
        error=error,
    )


def deduplicate_items(items: list[NewsItem]) -> list[NewsItem]:
    deduped: list[NewsItem] = []
    seen: set[str] = set()
    for item in items:
        item.url = normalize_news_url(item.url)
        key = item.url or f"{item.source}:{item.title}:{item.date}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def normalize_news_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    tracking_prefixes = ("utm_",)
    tracking_names = {"from", "from_source", "yclid", "gclid", "fbclid"}
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key not in tracking_names and not key.startswith(tracking_prefixes)
    ]
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), ""))


def validate_items(items: list[NewsItem]) -> list[NewsItem]:
    for item in items:
        item.language = item.language if item.language != "und" else detect_language(item)
        if item.status != "ok":
            continue
        missing = [field for field in ("source", "title", "url") if not getattr(item, field)]
        if missing:
            item.status = "parse_changed"
            item.error = f"Missing required fields: {', '.join(missing)}"
    return items


def detect_language(item: NewsItem) -> str:
    text = f"{item.title} {item.summary} {item.content}"
    if any("а" <= char.lower() <= "я" or char.lower() == "ё" for char in text):
        return "ru"
    if any("a" <= char.lower() <= "z" for char in text):
        return "en"
    return "und"


PARSERS: dict[str, ParserFunc] = {
    "metalinfo": fetch_metalinfo,
    "eurometal": fetch_eurometal,
    "yieh": fetch_yieh,
    "mysteel": fetch_mysteel,
    "reuters_sitemap": fetch_reuters_sitemap,
    "generic_html": fetch_generic_html,
    "telegram": fetch_telegram,
}

BROWSER_CAPABLE_PARSERS = frozenset({"metalinfo", "eurometal", "yieh", "generic_html"})
