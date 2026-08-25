#!/usr/bin/env python3
"""Запуск веб-интерфейса для коллег."""

from __future__ import annotations

import argparse

from web.config import load_dotenv


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Стратегический навигатор — веб-интерфейс")
    parser.add_argument("--host", default="127.0.0.1", help="Адрес (0.0.0.0 — доступ в локальной сети)")
    parser.add_argument("--port", type=int, default=8080, help="Порт")
    args = parser.parse_args()

    import uvicorn

    print(f"Откройте в браузере: http://{args.host}:{args.port}/")
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
