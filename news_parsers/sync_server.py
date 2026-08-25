"""Синхронизация папки «Новости» с VPS (scp)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent

_JSONL_DAY_RE = re.compile(
    r"strategic_navigator_news_.*_custom_(\d{4}-\d{2}-\d{2})_\1\.jsonl$",
    re.IGNORECASE,
)


def load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(PROJECT_DIR / ".env")
    except ImportError:
        pass


def deploy_settings() -> tuple[str, str]:
    load_env()
    server = os.getenv("DEPLOY_SERVER", "").strip()
    remote = os.getenv("DEPLOY_REMOTE_PATH", "/root/strategic-navigator").strip().rstrip("/")
    if not server:
        raise SystemExit(
            "Задайте DEPLOY_SERVER в .env, например: DEPLOY_SERVER=root@82.38.65.160"
        )
    return server, remote


def _is_kallanish(path: Path) -> bool:
    if path.name.startswith("._") or path.name.startswith("."):
        return False
    return path.suffix.lower() == ".docx" and "kallanish" in path.name.lower()


def collect_sync_files(
    news_dir: Path,
    *,
    dates: set[str] | None = None,
    all_jsonl: bool = False,
    include_kallanish: bool = True,
) -> list[Path]:
    if not news_dir.is_dir():
        return []

    files: list[Path] = []
    for path in sorted(news_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".jsonl":
            if all_jsonl or _JSONL_DAY_RE.match(path.name):
                if dates:
                    match = _JSONL_DAY_RE.match(path.name)
                    if not match or match.group(1) not in dates:
                        continue
                files.append(path)
            continue
        if include_kallanish and _is_kallanish(path):
            files.append(path)

    return files


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def sync_files(
    files: list[Path],
    *,
    server: str,
    remote_path: str,
    dry_run: bool = False,
) -> int:
    if not files:
        print("Нечего синхронизировать.", flush=True)
        return 0

    remote_news = f"{remote_path}/Новости"
    if dry_run:
        print(f"DRY RUN → {server}:{remote_news}/")
        for path in files:
            print(f"  {path.name}")
        return len(files)

    _run(["ssh", server, f"mkdir -p {remote_news}"])
    for path in files:
        _run(["scp", str(path), f"{server}:{remote_news}/"])

    return len(files)


def sync_news_dir(
    news_dir: Path,
    *,
    server: str | None = None,
    remote_path: str | None = None,
    dates: set[str] | None = None,
    all_jsonl: bool = False,
    include_kallanish: bool = True,
    dry_run: bool = False,
) -> int:
    deploy_server, deploy_remote = deploy_settings()
    server = server or deploy_server
    remote_path = remote_path or deploy_remote

    files = collect_sync_files(
        news_dir,
        dates=dates,
        all_jsonl=all_jsonl,
        include_kallanish=include_kallanish,
    )
    count = sync_files(files, server=server, remote_path=remote_path, dry_run=dry_run)
    if count:
        print(f"Синхронизировано файлов: {count} → {server}:{remote_path}/Новости/", flush=True)
    return count
