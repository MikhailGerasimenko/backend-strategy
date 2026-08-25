from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.handlers import (
    general_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.middleware import RequestIDMiddleware
from web.app import app as navigator_app

# Основное приложение — API Стратегического навигатора
app: FastAPI = navigator_app
app.title = settings.app_name
app.description = "Стратегический навигатор — брифы, RAG-агент, парсинг"
app.version = settings.app_version
app.debug = settings.debug

app.add_middleware(RequestIDMiddleware)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

# SSS / k8s health: GET /api/v1/health
app.include_router(api_router, prefix="/api/v1")
