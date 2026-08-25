from __future__ import annotations

import json
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .dates import normalize_date
from .models import compact_text


def soup_from_html(html: str | bytes) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def absolute_url(base_url: str, href: str | None) -> str:
    if not href:
        return ""
    return urljoin(base_url, href.strip())


def same_domain(url: str, candidate: str) -> bool:
    base_host = urlparse(url).netloc.replace("www.", "")
    candidate_host = urlparse(candidate).netloc.replace("www.", "")
    return bool(base_host and candidate_host and base_host == candidate_host)


def clean_node_text(node: Tag | None) -> str:
    if not node:
        return ""
    for tag in node(["script", "style", "noscript", "svg", "form"]):
        tag.extract()
    return compact_text(node.get_text(" ", strip=True))


def first_text(soup: BeautifulSoup | Tag, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        text = clean_node_text(node)
        if text:
            return text
    return ""


def first_attr(soup: BeautifulSoup | Tag, selectors: Iterable[str], attr: str) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get(attr):
            return compact_text(node.get(attr))
    return ""


def extract_article_meta(soup: BeautifulSoup) -> dict[str, str]:
    title = (
        first_attr(soup, ('meta[property="og:title"]', 'meta[name="twitter:title"]'), "content")
        or first_text(soup, ("h1", "article h1", ".article h1", ".post h1"))
    )
    description = (
        first_attr(
            soup,
            ('meta[property="og:description"]', 'meta[name="description"]', 'meta[name="twitter:description"]'),
            "content",
        )
        or first_text(soup, (".lead", ".article__text", ".article_intro", ".article-intro"))
    )
    date = (
        first_attr(soup, ("time[datetime]",), "datetime")
        or first_attr(
            soup,
            (
                'meta[property="article:published_time"]',
                'meta[name="article:published_time"]',
                'meta[itemprop="datePublished"]',
                'meta[name="date"]',
            ),
            "content",
        )
        or first_text(soup, ("time", ".date", ".article__date", ".doc_header__publish_time"))
    )
    content = first_text(
        soup,
        (
            "article",
            ".article__text",
            ".article__content",
            ".article-content",
            ".post-content",
            ".entry-content",
            ".doc__text",
            ".doc__body",
            ".b-article__text",
        ),
    )
    return {
        "title": compact_text(title),
        "summary": compact_text(description),
        "date": normalize_date(date),
        "content": compact_text(content or description),
    }


def extract_json_ld_articles(soup: BeautifulSoup) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in _flatten_json_ld(data):
            obj_type = obj.get("@type")
            if isinstance(obj_type, list):
                is_article = any(str(item).lower().endswith("article") for item in obj_type)
            else:
                is_article = str(obj_type).lower().endswith("article")
            if not is_article:
                continue
            url = obj.get("url") or obj.get("mainEntityOfPage") or ""
            if isinstance(url, dict):
                url = url.get("@id") or url.get("url") or ""
            items.append(
                {
                    "title": compact_text(obj.get("headline") or obj.get("name")),
                    "url": compact_text(url),
                    "date": normalize_date(compact_text(obj.get("datePublished"))),
                    "summary": compact_text(obj.get("description")),
                }
            )
    return items


def _flatten_json_ld(data: object) -> list[dict]:
    if isinstance(data, dict):
        result = [data]
        graph = data.get("@graph")
        if isinstance(graph, list):
            result.extend(item for item in graph if isinstance(item, dict))
        return result
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []
