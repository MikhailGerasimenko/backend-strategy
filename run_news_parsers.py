from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from news_parsers.runner import (
    load_keyword_filter_config,
    load_relevance_filter_config,
    run_all_sources,
)


PROJECT_DIR = Path(__file__).resolve().parent


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_DIR / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run strategic navigator news parsers.")
    parser.add_argument(
        "--sources",
        type=Path,
        default=PROJECT_DIR / "sources.json",
        help="Path to sources.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "Новости",
        help="Directory for Excel, CSV, JSONL and health reports.",
    )
    parser.add_argument(
        "--source",
        action="append",
        dest="sources_filter",
        help="Run only the named source. Can be passed multiple times.",
    )
    parser.add_argument(
        "--exclude-source",
        action="append",
        dest="exclude_sources",
        help="Не парсить указанный источник (по имени). Можно несколько раз.",
    )
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout per request in seconds.")
    parser.add_argument("--retries", type=int, default=1, help="HTTP retries per request.")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a fast health check with limited pages/items per source.",
    )
    parser.add_argument(
        "--period",
        choices=("day", "week", "month"),
        default="day",
        help="Parsing period: current day, last 7 days, or last 30 days.",
    )
    parser.add_argument(
        "--days",
        type=int,
        help="Parse the last N days. Overrides --period unless --since is provided.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Maximum listing pages per paginated source.",
    )
    parser.add_argument(
        "--since",
        help="Custom period start date in YYYY-MM-DD format. Overrides --period start.",
    )
    parser.add_argument(
        "--until",
        help="Custom period end date in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL connection URL. Can also be provided via DATABASE_URL.",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="Save parsed news to PostgreSQL (also auto if DATABASE_URL is set).",
    )
    parser.add_argument(
        "--no-save-db",
        action="store_true",
        help="Do not write to PostgreSQL even if DATABASE_URL is set.",
    )
    parser.add_argument(
        "--skip-telegram",
        action="store_true",
        help="Parse only web sources from sources.json, skip Telegram channels.",
    )
    parser.add_argument(
        "--telegram-only",
        action="store_true",
        help="Parse only Telegram channels from telegram_channels.json.",
    )
    parser.add_argument(
        "--relevance-filter",
        action="store_true",
        help="Keep only news matching prefix+suffix relevance rules (see sources.json).",
    )
    parser.add_argument(
        "--no-relevance-filter",
        action="store_true",
        help="Disable relevance filter even if enabled in sources.json.",
    )
    parser.add_argument(
        "--keyword-filter",
        action="store_true",
        help="Filter news by keyword blocks from keyword_blocks.json.",
    )
    parser.add_argument(
        "--no-keyword-filter",
        action="store_true",
        help="Disable keyword filter even if enabled in sources.json.",
    )
    parser.add_argument(
        "--keyword-block",
        choices=("block_1", "block_2", "block_3", "block_4"),
        default=None,
        help="Use only one keyword block (blocks do not combine).",
    )
    parser.add_argument(
        "--telegram-proxy",
        default=None,
        help="HTTP(S) proxy for Telegram only (t.me). Or set TELEGRAM_PROXY in env.",
    )
    parser.add_argument(
        "--index-rag",
        action="store_true",
        help="После парсинга сразу проиндексировать новости в RAG-базу (pgvector) "
        "с догрузкой полного текста статей. Нужен DATABASE_URL.",
    )
    parser.add_argument(
        "--no-index-fetch-full",
        action="store_true",
        help="При --index-rag не догружать полный текст по URL (быстро, текст из парсера).",
    )
    parser.add_argument(
        "--sync-server",
        action="store_true",
        help="После парсинга скопировать JSONL/Kallanish на VPS (DEPLOY_SERVER в .env).",
    )
    parser.add_argument(
        "--metalinfo-browser",
        action="store_true",
        help="MetalInfo через headless Chromium (Playwright), обход ngenix JS-challenge.",
    )
    parser.add_argument(
        "--no-metalinfo-browser",
        action="store_true",
        help="Не использовать браузер для MetalInfo (только requests).",
    )
    return parser.parse_args()


