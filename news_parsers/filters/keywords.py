"""Независимые блоки ключевых слов (блоки не комбинируются через AND)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..models import NewsItem, compact_text, item_full_text


@dataclass(frozen=True)
class KeywordBlock:
    id: str
    name: str
    keywords: tuple[str, ...]


def load_keyword_blocks(path: Path) -> list[KeywordBlock]:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    blocks: list[KeywordBlock] = []
    for raw in data.get("blocks", []):
        keywords = tuple(
            compact_text(keyword).lower().replace("ё", "е")
            for keyword in raw.get("keywords", [])
            if compact_text(keyword)
        )
        if not keywords:
            continue
        blocks.append(
            KeywordBlock(
                id=compact_text(raw.get("id", "")),
                name=compact_text(raw.get("name", "")),
                keywords=keywords,
            )
        )
    return blocks


def normalize_text(text: str) -> str:
    return compact_text(text).lower().replace("ё", "е")


def find_block_match(text: str, block: KeywordBlock) -> str | None:
    normalized = normalize_text(text)
    if not normalized:
        return None
    for keyword in sorted(block.keywords, key=len, reverse=True):
        if _keyword_matches(normalized, keyword):
            return keyword
    return None


def _keyword_matches(normalized_text: str, keyword: str) -> bool:
    if not keyword:
        return False
    if len(keyword) <= 4 and re.fullmatch(r"[a-z0-9]+", keyword):
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        return re.search(pattern, normalized_text) is not None
    return keyword in normalized_text


def match_item_to_block(item: NewsItem, blocks: list[KeywordBlock]) -> tuple[str, str, str] | None:
    """Первый совпавший блок по порядку в файле (блоки не наслаиваются)."""
    text = item_full_text(item)
    for block in blocks:
        keyword = find_block_match(text, block)
        if keyword:
            return block.id, block.name, keyword
    return None


def filter_items_by_keyword_blocks(
    items: list[NewsItem],
    blocks: list[KeywordBlock],
    *,
    active_block_id: str | None = None,
) -> tuple[list[NewsItem], dict[str, int | dict[str, int]]]:
    """
    active_block_id: если задан — оставить только новости этого блока.
    Иначе — любой блок; тег = первый совпавший.
    """
    if active_block_id:
        blocks = [block for block in blocks if block.id == active_block_id]
        if not blocks:
            raise ValueError(f"Unknown keyword block: {active_block_id}")

    kept: list[NewsItem] = []
    checked = 0
    passed = 0
    per_block: dict[str, int] = {}

    for item in items:
        if item.status != "ok":
            kept.append(item)
            continue
        checked += 1
        match = match_item_to_block(item, blocks)
        if not match:
            continue
        block_id, block_name, keyword = match
        passed += 1
        per_block[block_id] = per_block.get(block_id, 0) + 1
        item.keyword_block = block_id
        item.keyword_match = keyword
        item.relevance_match = f"{block_id}: {keyword}"
        kept.append(item)

    stats: dict[str, int | dict[str, int]] = {
        "checked": checked,
        "passed": passed,
        "dropped": checked - passed,
        "kept_total": len(kept),
        "per_block": per_block,
    }
    return kept, stats
