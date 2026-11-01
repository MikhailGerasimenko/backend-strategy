"""Загрузка страниц через headless Chromium (обход ngenix JS-challenge на MetalInfo)."""

from __future__ import annotations

import os
from typing import Mapping

from .http import FetchResult, resolve_http_proxy

_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_STEALTH_INIT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    "Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en-US', 'en']});"
    "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});"
)


def metalinfo_browser_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return os.getenv("METALINFO_USE_BROWSER", "1").lower() in ("1", "true", "yes")


def playwright_available() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _playwright_proxy() -> dict[str, str] | None:
    proxy = resolve_http_proxy()
    if not proxy:
        return None
    cfg: dict[str, str] = {"server": proxy}
    bypass = (os.getenv("NO_PROXY") or os.getenv("no_proxy") or "").strip()
    if bypass:
        cfg["bypass"] = bypass
    return cfg


def _chromium_launch_args() -> list[str]:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    # В K8s/Docker без privileged обычно нужен --no-sandbox.
    if os.getenv("PLAYWRIGHT_NO_SANDBOX", "1").lower() in ("1", "true", "yes"):
        args.append("--no-sandbox")
    return args


def _looks_like_challenge(html: str) -> bool:
    sample = (html or "")[:8000].lower()
    return (
        "js-challenge" in sample
        or "не смог пройти" in sample
        or "доступ ограничен" in sample
        or "checking your browser" in sample
    )


class BrowserFetcher:
    """Один браузер на прогон парсера — cookies сохраняются между страницами."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self._warmed = False

    def start(self) -> None:
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        launch_kwargs: dict = {
            "headless": self._headless,
            "args": _chromium_launch_args(),
        }
        proxy = _playwright_proxy()
        if proxy:
            launch_kwargs["proxy"] = proxy
        self._browser = self._playwright.chromium.launch(**launch_kwargs)
        self._context = self._browser.new_context(
            user_agent=_CHROME_UA,
            locale="ru-RU",
            viewport={"width": 1365, "height": 900},
            ignore_https_errors=True,
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
        self._warmed = False

    def __enter__(self) -> BrowserFetcher:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _warmup_metalinfo(self) -> None:
        """Первый заход на главную — ngenix ставит cookie, дальше listing открывается стабильнее."""
        if self._warmed or self._context is None:
            return
        page = self._context.new_page()
        try:
            page.goto(
                "https://www.metalinfo.ru/",
                wait_until="commit",
                timeout=60_000,
            )
            try:
                page.wait_for_load_state("domcontentloaded", timeout=20_000)
            except Exception:
                pass
            page.wait_for_timeout(5_000)
            self._warmed = True
        except Exception:
            # Warmup необязателен — listing всё равно попробуем.
            pass
        finally:
            page.close()

    def get(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        *,
        wait_selector: str | None = None,
        timeout_ms: int = 90_000,
        settle_ms: int = 8_000,
    ) -> FetchResult:
        if self._context is None:
            self.start()
        assert self._context is not None

        if "metalinfo.ru" in url:
            self._warmup_metalinfo()

        extra_headers = dict(headers or {})
        page = self._context.new_page()
        if extra_headers:
            page.set_extra_http_headers(extra_headers)

        try:
            # "commit" наступает сразу после ответа — не зависаем на антибот readyState=loading.
            page.goto(url, wait_until="commit", timeout=timeout_ms)
            selector = wait_selector or (
                "div.news-block" if "metalinfo.ru" in url else None
            )
            if selector:
                try:
                    page.wait_for_selector(selector, timeout=min(timeout_ms, 45_000))
                except Exception:
                    # JS-challenge ещё крутится — даём время и пробуем снова.
                    page.wait_for_timeout(settle_ms)
                    try:
                        page.wait_for_selector(selector, timeout=30_000)
                    except Exception:
                        pass
            else:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 20_000))
                except Exception:
                    pass
                page.wait_for_timeout(settle_ms)

            html = page.content()
            # Если всё ещё challenge — ещё одна пауза и перечитываем DOM.
            if _looks_like_challenge(html) and selector:
                page.wait_for_timeout(settle_ms)
                try:
                    page.wait_for_selector(selector, timeout=30_000)
                except Exception:
                    pass
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

        blocked = _looks_like_challenge(html)
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
        return fetcher.get(url, headers, wait_selector="div.news-block")