def main() -> None:
    load_env()
    args = parse_args()
    if args.skip_telegram and args.telegram_only:
        raise SystemExit("Use either --skip-telegram or --telegram-only, not both.")
    if args.relevance_filter and args.no_relevance_filter:
        raise SystemExit("Use either --relevance-filter or --no-relevance-filter, not both.")
    selected_sources = set(args.sources_filter) if args.sources_filter else None
    excluded_sources = set(args.exclude_sources) if args.exclude_sources else None
    env_db_url = os.getenv("DATABASE_URL", "").strip()
    auto_save = os.getenv("SAVE_NEWS_TO_DB", "true").lower() in ("1", "true", "yes")
    want_save_db = args.save_db or (auto_save and env_db_url and not args.no_save_db)
    database_url = args.database_url or (env_db_url if want_save_db else None)
    if want_save_db and not database_url:
        raise SystemExit("Use --database-url or set DATABASE_URL to save results to PostgreSQL.")
    if want_save_db:
        print("PostgreSQL: saving news to database (daily_news view).", flush=True)
    include_web = not args.telegram_only
    include_telegram = not args.skip_telegram
    relevance_cfg = load_relevance_filter_config(args.sources)
    keyword_cfg = load_keyword_filter_config(args.sources)
    relevance_filter = args.relevance_filter or (
        relevance_cfg.get("enabled", False) and not args.no_relevance_filter
    )
    keyword_filter = args.keyword_filter or (
        keyword_cfg.get("enabled", False) and not args.no_keyword_filter
    )
    metalinfo_browser: bool | None = None
    if args.metalinfo_browser:
        metalinfo_browser = True
    elif args.no_metalinfo_browser:
        metalinfo_browser = False
    items, health, paths = run_all_sources(
        args.sources,
        args.output_dir,
        selected_sources,
        exclude_source_names=excluded_sources,
        timeout=args.timeout,
        retries=args.retries,
        smoke=args.smoke,
        period=args.period,
        since=args.since,
        until=args.until,
        days=args.days,
        max_pages=args.max_pages,
        database_url=database_url,
        include_web=include_web,
        include_telegram=include_telegram,
        relevance_filter=relevance_filter,
        keyword_filter=keyword_filter,
        keyword_block=args.keyword_block,
        telegram_proxy=args.telegram_proxy,
        metalinfo_browser=metalinfo_browser,
    )

    print("")
    print(f"Collected news: {len(items)}")
    for report in health:
        print(f"- {report.source}: {report.status}, {report.items} items, {report.errors} errors")
    print("")
    print("Output files:")
    for label, path in paths.items():
        print(f"- {label}: {path}")

    if args.index_rag:
        rag_db_url = args.database_url or env_db_url
        if not rag_db_url:
            raise SystemExit("--index-rag требует DATABASE_URL (env или --database-url).")
        from news_parsers.rag.news_store import index_news_items

        print("")
        print("Индексация новостей в RAG-базу (pgvector)…", flush=True)
        fallback_date: date | None = None
        if args.since and args.until and args.since == args.until:
            fallback_date = date.fromisoformat(args.since)
        stats = index_news_items(
            rag_db_url,
            [it.to_dict() for it in items],
            indexed_by="parser",
            fallback_date=fallback_date,
            fetch_full=not args.no_index_fetch_full,
            log=lambda msg: print(f"  {msg}", flush=True),
        )
        print(
            f"RAG: новостей={stats['documents']}, чанков={stats['chunks']}, "
            f"полный текст догружен={stats.get('full_text_fetched', 0)}",
            flush=True,
        )

    auto_sync = os.getenv("SYNC_NEWS_TO_SERVER", "").lower() in ("1", "true", "yes")
    if args.sync_server or auto_sync:
        import shutil

        if not shutil.which("ssh"):
            print(
                "Синхронизация пропущена: ssh не найден (парсинг уже на сервере). "
                "На VPS отключите SYNC_NEWS_TO_SERVER в .env.",
                flush=True,
            )
        else:
            from news_parsers.sync_server import sync_news_dir

            dates: set[str] | None = None
            if args.since and args.until and args.since == args.until:
                dates = {args.since}
            elif args.since:
                dates = {args.since}
            print("")
            print("Синхронизация «Новости» на сервер…", flush=True)
            sync_news_dir(args.output_dir, dates=dates)


if __name__ == "__main__":
    main()
