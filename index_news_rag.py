#!/usr/bin/env python3
"""Индексация СЫРЫХ новостей (с полным текстом) в pgvector для RAG-брифов и ИИ-агента.

Примеры:
  # Проиндексировать день из последнего JSONL за эту дату в папке Новости
  python3 index_news_rag.py --date 2026-06-25

  # Явный файл
  python3 index_news_rag.py --jsonl "Новости/strategic_navigator_news_..._2026-06-25_2026-06-25.jsonl"

  # Без догрузки полного текста по URL (быстро, только то, что собрал парсер)
  python3 index_news_rag.py --date 2026-06-25 --no-fetch-full
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

from news_parsers.rag.news_store import index_news_jsonl

DEFAULT_NEWS_DIR = Path("Новости")


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _find_jsonl_for_day(news_dir: Path, day: date) -> Path | None:
    pattern = f"strategic_navigator_news_*_{day.isoformat()}_{day.isoformat()}.jsonl"
    matches = sorted(
        news_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Индексация сырых новостей в pgvector (RAG).")
    parser.add_argument("--date", help="Дата новостей YYYY-MM-DD (ищет JSONL в папке Новости).")
    parser.add_argument("--jsonl", help="Явный путь к JSONL-файлу выгрузки новостей.")
    parser.add_argument(
        "--news-dir", default=str(DEFAULT_NEWS_DIR), help="Папка с выгрузками (по умолчанию Новости)."
    )
    parser.add_argument("--database-url", help="PostgreSQL URL (или env DATABASE_URL).")
    parser.add_argument("--indexed-by", default="cli", help="Кто запустил индексацию.")
    parser.add_argument(
        "--no-fetch-full",
        action="store_true",
        help="Не догружать полный текст по URL (использовать только текст из парсера).",
    )
    args = parser.parse_args()

    database_url = args.database_url or os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("Ошибка: укажите --database-url или env DATABASE_URL.", file=sys.stderr)
        return 2

    fallback_date: date | None = None
    if args.jsonl:
        jsonl_path = Path(args.jsonl)
    elif args.date:
        fallback_date = _parse_date(args.date)
        jsonl_path = _find_jsonl_for_day(Path(args.news_dir), fallback_date)
        if jsonl_path is None:
            print(
                f"Ошибка: не найден JSONL за {args.date} в {args.news_dir}.",
                file=sys.stderr,
            )
            return 2
        print(f"Файл: {jsonl_path}")
    else:
        print("Ошибка: укажите --date или --jsonl.", file=sys.stderr)
        return 2

    stats = index_news_jsonl(
        database_url,
        jsonl_path,
        indexed_by=args.indexed_by,
        fallback_date=fallback_date,
        fetch_full=not args.no_fetch_full,
        log=print,
    )
    print(
        f"Итог: новостей={stats['documents']}, чанков={stats['chunks']}, "
        f"полный текст догружен={stats.get('full_text_fetched', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
