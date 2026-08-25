from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def item_full_text(item: "NewsItem") -> str:
    return " ".join(part for part in (item.title, item.summary, item.content) if part)


@dataclass
class NewsItem:
    source: str
    category: str
    title: str
    date: str
    url: str
    summary: str = ""
    content: str = ""
    language: str = "und"
    fetched_at: str = ""
    status: str = "ok"
    error: str = ""
    relevance_match: str = ""
    keyword_block: str = ""
    keyword_match: str = ""

    def __post_init__(self) -> None:
        if not self.fetched_at:
            self.fetched_at = utc_now_iso()
        self.title = compact_text(self.title)
        self.summary = compact_text(self.summary)
        self.content = compact_text(self.content)
        self.language = compact_text(self.language) or "und"
        self.error = compact_text(self.error)
        self.relevance_match = compact_text(self.relevance_match)
        self.keyword_block = compact_text(self.keyword_block)
        self.keyword_match = compact_text(self.keyword_match)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ParserHealth:
    source: str
    status: str
    items: int = 0
    errors: int = 0
    message: str = ""
    fetched_at: str = ""

    def __post_init__(self) -> None:
        if not self.fetched_at:
            self.fetched_at = utc_now_iso()
        self.message = compact_text(self.message)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compact_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split())
