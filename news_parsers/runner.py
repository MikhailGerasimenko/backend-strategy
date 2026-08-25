from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .database import save_to_postgres
from .filters import filter_items_by_keyword_blocks, filter_relevant_items, load_keyword_blocks
from .http import HttpClient, resolve_http_proxy, resolve_telegram_proxy
from .browser_fetch import BrowserFetcher, metalinfo_browser_enabled, playwright_available
from .models import NewsItem, ParserHealth
from .outputs import HEALTH_COLUMNS, NEWS_COLUMNS, write_csv, write_excel, write_jsonl
from .parsers import parse_source
from .periods import PeriodRange, apply_period_to_source, build_period_range, filter_items_by_period


def load_sources_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def source_uses_browser(source: dict[str, Any], use_metalinfo_browser: bool) -> bool:
    """Нужен ли headless-браузер для источника: MetalInfo или явный use_browser."""
    if source.get("parser") == "metalinfo":
        return use_metalinfo_browser
    return bool(source.get("use_browser"))


def load_relevance_filter_config(path: Path) -> dict[str, Any]:
    return load_sources_config(path).get("relevance_filter") or {}


def load_keyword_filter_config(path: Path) -> dict[str, Any]:
    return load_sources_config(path).get("keyword_filter") or {}


def resolve_keyword_blocks_path(sources_path: Path, cfg: dict[str, Any]) -> Path:
    return sources_path.parent / cfg.get("blocks_file", "keyword_blocks.json")


def load_sources(
    path: Path,
    *,
    include_web: bool = True,
    include_telegram: bool = True,
) -> list[dict[str, Any]]:
    data = load_sources_config(path)
    sources: list[dict[str, Any]] = []
    if include_web:
        sources.extend(
            source for source in data.get("sources", []) if source.get("enabled", True)
        )
    if include_telegram:
        sources.extend(expand_telegram_sources(data, path.parent))
    return sources


def expand_telegram_sources(data: dict[str, Any], base_dir: Path) -> list[dict[str, Any]]:
    telegram_cfg = data.get("telegram") or {}
    if not telegram_cfg.get("enabled", False):
        return []

    channels_path = base_dir / telegram_cfg.get("channels_file", "telegram_channels.json")
    if not channels_path.exists():
        return []

    with channels_path.open("r", encoding="utf-8") as file:
        channels_data = json.load(file)
    channels = channels_data.get("channels", [])
    category = telegram_cfg.get("category", "telegram")
    min_text_length = int(telegram_cfg.get("min_text_length", 30))
    expanded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for channel in channels:
        username = str(channel).strip().lstrip("@")
        if not username or username.lower() in seen:
            continue
        seen.add(username.lower())
        expanded.append(
            {
                "name": f"TG {username}",
                "parser": "telegram",
                "channel": username,
                "url": f"https://t.me/s/{username}",
                "category": category,
                "min_text_length": min_text_length,
                "enabled": True,
            }
        )
    try:
        from .custom_telegram import expand_custom_telegram_sources

        for extra in expand_custom_telegram_sources(min_text_length=min_text_length):
            username = str(extra.get("channel") or "").strip().lstrip("@")
            if not username or username.lower() in seen:
                continue
            seen.add(username.lower())
            expanded.append(extra)
    except Exception:
        pass
    return expanded


