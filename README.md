# Strategic Navigator — backend

FastAPI-бэкенд «Стратегического навигатора» на шаблоне SSS (Python/Poetry/Gunicorn/Helm).

В контуре:

- **векторы** → корпоративный Qdrant (`QDRANT_URL`), не pgvector
- **парсинг** → корп-прокси `http://ar-proxy.severstal.severstalgroup.com:3128`
- **фронт** → отдельный репозиторий SPA
- **секреты** → Vault/ESO (`OPENROUTER_API_KEY`, `AUTH_SECRET`, опционально `QDRANT_API_KEY`)

## Стек

| Слой | Технология |
|------|------------|
| API | FastAPI + Gunicorn |
| RAG | Qdrant (`rag_news`, `brief_index`) |
| LLM | OpenRouter |
| Парсинг | requests через HTTP(S)_PROXY / PARSING_PROXY |
| CI/CD | SSS `python.yml` |
| Образ | `devops-public/corp-images/python:3.13-debian` |

## Локально

```bash
poetry install --no-root
pip install -r requirements-navigator.txt
cp .env.example .env
# задайте OPENROUTER_API_KEY и при необходимости QDRANT_URL
make run
# http://127.0.0.1:8000/api/v1/health
# http://127.0.0.1:8000/login
```

## Переменные

См. `.env.example`. Ключевые:

- `HTTP_PROXY` / `HTTPS_PROXY` / `PARSING_PROXY` — корп-прокси
- `QDRANT_URL` — URL сервиса Qdrant в кластере
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
