"""Пользовательские Telegram-каналы (добавляются с сайта).

Файл лежит в папке «Новости/», чтобы не затирался rsync и был доступен
и web-, и parser-контейнеру (том ./Новости).
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models import compact_text

PROJECT_DIR = Path(__file__).resolve().parent.parent
NEWS_DIR = PROJECT_DIR / "Новости"
CUSTOM_CHANNELS_PATH = NEWS_DIR / "custom_telegram_channels.json"
BUILTIN_CHANNELS_PATH = PROJECT_DIR / "telegram_channels.json"
BRIEF_CATEGORIES_PATH = PROJECT_DIR / "source_brief_categories.json"

_CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")
_LOCK = threading.Lock()

RESERVED_PATHS = {
    "share",
    "joinchat",
    "addstickers",
    "socks",
    "proxy",
    "setlanguage",
    "iv",
    "login",
    "s",
}


def source_name_for_channel(channel: str) -> str:
    return f"TG {channel}"


def custom_channels_path() -> Path:
    return CUSTOM_CHANNELS_PATH


def parse_telegram_channel(raw: str) -> str:
    """Достаёт публичный username из ссылки / @ника / голого имени."""
    value = compact_text(raw)
    if not value:
        raise ValueError("Укажите ссылку на Telegram-канал.")
    if value.startswith("@"):
        value = value[1:]
    if "://" not in value and "t.me/" in value.lower():
        value = "https://" + value.lstrip("/")
    if "://" in value:
        parsed = urlparse(value)
        host = (parsed.netloc or "").lower()
        if host not in {"t.me", "www.t.me", "telegram.me", "www.telegram.me"}:
            raise ValueError("Ссылка должна вести на t.me / telegram.me.")
        parts = [p for p in (parsed.path or "").split("/") if p]
        if not parts:
            raise ValueError("В ссылке нет имени канала.")
        if parts[0].startswith("+") or parts[0].lower() in {"joinchat"}:
            raise ValueError(
                "Приватные invite-ссылки не поддерживаются — нужен публичный канал."
            )
        username = parts[1] if parts[0].lower() == "s" and len(parts) > 1 else parts[0]
    else:
        username = value.split("/")[0]
    username = username.strip().lstrip("@")
    if username.lower() in RESERVED_PATHS:
        raise ValueError("Это не ссылка на канал.")
    if not _CHANNEL_RE.match(username):
        raise ValueError(
            "Имя канала: 3–64 символа, латиница, цифры и подчёркивание "
            "(например https://t.me/rbc_news)."
        )
    return username


def _empty_payload() -> dict[str, Any]:
    return {"channels": []}


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty_payload()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_payload()
    if not isinstance(data, dict):
        return _empty_payload()
    channels = data.get("channels")
    if not isinstance(channels, list):
        data["channels"] = []
    return data


def _write_payload(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def list_builtin_usernames() -> set[str]:
    if not BUILTIN_CHANNELS_PATH.is_file():
        return set()
    try:
        data = json.loads(BUILTIN_CHANNELS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    names: set[str] = set()
    for item in data.get("channels") or []:
        name = compact_text(str(item)).lstrip("@")
        if name:
            names.add(name)
    return names


def list_custom_channels(*, path: Path | None = None) -> list[dict[str, Any]]:
    payload = _read_payload(path or CUSTOM_CHANNELS_PATH)
    rows: list[dict[str, Any]] = []
    for item in payload.get("channels") or []:
        if isinstance(item, str):
            channel = compact_text(item).lstrip("@")
            if not channel:
                continue
            rows.append(
                {
                    "channel": channel,
                    "name": source_name_for_channel(channel),
                    "url": f"https://t.me/{channel}",
                    "topic_category": "",
                    "brief": "all",
                    "added_by": "",
                    "created_at": "",
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        channel = compact_text(str(item.get("channel") or "")).lstrip("@")
        if not channel:
            continue
        rows.append(
            {
                "channel": channel,
                "name": source_name_for_channel(channel),
                "url": compact_text(str(item.get("url") or "")) or f"https://t.me/{channel}",
                "topic_category": compact_text(str(item.get("topic_category") or "")),
                "brief": compact_text(str(item.get("brief") or "all")) or "all",
                "added_by": compact_text(str(item.get("added_by") or "")),
                "created_at": compact_text(str(item.get("created_at") or "")),
            }
        )
    return rows


def custom_brief_meta(*, path: Path | None = None) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for row in list_custom_channels(path=path):
        meta[row["name"]] = {
            "brief": row.get("brief") or "all",
            "topic_category": row.get("topic_category") or "",
            "custom": True,
        }
    return meta


def list_known_topic_categories(*, extra: list[str] | None = None) -> list[str]:
    names: set[str] = set()
    if BRIEF_CATEGORIES_PATH.is_file():
        try:
            data = json.loads(BRIEF_CATEGORIES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for item in (data.get("sources") or {}).values():
            if not isinstance(item, dict):
                continue
            topic = compact_text(str(item.get("topic_category") or ""))
            if topic:
                names.add(topic)
    for row in list_custom_channels():
        topic = compact_text(str(row.get("topic_category") or ""))
        if topic:
            names.add(topic)
    for item in extra or []:
        topic = compact_text(item)
        if topic:
            names.add(topic)
    preferred = [
        "Металлургия РФ",
        "Металлургия мира",
        "Китай",
        "Макроэкономика РФ",
        "Макроэкономика мира",
    ]
    ordered = [name for name in preferred if name in names]
    rest = sorted(names - set(ordered), key=lambda s: s.lower())
    return ordered + rest


def add_custom_channel(
    *,
    url: str,
    topic_category: str,
    added_by: str = "",
    brief: str = "all",
    path: Path | None = None,
) -> dict[str, Any]:
    channel = parse_telegram_channel(url)
    topic = compact_text(topic_category)
    if not topic:
        raise ValueError("Укажите категорию источника.")
    if topic.lower() == "документы rag":
        raise ValueError("Категория «Документы RAG» зарезервирована для файлов.")
    if channel in list_builtin_usernames():
        raise ValueError(f"Канал @{channel} уже есть в стандартном списке источников.")

    target = path or CUSTOM_CHANNELS_PATH
    with _LOCK:
        payload = _read_payload(target)
        channels: list[Any] = list(payload.get("channels") or [])
        for item in channels:
            existing = (
                compact_text(item).lstrip("@")
                if isinstance(item, str)
                else compact_text(str((item or {}).get("channel") or "")).lstrip("@")
            )
            if existing.lower() == channel.lower():
                raise ValueError(f"Канал @{channel} уже добавлен.")
        row = {
            "channel": channel,
            "url": f"https://t.me/{channel}",
            "topic_category": topic,
            "brief": compact_text(brief) or "all",
            "added_by": compact_text(added_by),
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        channels.append(row)
        payload["channels"] = channels
        _write_payload(target, payload)
    return {
        **row,
        "name": source_name_for_channel(channel),
    }


def remove_custom_channel(channel: str, *, path: Path | None = None) -> dict[str, Any]:
    username = parse_telegram_channel(channel)
    target = path or CUSTOM_CHANNELS_PATH
    with _LOCK:
        payload = _read_payload(target)
        channels: list[Any] = list(payload.get("channels") or [])
        kept: list[Any] = []
        removed: dict[str, Any] | None = None
        for item in channels:
            existing = (
                compact_text(item).lstrip("@")
                if isinstance(item, str)
                else compact_text(str((item or {}).get("channel") or "")).lstrip("@")
            )
            if existing.lower() == username.lower():
                if isinstance(item, dict):
                    removed = item
                else:
                    removed = {"channel": existing}
                continue
            kept.append(item)
        if removed is None:
            raise ValueError(f"Канал @{username} не найден среди добавленных.")
        payload["channels"] = kept
        _write_payload(target, payload)
    return {
        "channel": username,
        "name": source_name_for_channel(username),
        **{k: v for k, v in (removed or {}).items() if k != "channel"},
    }


def expand_custom_telegram_sources(
    *,
    min_text_length: int = 30,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for row in list_custom_channels(path=path):
        channel = row["channel"]
        expanded.append(
            {
                "name": source_name_for_channel(channel),
                "parser": "telegram",
                "channel": channel,
                "url": f"https://t.me/s/{channel}",
                "category": "telegram",
                "min_text_length": min_text_length,
                "enabled": True,
                "custom": True,
                "topic_category": row.get("topic_category") or "",
            }
        )
    return expanded