def run_all_sources(
    sources_path: Path,
    output_dir: Path,
    source_names: set[str] | None = None,
    exclude_source_names: set[str] | None = None,
    timeout: int = 15,
    retries: int = 1,
    smoke: bool = False,
    period: str = "day",
    since: str | None = None,
    until: str | None = None,
    days: int | None = None,
    max_pages: int | None = None,
    database_url: str | None = None,
    include_web: bool = True,
    include_telegram: bool = True,
    relevance_filter: bool = False,
    keyword_filter: bool = False,
    keyword_block: str | None = None,
    telegram_proxy: str | None = None,
    metalinfo_browser: bool | None = None,
) -> tuple[list[NewsItem], list[ParserHealth], dict[str, Path]]:
    relevance_filter_enabled = relevance_filter
    keyword_filter_enabled = keyword_filter
    sources_data = load_sources_config(sources_path)
    telegram_cfg = sources_data.get("telegram") or {}
    metalinfo_cfg = sources_data.get("metalinfo") or {}
    use_metalinfo_browser = metalinfo_browser_enabled(
        metalinfo_browser if metalinfo_browser is not None else metalinfo_cfg.get("use_browser"),
    )
    all_sources = load_sources(sources_path, include_web=include_web, include_telegram=include_telegram)
    selected_sources = [
        source for source in all_sources
        if (not source_names or source.get("name") in source_names)
        and (not exclude_source_names or source.get("name") not in exclude_source_names)
    ]
    needs_browser = any(
        source_uses_browser(source, use_metalinfo_browser) for source in selected_sources
    )
    browser_fetcher: BrowserFetcher | None = None
    if needs_browser:
        if playwright_available():
            browser_fetcher = BrowserFetcher()
            browser_fetcher.start()
            print("Headless Chromium (Playwright) включён для обхода блокировок/JS-challenge.", flush=True)
        else:
            print(
                "Browser mode включён, но playwright не установлен. "
                "Выполните: pip install playwright && python3 -m playwright install chromium",
                flush=True,
            )
    site_proxy = resolve_http_proxy()
    tg_proxy = resolve_telegram_proxy(telegram_proxy, telegram_cfg.get("proxy_url"))
    client = HttpClient(timeout=timeout, retries=retries, proxy_url=site_proxy)
    telegram_client = (
        HttpClient(timeout=timeout, retries=retries, proxy_url=tg_proxy)
        if tg_proxy
        else client
    )
    if site_proxy:
        print(f"HTTP proxy enabled: {mask_proxy_url(site_proxy)}", flush=True)
    if tg_proxy and tg_proxy != site_proxy:
        print(f"Telegram proxy enabled: {mask_proxy_url(tg_proxy)}", flush=True)
    period_range = build_period_range(period=period, since=since, until=until, days=days)
    all_items: list[NewsItem] = []
    health: list[ParserHealth] = []

    try:
        for source in selected_sources:
            source = apply_period_to_source(source, period_range, smoke=smoke, max_pages=max_pages)
            print(f"Running {source.get('name')}...", flush=True)
            source_client = telegram_client if source.get("parser") == "telegram" else client
            src_browser = (
                browser_fetcher
                if source_uses_browser(source, use_metalinfo_browser)
                else None
            )
            items, source_health = parse_source(
                source, source_client, browser_fetcher=src_browser,
            )
            filtered_items = filter_items_by_period(items, period_range)
            source_health = health_for_filtered_items(source_health, filtered_items, period_range)
            all_items.extend(filtered_items)
            health.append(source_health)
            print(
                f"{source_health.source}: {source_health.status}, "
                f"items={source_health.items}, errors={source_health.errors}",
                flush=True,
            )
    finally:
        if browser_fetcher is not None:
            browser_fetcher.close()

    if keyword_filter_enabled and relevance_filter_enabled:
        print(
            "Warning: keyword_filter and relevance_filter both enabled; using keyword_filter only.",
            flush=True,
        )
        relevance_filter_enabled = False

    if keyword_filter_enabled:
        keyword_cfg = load_keyword_filter_config(sources_path)
        blocks_path = resolve_keyword_blocks_path(sources_path, keyword_cfg)
        blocks = load_keyword_blocks(blocks_path)
        active_block = keyword_block or keyword_cfg.get("default_block")
        before_count = len([item for item in all_items if item.status == "ok"])
        all_items, keyword_stats = filter_items_by_keyword_blocks(
            all_items,
            blocks,
            active_block_id=active_block,
        )
        health = refresh_health_after_relevance(health, all_items, period_range, keyword_stats)
        block_label = active_block or "any"
        per_block = keyword_stats.get("per_block", {})
        print(
            f"Keyword filter ({block_label}): "
            f"{keyword_stats['passed']}/{keyword_stats['checked']} passed, "
            f"dropped={keyword_stats['dropped']} (from {before_count} ok items)",
            flush=True,
        )
        if per_block:
            for block_id, count in per_block.items():
                print(f"  - {block_id}: {count}", flush=True)

    elif relevance_filter_enabled:
        relevance_cfg = load_relevance_filter_config(sources_path)
        window_size = int(relevance_cfg.get("window_size", 15))
        before_count = len([item for item in all_items if item.status == "ok"])
        all_items, relevance_stats = filter_relevant_items(all_items, window_size=window_size)
        health = refresh_health_after_relevance(health, all_items, period_range, relevance_stats)
        print(
            "Relevance filter: "
            f"{relevance_stats['passed']}/{relevance_stats['checked']} passed, "
            f"dropped={relevance_stats['dropped']} "
            f"(from {before_count} collected ok items)",
            flush=True,
        )

    paths = write_outputs(output_dir, all_items, health, period_range)
    if database_url:
        run_id = save_to_postgres(
            database_url,
            period_range,
            all_items,
            health,
            paths,
            relevance_filter_enabled=relevance_filter_enabled or keyword_filter_enabled,
        )
        from .database import count_news_by_date

        day = period_range.end
        stored = count_news_by_date(database_url, day)
        print(
            f"Saved to PostgreSQL: parser_runs.id={run_id}, "
            f"daily_news on {day}: {stored} rows (date, title, topic, news_text, link)",
            flush=True,
        )
    return all_items, health, paths


