#!/usr/bin/env python3
"""Архивация новостей старше года в S3 и очистка БД.

По умолчанию только считает (--dry-run). Удаление — с флагом --apply,
и только после успешной загрузки в архивный бакет.

  python archive_old_news.py
  python archive_old_news.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

from news_parsers.news_archive import archive_cutoff, archive_postgres, archive_qdrant
from news_parsers.object_storage import load_storage_settings, put_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description="Архив новостей старше года в S3")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Загрузить в бакет и удалить из БД. Без флага — только подсчёт.",
    )
    parser.add_argument("--days", type=int, default=None, help="Старше скольки дней (по умолчанию 365)")
    args = parser.parse_args()
    cutoff = archive_cutoff(days=args.days)
    settings = load_storage_settings()
    report: dict = {
        "cutoff": cutoff.isoformat(),
        "apply": args.apply,
        "archive_bucket": settings.archive.name if settings.archive else None,
        "files_bucket": settings.files.name if settings.files else None,
    }
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        report["postgres"] = archive_postgres(database_url, cutoff, apply=args.apply)
    else:
        report["postgres"] = {"skipped": "DATABASE_URL не задан"}
    report["qdrant"] = archive_qdrant(cutoff, apply=args.apply)
    if args.apply and settings.files is not None:
        key = f"archive-runs/{date.today().isoformat()}.json"
        report["files_manifest"] = put_bytes(
            settings.files,
            key,
            json.dumps(report, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
