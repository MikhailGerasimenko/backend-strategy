"""Отключение источников без удаления новостей из векторной БД."""

from __future__ import annotations

from typing import Any

from news_parsers.custom_telegram import (
    add_custom_channel,
    list_custom_channels,
    remove_custom_channel,
)
from news_parsers.disabled_sources import (
    add_disabled,
    clear_disabled,
    is_disabled,
    list_disabled_sources,
    pop_disabled,
)
from news_parsers.runner import load_sources

from .config import SOURCES_PATH
from .services_rag import load_brief_categories


def _topic_from_brief(name: str) -> tuple[str, str]:
    meta = (load_brief_categories().get("sources") or {}).get(name) or {}
    return (
        str(meta.get("topic_category") or "").strip(),
        str(meta.get("brief") or "").strip() or "all",
    )


def list_managed_sources() -> dict[str, Any]:
    configured = load_sources(SOURCES_PATH, include_web=True, include_telegram=True)
    custom_by_name = {row["name"]: row for row in list_custom_channels() if row.get("name")}
    active: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in configured:
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        custom = custom_by_name.get(name) or {}
        topic, brief = _topic_from_brief(name)
        kind = "telegram" if item.get("parser") == "telegram" else "web"
        active.append(
            {
                "name": name,
                "kind": kind,
                "channel": item.get("channel") or custom.get("channel") or "",
                "url": item.get("url") or custom.get("url") or "",
                "custom": bool(custom),
                "topic_category": custom.get("topic_category") or topic,
                "brief": custom.get("brief") or brief,
            }
        )
    disabled = []
    for row in list_disabled_sources():
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        topic, brief = _topic_from_brief(name)
        disabled.append(
            {
                **row,
                "name": name,
                "kind": row.get("kind") or ("telegram" if name.startswith("TG ") else "web"),
                "topic_category": row.get("topic_category") or topic,
                "brief": row.get("brief") or brief,
            }
        )
    return {"sources": active, "disabled": disabled}


def disable_managed_source(name: str, *, disabled_by: str = "") -> dict[str, Any]:
    label = (name or "").strip()
    if not label:
        raise ValueError("Не указан источник.")
    if is_disabled(label):
        raise ValueError(f"Источник «{label}» уже отключён.")

    configured = load_sources(SOURCES_PATH, include_web=True, include_telegram=True)
    item = next((row for row in configured if str(row.get("name") or "").strip() == label), None)
    if item is None:
        raise ValueError(f"Источник «{label}» не найден среди активных.")

    custom_by_name = {row["name"]: row for row in list_custom_channels() if row.get("name")}
    custom = custom_by_name.get(label) or {}
    origin = "custom" if custom else "builtin"
    topic, brief = _topic_from_brief(label)
    meta = {
        "name": label,
        "kind": "telegram" if item.get("parser") == "telegram" else "web",
        "origin": origin,
        "channel": item.get("channel") or custom.get("channel") or "",
        "url": item.get("url") or custom.get("url") or "",
        "topic_category": custom.get("topic_category") or topic,
        "brief": custom.get("brief") or brief,
        "added_by": custom.get("added_by") or "",
        "disabled_by": disabled_by,
    }
    if origin == "custom":
        channel = str(meta.get("channel") or "")
        if not channel:
            raise ValueError("У пользовательского канала нет имени.")
        stored = add_disabled(meta)
        try:
            remove_custom_channel(channel)
        except Exception:
            clear_disabled(label)
            raise
        return stored
    return add_disabled(meta)


def restore_managed_source(name: str, *, restored_by: str = "") -> dict[str, Any]:
    label = (name or "").strip()
    if not label:
        raise ValueError("Не указан источник.")
    row = pop_disabled(label)
    if row.get("origin") == "custom":
        url = str(row.get("url") or "").strip() or f"https://t.me/{row.get('channel') or ''}"
        topic = str(row.get("topic_category") or "").strip() or "Прочее"
        try:
            added = add_custom_channel(
                url=url,
                topic_category=topic,
                added_by=str(row.get("added_by") or restored_by or ""),
                brief=str(row.get("brief") or "all"),
            )
        except Exception:
            add_disabled(row)
            raise
        return {**row, **added, "restored": True}
    return {**row, "restored": True}