def write_outputs(
    output_dir: Path,
    items: list[NewsItem],
    health: list[ParserHealth],
    period_range: PeriodRange | None = None,
) -> dict[str, Path]:
    stamp = datetime.today().strftime("%Y%m%d")
    output_dir.mkdir(parents=True, exist_ok=True)

    news_rows = [item.to_dict() for item in items]
    health_rows = [row.to_dict() for row in health]
    paths = {
        "jsonl": output_dir / f"strategic_navigator_news_{file_suffix(stamp, period_range)}.jsonl",
        "csv": output_dir / f"strategic_navigator_news_{file_suffix(stamp, period_range)}.csv",
        "health_csv": output_dir / f"parser_health_{file_suffix(stamp, period_range)}.csv",
        "health_jsonl": output_dir / f"parser_health_{file_suffix(stamp, period_range)}.jsonl",
        "excel": output_dir / f"metallurgy_news_{file_suffix(stamp, period_range)}.xlsx",
    }

    write_jsonl(paths["jsonl"], news_rows)
    write_jsonl(paths["health_jsonl"], health_rows)
    write_csv(paths["csv"], news_rows, NEWS_COLUMNS)
    write_csv(paths["health_csv"], health_rows, HEALTH_COLUMNS)
    write_excel(paths["excel"], items, health)
    return paths


def refresh_health_after_relevance(
    health: list[ParserHealth],
    items: list[NewsItem],
    period_range: PeriodRange,
    relevance_stats: dict[str, int],
) -> list[ParserHealth]:
    items_by_source: dict[str, list[NewsItem]] = {}
    for item in items:
        items_by_source.setdefault(item.source, []).append(item)

    suffix = (
        f"; relevance={relevance_stats['passed']}/{relevance_stats['checked']} "
        f"(dropped {relevance_stats['dropped']})"
    )
    updated: list[ParserHealth] = []
    for report in health:
        source_items = items_by_source.get(report.source, [])
        refreshed = health_for_filtered_items(report, source_items, period_range)
        refreshed.message = compact_health_message(refreshed.message, suffix)
        updated.append(refreshed)
    return updated


def compact_health_message(message: str, suffix: str) -> str:
    if suffix.strip("; ") in message:
        return message
    return f"{message}{suffix}" if message else suffix.strip("; ")


def health_for_filtered_items(
    health: ParserHealth,
    items: list[NewsItem],
    period_range: PeriodRange,
) -> ParserHealth:
    errors = sum(1 for item in items if item.status != "ok" or item.error)
    status = health.status
    if not items:
        status = "no_items"
    elif items and all(item.status == "blocked" for item in items):
        status = "blocked"
    elif items and all(item.status == "network_error" for item in items):
        status = "network_error"
    elif items and all(item.status == "parse_changed" for item in items):
        status = "parse_changed"
    elif any(item.status == "ok" for item in items):
        status = "ok"
    message = health.message or f"period={period_range.name} {period_range.start.isoformat()}..{period_range.end.isoformat()}"
    return ParserHealth(source=health.source, status=status, items=len(items), errors=errors, message=message)


def mask_proxy_url(proxy_url: str) -> str:
    if "@" not in proxy_url:
        return proxy_url
    creds, host = proxy_url.rsplit("@", 1)
    scheme = creds.split("://", 1)[0] + "://"
    return f"{scheme}***:***@{host}"


def file_suffix(stamp: str, period_range: PeriodRange | None) -> str:
    if not period_range:
        return stamp
    return f"{stamp}_{period_range.name}_{period_range.start.isoformat()}_{period_range.end.isoformat()}"
