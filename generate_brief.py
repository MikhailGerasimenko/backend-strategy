#!/usr/bin/env python3
"""Генерация аналитического комментария через OpenRouter."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path

from news_parsers.database import save_brief
from news_parsers.docx_brief import convert_markdown_file_to_docx, write_brief_docx, brief_docx_filename
from news_parsers.llm.brief import (
    BriefContext,
    generate_brief_comment,
    load_brief_input_from_db,
    load_brief_input_from_jsonl,
)
from news_parsers.llm.config import BRIEF_MODEL
from news_parsers.periods import build_period_range


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "Новости"


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_DIR / ".env")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate strategic brief comment via OpenRouter.")
    parser.add_argument("--period", choices=("day", "week", "month"), default="week")
    parser.add_argument("--days", type=int, help="Last N days instead of --period.")
    parser.add_argument("--since", help="Period start YYYY-MM-DD.")
    parser.add_argument("--until", help="Period end YYYY-MM-DD.")
    parser.add_argument(
        "--jsonl",
        type=Path,
        help="Load news from JSONL instead of PostgreSQL.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL URL. Default: DATABASE_URL env.",
    )
    parser.add_argument(
        "--relevant-only",
        action="store_true",
        help="Use only news with non-empty relevance_match / keyword match.",
    )
    parser.add_argument(
        "--keyword-block",
        choices=("block_1", "block_2", "block_3", "block_4"),
        default=None,
        help="Include only news tagged with this keyword block.",
    )
    parser.add_argument("--model", default=None, help=f"OpenRouter model (default: {BRIEF_MODEL}).")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for markdown brief file.",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="Save brief to PostgreSQL table briefs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show how many news will be sent, do not call LLM.",
    )
    parser.add_argument(
        "--indicators-xlsx",
        type=Path,
        default=PROJECT_DIR / "Список показателей.xlsx",
        help="Excel file with indicators for the final impact section.",
    )
    parser.add_argument(
        "--format-pdf",
        type=Path,
        default=PROJECT_DIR / "news2026-03-19.pdf",
        help="Reference PDF (news2026) for comment structure and tone.",
    )
    parser.add_argument(
        "--format",
        choices=("docx", "md", "both"),
        default="docx",
        help="Output format: Word (docx), markdown, or both.",
    )
    parser.add_argument(
        "--from-md",
        type=Path,
        help="Convert existing markdown brief to Word without calling LLM.",
    )
    parser.add_argument(
        "--single-pass",
        action="store_true",
        help="One LLM request for all news (may hit rate limits with 300+ items). "
        "Default for large batches: chunked generation.",
    )
    parser.add_argument(
        "--kallanish-docx",
        type=Path,
        default=None,
        help="Word-файл Kallanish для промпта. По умолчанию: *kallanish*.docx в --output-dir.",
    )
    parser.add_argument(
        "--no-kallanish",
        action="store_true",
        help="Не подключать материалы Kallanish к генерации брифа.",
    )
    return parser.parse_args()


def check_openrouter_key() -> None:
    import os

    if os.getenv("OPENROUTER_API_KEY", "").strip():
        return
    example = PROJECT_DIR / ".env.example"
    hint = ""
    if example.exists() and "sk-or-" in example.read_text(encoding="utf-8"):
        hint = (
            "\nПохоже, ключ указан в .env.example. Скопируйте его в .env:\n"
            "  OPENROUTER_API_KEY=sk-or-...\n"
            "(файл .env не коммитится в git)"
        )
    raise SystemExit(
        "OPENROUTER_API_KEY не задан. Добавьте ключ в файл .env в корне проекта." + hint
    )


def main() -> None:
    load_env()
    args = parse_args()

    if args.from_md:
        docx_path = convert_markdown_file_to_docx(args.from_md)
        print(f"Word document saved: {docx_path}")
        return

    check_openrouter_key()
    period_range = build_period_range(
        period=args.period,
        since=args.since,
        until=args.until,
        days=args.days,
    )

    if args.jsonl:
        brief_input = load_brief_input_from_jsonl(
            args.jsonl,
            period_range,
            keyword_block=args.keyword_block,
            relevant_only=args.relevant_only,
        )
        source_label = str(args.jsonl)
    else:
        database_url = args.database_url or os.getenv("DATABASE_URL")
        if not database_url:
            raise SystemExit(
                "Provide --jsonl or set DATABASE_URL / --database-url to load news from PostgreSQL."
            )
        brief_input = load_brief_input_from_db(
            database_url,
            period_range,
            relevant_only=args.relevant_only,
            keyword_block=args.keyword_block,
        )
        source_label = "PostgreSQL"

    print(f"News source: {source_label}")
    print(f"Period: {period_range.start} .. {period_range.end} ({period_range.name})")
    print(f"News items: {len(brief_input.news)}")
    if len(brief_input.news) > 200:
        print(
            "Note: large batch — prompt body per news is shortened automatically. "
            "Set BRIEF_MAX_NEWS_IN_PROMPT>0 to cap if needed.",
        )
    if args.keyword_block:
        print(f"Filter: keyword_block={args.keyword_block}")
    if args.relevant_only:
        print("Filter: relevant_only=True")

    if not brief_input.news:
        raise SystemExit("No news to analyze. Run parsers with --save-db or pass --jsonl.")

    brief_context = BriefContext(
        indicators_path=args.indicators_xlsx,
        format_pdf_path=args.format_pdf,
        kallanish_docx_path=args.kallanish_docx,
        news_dir=args.output_dir,
        include_kallanish=not args.no_kallanish,
    )
    if not args.indicators_xlsx.exists():
        print(f"Warning: indicators file not found: {args.indicators_xlsx}", flush=True)
    if not args.format_pdf.exists():
        print(f"Warning: format PDF not found: {args.format_pdf}", flush=True)

    if not args.no_kallanish:
        from news_parsers.llm.kallanish_docx import resolve_kallanish_path

        k_path = resolve_kallanish_path(
            explicit_path=args.kallanish_docx,
            news_dir=args.output_dir,
            include=True,
        )
        if k_path:
            print(f"Kallanish: {k_path} ({k_path.stat().st_size // 1024} KB)")
        elif args.kallanish_docx:
            print(f"Warning: Kallanish file not found: {args.kallanish_docx}", flush=True)
        else:
            print(
                f"Kallanish: не найден в {args.output_dir} "
                "(ожидается *kallanish*.docx — бриф будет только по новостям)",
                flush=True,
            )

    if args.dry_run:
        from news_parsers.llm.brief import build_user_prompt

        prompt, news_in_prompt = build_user_prompt(brief_input, brief_context)
        print(f"News in prompt: {news_in_prompt} / {len(brief_input.news)}")
        print(f"Prompt size: {len(prompt)} characters (~{len(prompt) // 4} tokens)")
        return

    print(f"Calling OpenRouter model: {args.model or BRIEF_MODEL}...", flush=True)
    content, metadata = generate_brief_comment(
        brief_input,
        model=args.model,
        context=brief_context,
        project_dir=PROJECT_DIR,
        single_pass=args.single_pass,
    )

    print(f"Generation mode: {metadata.get('generation_mode', '?')} ({metadata.get('api_calls', 1)} API calls)")
    print(
        f"News in LLM prompt: {metadata.get('news_in_prompt', '?')} / {metadata.get('news_count', '?')}"
    )
    if metadata.get("news_omitted_from_prompt"):
        print(
            f"Warning: {metadata['news_omitted_from_prompt']} news not sent to LLM "
            "(set BRIEF_MAX_NEWS_IN_PROMPT=0 in .env to send all).",
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.today().strftime("%Y%m%d")
    base_name = f"brief_{stamp}_{period_range.name}_{period_range.start}_{period_range.end}"
    metadata["generated_at"] = datetime.now().isoformat(timespec="seconds")

    docx_path = args.output_dir / brief_docx_filename(period_range.end)
    md_path = args.output_dir / f"{base_name}.md"

    if args.format in {"docx", "both"}:
        write_brief_docx(
            docx_path,
            content,
            report_date=period_range.end.strftime("%d.%m.%Y"),
            period_label=(
                f"{period_range.start.strftime('%d.%m.%Y')} — "
                f"{period_range.end.strftime('%d.%m.%Y')}"
            ),
        )
        print(f"Word document saved: {docx_path}")

    if args.format in {"md", "both"}:
        md_path.write_text(format_markdown(content, period_range, metadata), encoding="utf-8")
        print(f"Markdown saved: {md_path}")

    manifest_path = docx_path.with_suffix(".sources.json")
    manifest = build_sources_manifest(brief_input, source_label, period_range, metadata)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Sources manifest: {manifest_path}")
    print_sources_summary(manifest)

    if args.save_db:
        database_url = args.database_url or os.getenv("DATABASE_URL")
        if not database_url:
            raise SystemExit("--save-db requires DATABASE_URL or --database-url.")
        brief_id = save_brief(
            database_url,
            content=content,
            period_range=period_range,
            model=metadata["model"],
            prompt_version=metadata["prompt_version"],
            news_count=metadata["news_count"],
            metadata={
                **metadata,
                "docx_path": str(docx_path),
                "markdown_path": str(md_path) if args.format in {"md", "both"} else "",
            },
        )
        print(f"Saved to PostgreSQL: briefs.id={brief_id}")

    print("")
    print("--- Preview ---")
    preview = content[:1500] + ("…" if len(content) > 1500 else "")
    print(preview)


def build_sources_manifest(brief_input, input_label: str, period_range, metadata: dict) -> dict:
    by_source: Counter[str] = Counter()
    items_preview: list[dict] = []
    for item in brief_input.news:
        if hasattr(item, "source"):
            source = item.source
            title = item.title
            date_value = item.published_date.isoformat() if item.published_date else ""
            url = item.url
            relevance = item.relevance_match
        else:
            source = item.get("source", "")
            title = item.get("title", "")
            date_value = item.get("date", "")
            url = item.get("url", "")
            relevance = item.get("relevance_match", "")
        by_source[source] += 1
        items_preview.append(
            {
                "source": source,
                "date": date_value,
                "title": title[:200],
                "url": url,
                "relevance_match": relevance or "",
            }
        )
    return {
        "input": input_label,
        "period": {
            "name": period_range.name,
            "start": period_range.start.isoformat(),
            "end": period_range.end.isoformat(),
        },
        "model": metadata.get("model", ""),
        "news_count": len(brief_input.news),
        "sources_count": len(by_source),
        "by_source": dict(by_source.most_common()),
        "items": items_preview,
    }


def print_sources_summary(manifest: dict) -> None:
    print("")
    print("Источники в промпте LLM:")
    for source, count in manifest.get("by_source", {}).items():
        print(f"  - {source}: {count} новостей")
    print(f"Всего: {manifest.get('news_count', 0)} новостей, {manifest.get('sources_count', 0)} источников")


def format_markdown(content: str, period_range, metadata: dict) -> str:
    header = (
        f"# Аналитический комментарий (формат news2026)\n\n"
        f"**Период:** {period_range.start} — {period_range.end} ({period_range.name})  \n"
        f"**Модель:** {metadata.get('model', '')}  \n"
        f"**Новостей в основе:** {metadata.get('news_count', 0)}  \n"
        f"**Источников:** {metadata.get('sources_count', '')}  \n"
        f"**Показатели:** {metadata.get('indicators_file', '')}  \n"
        f"**Образец:** {metadata.get('format_reference', '')}  \n"
        f"**Kallanish:** {metadata.get('kallanish_file', '') or '—'}  \n"
        f"**Сгенерировано:** {datetime.now().isoformat(timespec='seconds')}\n\n"
        f"---\n\n"
    )
    return header + content


if __name__ == "__main__":
    main()
