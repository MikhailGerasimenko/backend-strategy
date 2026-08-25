"""Загрузка страниц через headless Chromium (обход ngenix JS-challenge на MetalInfo)."""

from __future__ import annotations

import os
from typing import Mapping

from .http import FetchResult

_CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_STEALTH_INIT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)


def metalinfo_browser_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return explicit
    return os.getenv("METALINFO_USE_BROWSER", "").lower() in ("1", "true", "yes")


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


class BrowserFetcher:
    """Один браузер на прогон парсера — cookies сохраняются между страницами."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._context = None

    def start(self) -> None:
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self._headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._context = self._browser.new_context(
            user_agent=_CHROME_UA,
            locale="ru-RU",
            viewport={"width": 1280, "height": 800},
        )
        self._context.add_init_script(_STEALTH_INIT)

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def __enter__(self) -> BrowserFetcher:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        *,
        wait_selector: str | None = None,
        timeout_ms: int = 60_000,
        settle_ms: int = 8_000,
    ) -> FetchResult:
        if self._context is None:
            self.start()
        assert self._context is not None

        extra_headers = dict(headers or {})
        page = self._context.new_page()
        if extra_headers:
            page.set_extra_http_headers(extra_headers)

        try:
            # "commit" наступает сразу после получения ответа — не зависаем на сайтах,
            # которые искусственно держат readyState=loading (анти-бот скрипты).
            page.goto(url, wait_until="commit", timeout=timeout_ms)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=min(timeout_ms, 30_000))
                except Exception:
                    pass
            else:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 20_000))
                except Exception:
                    pass
                # Даём JS-челленджу/скриптам время дорисовать контент.
                page.wait_for_timeout(settle_ms)
            html = page.content()
            final_url = page.url
        except Exception as exc:
            return FetchResult(
                url=url,
                status_code=0,
                text="",
                content=b"",
                final_url=url,
                error=str(exc),
            )
        finally:
            page.close()

        blocked = (
            "js-challenge" in html[:4000].lower()
            or "не смог пройти" in html.lower()
        )
        status = 503 if blocked else 200
        encoded = html.encode("utf-8", errors="replace")
        return FetchResult(
            url=url,
            status_code=status,
            text=html,
            content=encoded,
            final_url=final_url,
            error="ngenix_js_challenge" if blocked else "",
        )


def fetch_metalinfo_via_browser(url: str, headers: Mapping[str, str] | None = None) -> FetchResult:
    """Одноразовый запрос (медленнее — каждый раз новый браузер)."""
    with BrowserFetcher() as fetcher:
        return fetcher.get(url, headers)
