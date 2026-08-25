from __future__ import annotations

import os
import time
from typing import Any, Callable

import requests

from .config import (
    BRIEF_MODEL,
    HTTP_TIMEOUT,
    MAX_TOKENS,
    OPENROUTER_BASE_URL,
    OPENROUTER_RETRIES,
    OPENROUTER_RETRY_BASE_SEC,
)

RETRYABLE_STATUS = {429, 502, 503, 504}


def get_api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "").strip()


class OpenRouterError(RuntimeError):
    pass


class GenerationCancelled(RuntimeError):
    """Генерация остановлена пользователем."""

    is_cancellation = True


def _check_cancel(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel and should_cancel():
        raise GenerationCancelled("Генерация остановлена пользователем")


def chat_completion(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    api_key = get_api_key()
    if not api_key:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not set. Export it or add to .env before running generate_brief.py."
        )

    payload: dict[str, Any] = {
        "model": model or os.getenv("BRIEF_MODEL", BRIEF_MODEL).strip() or BRIEF_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens or MAX_TOKENS,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/strategic-navigator",
        "X-Title": "Strategic Navigator Brief",
    }

    last_error: Exception | None = None
    for attempt in range(OPENROUTER_RETRIES):
        _check_cancel(should_cancel)
        try:
            response = requests.post(
                OPENROUTER_BASE_URL,
                headers=headers,
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            if response.status_code in RETRYABLE_STATUS:
                wait = _retry_wait_seconds(response, attempt)
                if attempt < OPENROUTER_RETRIES - 1:
                    print(
                        f"OpenRouter {response.status_code}, retry in {wait:.0f}s "
                        f"({attempt + 1}/{OPENROUTER_RETRIES})...",
                        flush=True,
                    )
                    _sleep_interruptible(wait, should_cancel)
                    continue
                response.raise_for_status()
            response.raise_for_status()
            data = response.json()
            try:
                return data["choices"][0]["message"]["content"].strip()
            except (KeyError, IndexError, TypeError) as exc:
                raise OpenRouterError(f"Unexpected OpenRouter response: {data}") from exc
        except GenerationCancelled:
            raise
        except requests.RequestException as exc:
            last_error = exc
            status = exc.response.status_code if exc.response is not None else None
            if status in RETRYABLE_STATUS and attempt < OPENROUTER_RETRIES - 1:
                wait = _retry_wait_seconds(exc.response, attempt)
                print(
                    f"OpenRouter error {status}, retry in {wait:.0f}s "
                    f"({attempt + 1}/{OPENROUTER_RETRIES})...",
                    flush=True,
                )
                _sleep_interruptible(wait, should_cancel)
                continue
            detail = ""
            if exc.response is not None:
                detail = f" — {exc.response.text[:500]}"
            raise OpenRouterError(f"OpenRouter request failed: {exc}{detail}") from exc

    raise OpenRouterError(f"OpenRouter request failed after retries: {last_error}")


def _sleep_interruptible(seconds: float, should_cancel: Callable[[], bool] | None) -> None:
    end = time.monotonic() + max(0.0, seconds)
    while True:
        _check_cancel(should_cancel)
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def _retry_wait_seconds(response: requests.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(float(retry_after), 5.0)
            except ValueError:
                pass
    return OPENROUTER_RETRY_BASE_SEC * (2**attempt)
