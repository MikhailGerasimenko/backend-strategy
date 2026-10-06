"""Отключённые источники: парсинг стоп, новости в RAG не трогаем.

Файл в «Новости/», чтобы не затирался rsync.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import compact_text

PROJECT_DIR = Path(__file__).resolve().parent.parent
NEWS_DIR = PROJECT_DIR / "Новости"
DISABLED_SOURCES_PATH = NEWS_DIR / "disabled_sources.json"

_LOCK = threading.Lock()


def _empty_payload() -> dict[str, Any]:
    return {"sources": []}


def _read_payload(path: Path | None = None) -> dict[str, Any]:
    target = path or DISABLED_SOURCES_PATH
    if not target.is_file():
        return _empty_payload()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_payload()
    if not isinstance(data, dict):
        return _empty_payload()
    rows = data.get("sources")
    if not isinstance(rows, list):
        data["sources"] = []
    return data


def _write_payload(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _row_name(item: Any) -> str:
    if isinstance(item, str):
        return compact_text(item)
    if isinstance(item, dict):
        return compact_text(str(item.get("name") or ""))
    return ""


def list_disabled_sources(*, path: Path | None = None) -> list[dict[str, Any]]:
    payload = _read_payload(path)
    rows: list[dict[str, Any]] = []
    for item in payload.get("sources") or []:
        if isinstance(item, str):
            name = compact_text(item)
            if name:
                rows.append({"name": name, "origin": "builtin"})
            continue
        if not isinstance(item, dict):
            continue
        name = compact_text(str(item.get("name") or ""))
        if not name:
            continue
        rows.append(dict(item, name=name))
    return rows


def disabled_name_set(*, path: Path | None = None) -> set[str]:
    return {row["name"] for row in list_disabled_sources(path=path) if row.get("name")}


def is_disabled(name: str, *, path: Path | None = None) -> bool:
    needle = compact_text(name)
    if not needle:
        return False
    return needle in disabled_name_set(path=path)


def archived_brief_meta(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for row in list_disabled_sources(path=path):
        name = compact_text(str(row.get("name") or ""))
        if not name:
            continue
        meta[name] = {
            "brief": compact_text(str(row.get("brief") or "")) or "all",
            "topic_category": compact_text(str(row.get("topic_category") or "")),
            "custom": row.get("origin") == "custom",
            "retired": True,
        }
    return meta


def exclude_disabled(sources: list[dict[str, Any]], *, path: Path | None = None) -> list[dict[str, Any]]:
    blocked = disabled_name_set(path=path)
    if not blocked:
        return sources
    return [
        item
        for item in sources
        if compact_text(str(item.get("name") or "")) not in blocked
    ]


def add_disabled(row: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    name = compact_text(str(row.get("name") or ""))
    if not name:
        raise ValueError("Не указано имя источника.")
    target = path or DISABLED_SOURCES_PATH
    stored = {
        **{k: v for k, v in row.items() if v not in (None, "")},
        "name": name,
        "disabled_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    with _LOCK:
        payload = _read_payload(target)
        rows: list[Any] = list(payload.get("sources") or [])
        kept: list[Any] = []
        for item in rows:
            if _row_name(item) == name:
                continue
            kept.append(item)
        kept.append(stored)
        payload["sources"] = kept
        _write_payload(target, payload)
    return stored


def pop_disabled(name: str, *, path: Path | None = None) -> dict[str, Any]:
    needle = compact_text(name)
    if not needle:
        raise ValueError("Не указано имя источника.")
    target = path or DISABLED_SOURCES_PATH
    with _LOCK:
        payload = _read_payload(target)
        rows: list[Any] = list(payload.get("sources") or [])
        kept: list[Any] = []
        removed: dict[str, Any] | None = None
        for item in rows:
            if _row_name(item) == needle:
                if isinstance(item, dict):
                    removed = dict(item)
                else:
                    removed = {"name": needle, "origin": "builtin"}
                continue
            kept.append(item)
        if removed is None:
            raise ValueError(f"Источник «{needle}» не найден среди отключённых.")
        payload["sources"] = kept
        _write_payload(target, payload)
    return removed


def clear_disabled(name: str, *, path: Path | None = None) -> bool:
    needle = compact_text(name)
    if not needle or needle not in disabled_name_set(path=path):
        return False
    pop_disabled(needle, path=path)
    return True
