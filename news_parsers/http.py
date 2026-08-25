from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import compact_text


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Сильные маркеры: однозначная страница-заглушка → блок при любом размере ответа.
STRONG_BLOCK_MARKERS = (
    "access denied",
    "are you a robot",
    "verify you are human",
    "unusual traffic",
    "attention required! | cloudflare",
    "request unsuccessful. incapsula",
    "checking your browser before accessing",
)
# Слабые маркеры (captcha, cloudflare, forbidden и т.п.) часто встречаются в обычных
# страницах (скрипты reCAPTCHA, ссылки на CDN). Считаем блоком только если тело короткое.
WEAK_BLOCK_MARKERS = (
    "enable javascript",
    "captcha",
    "cloudflare",
    "forbidden",
)
WEAK_BLOCK_MAX_LEN = 2000


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    content: bytes
    final_url: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300 and not self.error

    @property
    def blocked(self) -> bool:
        if self.status_code in {401, 403, 451, 429}:
            return True
        sample = self.text[:5000].lower()
        if any(marker in sample for marker in STRONG_BLOCK_MARKERS):
            return True
        if len(self.text) <= WEAK_BLOCK_MAX_LEN and any(
            marker in sample for marker in WEAK_BLOCK_MARKERS
        ):
            return True
        return False


def build_proxies(proxy_url: str | None) -> dict[str, str] | None:
    if not proxy_url:
        return None
    proxy_url = proxy_url.strip()
    if not proxy_url:
        return None
    return {"http": proxy_url, "https": proxy_url}


def resolve_http_proxy(
    cli_proxy: str | None = None,
    config_proxy: str | None = None,
) -> str | None:
    """Общий HTTP(S)-прокси для парсинга сайтов и Telegram.

    Порядок: CLI → config → HTTP_PROXY / HTTPS_PROXY → PARSING_PROXY.
    В контуре по умолчанию: http://ar-proxy.severstal.severstalgroup.com:3128
    """
    for candidate in (
        cli_proxy,
        config_proxy,
        os.getenv("PARSING_PROXY"),
        os.getenv("HTTP_PROXY"),
        os.getenv("http_proxy"),
        os.getenv("HTTPS_PROXY"),
        os.getenv("https_proxy"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return None


def resolve_telegram_proxy(
    cli_proxy: str | None = None,
    config_proxy: str | None = None,
) -> str | None:
    """Прокси для Telegram: CLI → sources.json → TELEGRAM_PROXY → общий HTTP-прокси."""
    for candidate in (
        cli_proxy,
        config_proxy,
        os.getenv("TELEGRAM_PROXY"),
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return resolve_http_proxy()


class HttpClient:
    def __init__(
        self,
        timeout: int = 25,
        retries: int = 3,
        backoff_factor: float = 1.0,
        headers: Mapping[str, str] | None = None,
        proxy_url: str | None = None,
    ) -> None:
        self.timeout = timeout
        self.proxy_url = proxy_url
        self.session = requests.Session()
        proxies = build_proxies(proxy_url)
        if proxies:
            self.session.proxies.update(proxies)
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        merged_headers = dict(DEFAULT_HEADERS)
        if headers:
            merged_headers.update(headers)
        self.session.headers.update(merged_headers)

    def get(self, url: str, headers: Mapping[str, str] | None = None) -> FetchResult:
        try:
            response = self.session.get(url, timeout=self.timeout, headers=headers)
            return FetchResult(
                url=url,
                status_code=response.status_code,
                text=response.text or "",
                content=response.content or b"",
                final_url=response.url,
            )
        except requests.RequestException as exc:
            return FetchResult(
                url=url,
                status_code=0,
                text="",
                content=b"",
                final_url=url,
                error=compact_text(exc),
            )
