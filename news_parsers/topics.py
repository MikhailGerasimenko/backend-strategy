"""Темы новостей по блокам keyword_blocks.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .models import NewsItem

PROJECT_DIR = Path(__file__).resolve().parents[1]
KEYWORD_BLOCKS_PATH = PROJECT_DIR / "keyword_blocks.json"


@lru_cache(maxsize=1)
def load_topic_labels(path: Path | None = None) -> dict[str, str]:
    blocks_path = path or KEYWORD_BLOCKS_PATH
    if not blocks_path.exists():
        return {}
    data = json.loads(blocks_path.read_text(encoding="utf-8"))
    return {
        block["id"]: block.get("name") or block["id"]
        for block in data.get("blocks", [])
        if block.get("id")
    }


def derive_topic(item: NewsItem, labels: dict[str, str] | None = None) -> str:
    labels = labels if labels is not None else load_topic_labels()
    if item.keyword_block:
        return labels.get(item.keyword_block, item.keyword_block)
    if item.category:
        return item.category
    return item.source or "Без темы"


def news_text_body(item: NewsItem) -> str:
    text = (item.content or "").strip()
    if text:
        return text
    summary = (item.summary or "").strip()
    if summary:
        return summary
    return (item.title or "").strip()
