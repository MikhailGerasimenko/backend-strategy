from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .config import NEWS_DIR
from .prompt_split import join_brief_prompt, split_brief_prompt

PROMPT_OVERRIDES_PATH = NEWS_DIR / "prompt_overrides.json"
_LOCK = threading.Lock()


def _read_data(path: Path | None = None) -> dict[str, Any]:
    target = path or PROMPT_OVERRIDES_PATH
    if not target.is_file():
        return {"daily": {}, "weekly": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"daily": {}, "weekly": {}}
    if not isinstance(data, dict):
        return {"daily": {}, "weekly": {}}
    for scope in ("daily", "weekly"):
        if not isinstance(data.get(scope), dict):
            data[scope] = {}
    return data


def _write_data(data: dict[str, Any], path: Path | None = None) -> None:
    target = path or PROMPT_OVERRIDES_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


def _normalize_override_record(value: Any) -> dict[str, str] | None:
    """Приводит override к {prompt, system_prompt, user_prompt}."""
    if isinstance(value, dict):
        system = value.get("system_prompt")
        user = value.get("user_prompt")
        prompt = (value.get("prompt") or "").strip()
        if system is not None or user is not None:
            system_s = (system or "").strip()
            user_s = (user or "").strip()
            if not prompt:
                prompt = join_brief_prompt(system_s, user_s)
            if not prompt:
                return None
            return {
                "prompt": prompt,
                "system_prompt": system_s,
                "user_prompt": user_s,
            }
        if prompt:
            parts = split_brief_prompt(prompt)
            return {
                "prompt": prompt,
                "system_prompt": parts["system_prompt"],
                "user_prompt": parts["user_prompt"],
            }
        return None

    if isinstance(value, str) and value.strip():
        prompt = value.strip()
        parts = split_brief_prompt(prompt)
        return {
            "prompt": prompt,
            "system_prompt": parts["system_prompt"],
            "user_prompt": parts["user_prompt"],
        }
    return None


def get_prompt_override_record(
    scope: str, user_login: str, variant: str
) -> dict[str, str] | None:
    data = _read_data()
    by_user = data.get(scope) or {}
    user_prompts = by_user.get(user_login) or {}
    return _normalize_override_record(user_prompts.get(variant))


def get_prompt_override(scope: str, user_login: str, variant: str) -> str | None:
    record = get_prompt_override_record(scope, user_login, variant)
    if record is None:
        return None
    return record["prompt"]


def resolve_prompt(scope: str, user_login: str, variant: str, fallback: str) -> tuple[str, bool]:
    prompt = get_prompt_override(scope, user_login, variant)
    if prompt is None:
        return fallback, False
    return prompt, True


def resolve_prompt_parts(
    scope: str, user_login: str, variant: str, fallback: str
) -> tuple[dict[str, str], bool]:
    """Возвращает ({prompt, system_prompt, user_prompt}, customized)."""
    record = get_prompt_override_record(scope, user_login, variant)
    if record is not None:
        return record, True
    parts = split_brief_prompt(fallback)
    return {
        "prompt": fallback,
        "system_prompt": parts["system_prompt"],
        "user_prompt": parts["user_prompt"],
    }, False


def save_prompt_override(
    scope: str,
    user_login: str,
    variant: str,
    prompt: str,
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    plain: bool = False,
) -> None:
    cleaned = prompt.strip()
    if not cleaned:
        raise ValueError("Промпт пустой.")

    if plain:
        # Map/reduce — цельный служебный текст, без разбиения на «структуру брифа».
        record: dict[str, str] | str = cleaned
    elif system_prompt is not None or user_prompt is not None:
        # Храним ровно то, что пользователь ввёл в двух полях — без зависимости от маркера.
        record = {
            "prompt": cleaned,
            "system_prompt": (system_prompt or "").strip(),
            "user_prompt": (user_prompt or "").strip(),
        }
    else:
        # Старый путь: одна строка — сразу кладём и разбиение для UI.
        parts = split_brief_prompt(cleaned)
        record = {
            "prompt": cleaned,
            "system_prompt": parts["system_prompt"],
            "user_prompt": parts["user_prompt"],
        }

    with _LOCK:
        data = _read_data()
        by_user = data.setdefault(scope, {})
        user_prompts = dict(by_user.get(user_login) or {})
        user_prompts[variant] = record
        by_user[user_login] = user_prompts
        _write_data(data)


def clear_prompt_override(scope: str, user_login: str, variant: str) -> bool:
    with _LOCK:
        data = _read_data()
        by_user = data.get(scope) or {}
        user_prompts = dict(by_user.get(user_login) or {})
        if variant not in user_prompts:
            return False
        user_prompts.pop(variant, None)
        if user_prompts:
            by_user[user_login] = user_prompts
        else:
            by_user.pop(user_login, None)
        data[scope] = by_user
        _write_data(data)
        return True
