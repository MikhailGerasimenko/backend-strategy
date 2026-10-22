# Strategic Navigator — backend

FastAPI-бэкенд «Стратегического навигатора» на шаблоне SSS (Python/Poetry/Gunicorn/Helm).

В контуре:

- **векторы** → PostgreSQL + pgvector (`DATABASE_URL`), как в исходном проекте
- **парсинг** → корп-прокси `http://ar-proxy.severstal.severstalgroup.com:3128`
- **фронт** → отдельный репозиторий SPA
- **секреты** → Vault/ESO (`DATABASE_URL`, `OPENROUTER_API_KEY`, `AUTH_SECRET`)

## Стек

| Слой | Технология |
|------|------------|
| API | FastAPI + Gunicorn |
| RAG | PostgreSQL + pgvector (`rag_news_*`, `brief_index_*`) |
| LLM | OpenRouter |
| Парсинг | requests через HTTP(S)_PROXY / PARSING_PROXY |
| CI/CD | SSS `python.yml` |
| Образ | `devops-public/corp-images/python:3.13-debian` |

## Локально

```bash
poetry install --no-root
pip install -r requirements-navigator.txt
cp .env.example .env
# задайте DATABASE_URL и OPENROUTER_API_KEY
make run
# http://127.0.0.1:8000/api/v1/health
# http://127.0.0.1:8000/login
```

## Переменные

См. `.env.example`. Ключевые:

- `HTTP_PROXY` / `HTTPS_PROXY` / `PARSING_PROXY` — корп-прокси
- `DATABASE_URL` — корпоративный Postgres с pgvector
- `OPENROUTER_API_KEY`, `AUTH_SECRET` — секреты

## Структура

```
app/                 # SSS shell (health, middleware, settings)
web/                 # API навигатора (брифы, агент, auth)
news_parsers/        # парсеры, LLM, RAG
config/              # gunicorn
.helm/               # деплой в k8s
```

## Health

- `GET /api/v1/health` — k8s probes
- `GET /api/health` — статус OpenRouter / векторного стора (после входа)
