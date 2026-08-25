from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
NEWS_DIR = PROJECT_DIR / "Новости"
SOURCES_PATH = PROJECT_DIR / "sources.json"
BRIEF_CATEGORIES_PATH = PROJECT_DIR / "source_brief_categories.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as _load

        _load(PROJECT_DIR / ".env")
    except ImportError:
        pass


def openrouter_configured() -> bool:
    load_dotenv()
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


def default_model() -> str:
    load_dotenv()
    from news_parsers.llm.config import BRIEF_MODEL

    return BRIEF_MODEL
