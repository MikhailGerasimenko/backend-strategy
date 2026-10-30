from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from news_parsers.docx_brief import write_map_digests_docx
from news_parsers.llm.openrouter import OpenRouterError
from news_parsers.llm.brief_cleanup import DEFAULT_CLEANUP_SYSTEM_PROMPT

logger = logging.getLogger("strategic_navigator.web")
from news_parsers.llm.period_brief import (
    MAP_SYSTEM_PROMPT as DEFAULT_MAP_SYSTEM_PROMPT,
    REDUCE_INSTRUCTIONS_PROMPT as DEFAULT_REDUCE_INSTRUCTIONS_PROMPT,
)
from news_parsers.rag.store import BriefIndexError, DuplicateDocumentError
from news_parsers.custom_telegram import (
    add_custom_channel,
    list_custom_channels,
    list_known_topic_categories,
    parse_telegram_channel,
    source_name_for_channel,
)

from .auth import (
    add_session_middleware,
    clear_session_user,
    get_session_user,
    login_user,
    register_and_login,
    require_admin,
    require_user,
    require_local_auth,
    keycloak_enabled,
)
from .keycloak import router as keycloak_router, logout_response
from .config import STATIC_DIR, default_model, openrouter_configured
from .jobs import job_manager
from .prompt_overrides import (
    clear_prompt_override,
    get_prompt_override,
    resolve_prompt,
    resolve_prompt_parts,
    save_prompt_override,
)
from .prompt_split import join_brief_prompt, split_brief_prompt
from .services import (
    day_status,
    delete_news_data_for_day,
    get_default_system_prompt,
    kallanish_status,
    list_available_news_dates,
    list_system_prompt_variants,
    news_data_overview,
    run_brief_for_day,
    run_parse_for_day,
    save_news_jsonl_upload,
    upload_kallanish,
)
from .source_management import (
    disable_managed_source,
    list_managed_sources,
    restore_managed_source,
)
from .services_rag import (
    ask_news_agent,
    cleanup_preview_brief,
    export_period_brief_docx,
    export_period_brief_json,
    get_default_agent_system_prompt,
    get_default_weekly_system_prompt,
    index_document_upload,
    index_documents_upload,
    index_kallanish_to_rag,
    index_pdf_upload,
    list_brief_news_sources,
    list_rag_attachments,
    list_weekly_system_prompt_variants,
    pgvector_configured,
    rag_period_coverage,
    remove_rag_attachment,
    run_weekly_brief,
)
from .user_store import registration_status


app = FastAPI(
    title="Стратегический навигатор",
    description="Тестирование системного промпта и генерация брифа",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(keycloak_router)

PUBLIC_PAGES = {"/login", "/register"}
PUBLIC_API_PREFIXES = ("/api/auth", "/api/v1/health")


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static"):
        return await call_next(request)
    if path in PUBLIC_PAGES or any(path.startswith(prefix) for prefix in PUBLIC_API_PREFIXES):
        return await call_next(request)

    user = get_session_user(request)
    if user:
        return await call_next(request)

    if path.startswith("/api/"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Требуется вход в систему."},
        )
    if path == "/":
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


class LoginBody(BaseModel):
    login: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)


class RegisterBody(BaseModel):
    login: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=5, max_length=128)


class PipelineRequest(BaseModel):
    date: str = Field(..., description="Дата в формате YYYY-MM-DD")
    system_prompt: str = Field(..., min_length=50)
    model: Optional[str] = None
    relevant_only: bool = True
    include_kallanish: bool = True
    skip_parse: bool = False
    brief_kind: str = Field(
        default="full",
        description="Тип брифа: full | market | corporate (влияет на имя Word-файла)",
    )


class ParseRequest(BaseModel):
    date: str


class WeeklyBriefRequest(BaseModel):
    period_start: str = Field(..., description="Начало периода YYYY-MM-DD")
    period_end: str = Field(..., description="Конец периода YYYY-MM-DD")
    system_prompt: str = Field(..., min_length=50)
    map_system_prompt: Optional[str] = Field(
        default=None,
        description="Промпт фактографа (map-этап); пусто/null — дефолт с сервера",
        max_length=20000,
    )
    reduce_instructions: Optional[str] = Field(
        default=None,
        description="Инструкции склейки (reduce-этап); пусто/null — дефолт с сервера",
        max_length=20000,
    )
    model: Optional[str] = None
    brief_kind: str = Field(
        default="full",
        description="Тип: full | market | corporate",
    )
    sources: Optional[list[str]] = Field(
        default=None,
        description="Имена источников для брифа; null — все",
    )
    attachment_ids: Optional[list[int]] = Field(
        default=None,
        description="ID документов PDF/Word в бриф; null — все, [] — ни одного",
    )


class AgentAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    prior_sources: Optional[list[dict]] = None
    history: Optional[list[dict]] = None
    system_prompt: Optional[str] = Field(
        default=None,
        description="Системный промпт агента; пусто/null — дефолт с сервера",
        max_length=20000,
    )


class ExportBriefRequest(BaseModel):
    content: str = Field(..., min_length=50, description="Отредактированный текст брифа")


class CleanupBriefRequest(BaseModel):
    content: str = Field(..., min_length=50, description="Текст брифа для clean-up")
    system_prompt: Optional[str] = Field(
        default=None,
        description="Системный промпт для LLM clean-up; пусто/null — дефолт с сервера",
        max_length=20000,
    )


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD.") from exc


def _weekly_cleanup_variant(brief_kind: str) -> str:
    return f"cleanup::{brief_kind or 'full'}"


# Промпты map/reduce одни на все типы брифа (служебные этапы не зависят от типа).
MAP_PROMPT_VARIANT = "map"
REDUCE_PROMPT_VARIANT = "reduce"


def _yesterday_msk() -> date:
    try:
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    except Exception:
        today = date.today()
    return today - timedelta(days=1)


@app.get("/login")
async def login_page(request: Request) -> FileResponse:
    if get_session_user(request):
        return RedirectResponse(url="/", status_code=303)
    if keycloak_enabled():
        return FileResponse(STATIC_DIR / "sso.html")
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/register")
async def register_page(request: Request) -> FileResponse:
    if keycloak_enabled():
        return RedirectResponse("/login", status_code=303)
    if get_session_user(request):
        return RedirectResponse(url="/", status_code=303)
    return FileResponse(STATIC_DIR / "register.html")


@app.get("/weekly")
async def weekly_page_redirect(request: Request, user: dict = Depends(require_user)) -> RedirectResponse:
    return RedirectResponse(url="/", status_code=303)


class CustomTelegramSourceBody(BaseModel):
    url: str = Field(..., min_length=3, max_length=300)
    topic_category: str = Field(..., min_length=1, max_length=80)
    parse_yesterday: bool = True


class ManagedSourceBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class PromptOverrideBody(BaseModel):
    variant: str = Field(..., min_length=1, max_length=80)
    prompt: Optional[str] = Field(
        default=None,
        description="Полный склеенный промпт (обратная совместимость)",
        min_length=50,
        max_length=40000,
    )
    system_prompt: Optional[str] = Field(
        default=None,
        description="Часть до ОБЯЗАТЕЛЬНАЯ СТРУКТУРА",
        max_length=40000,
    )
    user_prompt: Optional[str] = Field(
        default=None,
        description="Часть от ОБЯЗАТЕЛЬНАЯ СТРУКТУРА включительно",
        max_length=40000,
    )


def _resolve_override_prompt_parts(body: PromptOverrideBody) -> dict[str, str]:
    """Склеивает system+user (или берёт prompt) и возвращает части для хранения."""
    if body.system_prompt is not None or body.user_prompt is not None:
        system = (body.system_prompt or "").strip()
        user = (body.user_prompt or "").strip()
        joined = join_brief_prompt(system, user)
        if len(joined.strip()) < 50:
            raise ValueError("Промпт слишком короткий.")
        return {
            "prompt": joined,
            "system_prompt": system,
            "user_prompt": user,
        }
    prompt = (body.prompt or "").strip()
    if len(prompt) < 50:
        raise ValueError("Промпт слишком короткий.")
    parts = split_brief_prompt(prompt)
    return {
        "prompt": prompt,
        "system_prompt": parts["system_prompt"],
        "user_prompt": parts["user_prompt"],
    }


def _resolve_override_prompt_text(body: PromptOverrideBody) -> str:
    """Склеивает system+user либо берёт готовый prompt."""
    return _resolve_override_prompt_parts(body)["prompt"]


@app.get("/sources")
async def custom_sources_page(request: Request, user: dict = Depends(require_user)) -> FileResponse:
    response = FileResponse(STATIC_DIR / "sources.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/agent")
async def agent_page(request: Request, user: dict = Depends(require_user)) -> FileResponse:
    response = FileResponse(STATIC_DIR / "agent.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.get("/")
async def index(request: Request, user: dict = Depends(require_user)) -> FileResponse:
    response = FileResponse(STATIC_DIR / "weekly.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.post("/api/auth/register")
async def api_register(body: RegisterBody, request: Request) -> dict:
    user = register_and_login(request, body.login, body.password)
    return {"ok": True, "user": user}


@app.post("/api/auth/login")
async def api_login(body: LoginBody, request: Request) -> dict:
    user = login_user(request, body.login, body.password)
    return {"ok": True, "user": user}


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    if keycloak_enabled():
        return await logout_response(request)
    clear_session_user(request)
    return {"ok": True}


@app.get("/api/auth/me")
async def api_me(user: dict = Depends(require_user)) -> dict:
    return {"user": user}


@app.get("/api/auth/check-login")
async def api_check_login(login: str) -> dict:
    require_local_auth()
    return registration_status(login)


@app.get("/api/auth/accounts-overview")
async def api_accounts_overview() -> dict:
    require_local_auth()
    from .user_store import list_allowed_accounts, registration_status

    accounts = []
    for item in list_allowed_accounts():
        login = str(item.get("login", ""))
        status = registration_status(login)
        accounts.append(
            {
                "login": status["login"],
                "full_name": status.get("full_name") or login,
                "registered": status["registered"],
                "can_register": status["can_register"],
            }
        )
    return {"accounts": accounts}


@app.get("/api/health")
async def health(user: dict = Depends(require_user)) -> dict:
    return {
        "status": "ok",
        "openrouter_configured": openrouter_configured(),
        "pgvector_configured": pgvector_configured(),
        "vector_backend": __import__("news_parsers.rag.vector_backend", fromlist=["vector_backend"]).vector_backend(),
        "default_model": default_model(),
        "user": user.get("login"),
        "role": user.get("role", "user"),
        "is_admin": bool(user.get("is_admin")),
    }


@app.get("/api/system-prompts")
async def system_prompts(user: dict = Depends(require_user)) -> dict:
    return {"variants": list_system_prompt_variants()}


@app.get("/api/default-prompt")
async def default_prompt(
    variant: str = "full",
    user: dict = Depends(require_user),
) -> dict:
    prompt, customized = resolve_prompt(
        "daily",
        str(user.get("login") or ""),
        variant,
        get_default_system_prompt(variant),
    )
    return {"variant": variant, "prompt": prompt, "customized": customized}


@app.post("/api/default-prompt")
async def save_default_prompt(
    body: PromptOverrideBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        prompt = _resolve_override_prompt_text(body)
        save_prompt_override("daily", str(user.get("login") or ""), body.variant, prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "variant": body.variant, "customized": True}


@app.delete("/api/default-prompt")
async def reset_default_prompt(
    variant: str,
    user: dict = Depends(require_user),
) -> dict:
    removed = clear_prompt_override("daily", str(user.get("login") or ""), variant)
    return {"ok": True, "variant": variant, "customized": False, "removed": removed}


@app.get("/api/weekly/system-prompts")
async def weekly_system_prompts(user: dict = Depends(require_user)) -> dict:
    return {"variants": list_weekly_system_prompt_variants()}


@app.get("/api/weekly/default-prompt")
async def weekly_default_prompt(
    variant: str = "full",
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    user: dict = Depends(require_user),
) -> dict:
    start = _parse_day(period_start) if period_start else None
    end = _parse_day(period_end) if period_end else None
    if start and not end:
        end = start
    if end and not start:
        start = end
    fallback = get_default_weekly_system_prompt(
        variant,
        period_start=start,
        period_end=end,
    )
    parts, customized = resolve_prompt_parts(
        "weekly",
        str(user.get("login") or ""),
        variant,
        fallback,
    )
    return {
        "variant": variant,
        "prompt": parts["prompt"],
        "system_prompt": parts["system_prompt"],
        "user_prompt": parts["user_prompt"],
        "customized": customized,
    }


@app.post("/api/weekly/default-prompt")
async def save_weekly_default_prompt(
    body: PromptOverrideBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        parts = _resolve_override_prompt_parts(body)
        save_prompt_override(
            "weekly",
            str(user.get("login") or ""),
            body.variant,
            parts["prompt"],
            system_prompt=parts["system_prompt"],
            user_prompt=parts["user_prompt"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "variant": body.variant, "customized": True}


@app.delete("/api/weekly/default-prompt")
async def reset_weekly_default_prompt(
    variant: str,
    user: dict = Depends(require_user),
) -> dict:
    removed = clear_prompt_override("weekly", str(user.get("login") or ""), variant)
    return {"ok": True, "variant": variant, "customized": False, "removed": removed}


@app.get("/api/weekly/cleanup-prompt")
async def weekly_cleanup_prompt(
    brief_kind: str = "full",
    user: dict = Depends(require_user),
) -> dict:
    variant = _weekly_cleanup_variant(brief_kind)
    prompt, customized = resolve_prompt(
        "weekly",
        str(user.get("login") or ""),
        variant,
        DEFAULT_CLEANUP_SYSTEM_PROMPT,
    )
    return {"brief_kind": brief_kind, "variant": variant, "prompt": prompt, "customized": customized}


@app.post("/api/weekly/cleanup-prompt")
async def save_weekly_cleanup_prompt(
    body: PromptOverrideBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        prompt = _resolve_override_prompt_text(body)
        save_prompt_override("weekly", str(user.get("login") or ""), body.variant, prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "variant": body.variant, "customized": True}


@app.delete("/api/weekly/cleanup-prompt")
async def reset_weekly_cleanup_prompt(
    brief_kind: str,
    user: dict = Depends(require_user),
) -> dict:
    variant = _weekly_cleanup_variant(brief_kind)
    removed = clear_prompt_override("weekly", str(user.get("login") or ""), variant)
    return {"ok": True, "variant": variant, "customized": False, "removed": removed}


@app.get("/api/weekly/map-prompt")
async def weekly_map_prompt(user: dict = Depends(require_user)) -> dict:
    prompt, customized = resolve_prompt(
        "weekly",
        str(user.get("login") or ""),
        MAP_PROMPT_VARIANT,
        DEFAULT_MAP_SYSTEM_PROMPT,
    )
    return {"variant": MAP_PROMPT_VARIANT, "prompt": prompt, "customized": customized}


@app.post("/api/weekly/map-prompt")
async def save_weekly_map_prompt(
    body: PromptOverrideBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        prompt = _resolve_override_prompt_text(body)
        save_prompt_override(
            "weekly",
            str(user.get("login") or ""),
            MAP_PROMPT_VARIANT,
            prompt,
            plain=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "variant": MAP_PROMPT_VARIANT, "customized": True}


@app.delete("/api/weekly/map-prompt")
async def reset_weekly_map_prompt(user: dict = Depends(require_user)) -> dict:
    removed = clear_prompt_override("weekly", str(user.get("login") or ""), MAP_PROMPT_VARIANT)
    return {"ok": True, "variant": MAP_PROMPT_VARIANT, "customized": False, "removed": removed}


@app.get("/api/weekly/reduce-prompt")
async def weekly_reduce_prompt(user: dict = Depends(require_user)) -> dict:
    prompt, customized = resolve_prompt(
        "weekly",
        str(user.get("login") or ""),
        REDUCE_PROMPT_VARIANT,
        DEFAULT_REDUCE_INSTRUCTIONS_PROMPT,
    )
    return {"variant": REDUCE_PROMPT_VARIANT, "prompt": prompt, "customized": customized}


@app.post("/api/weekly/reduce-prompt")
async def save_weekly_reduce_prompt(
    body: PromptOverrideBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        prompt = _resolve_override_prompt_text(body)
        save_prompt_override(
            "weekly",
            str(user.get("login") or ""),
            REDUCE_PROMPT_VARIANT,
            prompt,
            plain=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "variant": REDUCE_PROMPT_VARIANT, "customized": True}


@app.delete("/api/weekly/reduce-prompt")
async def reset_weekly_reduce_prompt(user: dict = Depends(require_user)) -> dict:
    removed = clear_prompt_override("weekly", str(user.get("login") or ""), REDUCE_PROMPT_VARIANT)
    return {"ok": True, "variant": REDUCE_PROMPT_VARIANT, "customized": False, "removed": removed}


@app.get("/api/rag/period-coverage")
async def api_rag_period_coverage(
    period_start: str,
    period_end: str,
    brief_kind: str = "full",
    sources: Optional[str] = None,
    user: dict = Depends(require_user),
) -> dict:
    selected = [s.strip() for s in (sources or "").split(",") if s.strip()] or None
    return rag_period_coverage(
        _parse_day(period_start),
        _parse_day(period_end),
        brief_kind,
        sources=selected,
    )


@app.get("/api/rag/attachments")
async def api_rag_list_attachments(
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    user: dict = Depends(require_user),
) -> dict:
    start = _parse_day(period_start) if period_start and period_start.strip() else None
    end = _parse_day(period_end) if period_end and period_end.strip() else None
    return list_rag_attachments(period_start=start, period_end=end)


@app.delete("/api/rag/attachments/{document_id}")
async def api_rag_delete_attachment(
    document_id: int,
    user: dict = Depends(require_user),
) -> dict:
    if not pgvector_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан.")
    try:
        return remove_rag_attachment(document_id)
    except BriefIndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/custom-sources")
async def api_list_custom_sources(user: dict = Depends(require_user)) -> dict:
    managed = list_managed_sources()
    return {
        "channels": list_custom_channels(),
        "sources": managed["sources"],
        "disabled": managed["disabled"],
        "categories": list_known_topic_categories(),
    }


@app.post("/api/managed-sources/disable")
async def api_disable_managed_source(
    body: ManagedSourceBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        row = disable_managed_source(
            body.name,
            disabled_by=str(user.get("login") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "source": row, "kept_in_rag": True}


@app.post("/api/managed-sources/restore")
async def api_restore_managed_source(
    body: ManagedSourceBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        row = restore_managed_source(
            body.name,
            restored_by=str(user.get("login") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "source": row}


@app.post("/api/custom-sources")
async def api_add_custom_source(
    body: CustomTelegramSourceBody,
    user: dict = Depends(require_user),
) -> dict:
    try:
        row = add_custom_channel(
            url=body.url,
            topic_category=body.topic_category,
            added_by=str(user.get("login") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    parse_job_id = None
    if body.parse_yesterday:
        day = _yesterday_msk()
        source_name = source_name_for_channel(row["channel"])

        def work(job) -> None:
            result = run_parse_for_day(day, job.append_log, source_names={source_name})
            job.result = result

        job = job_manager.submit("parse-custom-source", work)
        parse_job_id = job.id
    return {
        "ok": True,
        "channel": row,
        "parse_job_id": parse_job_id,
        "categories": list_known_topic_categories(),
    }


@app.delete("/api/custom-sources/{channel}")
async def api_delete_custom_source(
    channel: str,
    user: dict = Depends(require_user),
) -> dict:
    try:
        username = parse_telegram_channel(channel)
        removed = disable_managed_source(
            source_name_for_channel(username),
            disabled_by=str(user.get("login") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "channel": removed, "kept_in_rag": True}


@app.post("/api/custom-sources/{channel}/parse")
async def api_parse_custom_source(
    channel: str,
    date_str: Optional[str] = None,
    user: dict = Depends(require_user),
) -> dict:
    try:
        username = parse_telegram_channel(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    day = _parse_day(date_str) if date_str else _yesterday_msk()
    source_name = source_name_for_channel(username)

    def work(job) -> None:
        result = run_parse_for_day(day, job.append_log, source_names={source_name})
        job.result = result

    job = job_manager.submit("parse-custom-source", work)
    return {"job_id": job.id, "source": source_name, "date": day.isoformat()}


@app.get("/api/rag/period-sources")
async def api_rag_period_sources(
    period_start: str,
    period_end: str,
    user: dict = Depends(require_user),
) -> dict:
    return list_brief_news_sources(_parse_day(period_start), _parse_day(period_end))


@app.get("/api/agent/default-prompt")
async def agent_default_prompt(user: dict = Depends(require_user)) -> dict:
    return {"prompt": get_default_agent_system_prompt()}


@app.post("/api/agent/ask")
async def api_agent_ask(
    body: AgentAskRequest,
    user: dict = Depends(require_user),
) -> dict:
    if not openrouter_configured():
        raise HTTPException(status_code=503, detail="Не настроен OPENROUTER_API_KEY")
    if not pgvector_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан для RAG.")
    start = _parse_day(body.period_start) if body.period_start else None
    end = _parse_day(body.period_end) if body.period_end else None
    try:
        return await asyncio.to_thread(
            ask_news_agent,
            body.question,
            period_start=start,
            period_end=end,
            prior_sources=body.prior_sources,
            history=body.history,
            system_prompt=body.system_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — БД/сеть: отдать читаемую причину, а не пустой 500
        logger.exception("agent_ask failed")
        raise HTTPException(
            status_code=500, detail=f"Ошибка агента: {type(exc).__name__}: {exc}"
        ) from exc


@app.post("/api/rag/upload-document")
async def api_rag_upload_document(
    date_str: str = Form(...),
    period_end: str = Form(""),
    document_type: str = Form(""),
    title: str = Form(""),
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
) -> dict:
    if not pgvector_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан.")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Выберите файл.")
    content = await file.read()
    end = _parse_day(period_end) if period_end.strip() else None
    try:
        return await asyncio.to_thread(
            index_document_upload,
            content,
            file.filename,
            brief_date=_parse_day(date_str),
            period_end=end,
            document_type=document_type.strip(),
            indexed_by=str(user.get("login", "user")),
            title=title,
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, BriefIndexError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OpenRouterError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ошибка эмбеддингов (OpenRouter): {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка индексации документа: {type(exc).__name__}: {exc}",
        ) from exc


@app.post("/api/rag/upload-documents")
async def api_rag_upload_documents(
    date_str: str = Form(...),
    period_end: str = Form(""),
    document_type: str = Form(""),
    files: list[UploadFile] = File(...),
    user: dict = Depends(require_user),
) -> dict:
    if not pgvector_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан.")
    if not files:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один файл.")

    items: list[tuple[bytes, str]] = []
    for upload in files:
        if not upload.filename:
            continue
        items.append((await upload.read(), upload.filename))
    if not items:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один файл.")

    end = _parse_day(period_end) if period_end.strip() else None
    try:
        return await asyncio.to_thread(
            index_documents_upload,
            items,
            brief_date=_parse_day(date_str),
            period_end=end,
            document_type=document_type.strip(),
            indexed_by=str(user.get("login", "user")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка пакетной индексации: {type(exc).__name__}: {exc}",
        ) from exc


@app.post("/api/rag/upload-pdf")
async def api_rag_upload_pdf(
    date_str: str = Form(...),
    period_end: str = Form(""),
    brief_kind: str = Form("full"),
    title: str = Form(""),
    file: UploadFile = File(...),
    user: dict = Depends(require_user),
) -> dict:
    if not pgvector_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан.")
    if not file.filename:
        raise HTTPException(status_code=400, detail="Выберите PDF.")
    content = await file.read()
    end = _parse_day(period_end) if period_end.strip() else None
    try:
        return await asyncio.to_thread(
            index_pdf_upload,
            content,
            file.filename,
            brief_date=_parse_day(date_str),
            period_end=end,
            brief_kind=brief_kind,
            indexed_by=str(user.get("login", "user")),
            title=title,
        )
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ValueError, BriefIndexError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OpenRouterError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Ошибка эмбеддингов (OpenRouter): {exc}",
        ) from exc


@app.get("/api/day-status")
async def api_day_status(date_str: str, user: dict = Depends(require_user)) -> dict:
    return day_status(_parse_day(date_str))


@app.get("/api/news/dates")
async def api_news_dates(user: dict = Depends(require_user)) -> dict:
    return {"dates": list_available_news_dates()}


@app.post("/api/news/upload")
async def api_news_upload(
    date_str: str = Form(...),
    file: UploadFile = File(...),
    user: dict = Depends(require_admin),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Выберите файл.")
    content = await file.read()
    try:
        return save_news_jsonl_upload(_parse_day(date_str), content, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/data")
async def api_news_data(date_str: str, user: dict = Depends(require_admin)) -> dict:
    return news_data_overview(_parse_day(date_str))


@app.delete("/api/news/data")
async def api_delete_news_data(
    date_str: str,
    include_briefs: bool = True,
    user: dict = Depends(require_admin),
) -> dict:
    try:
        return delete_news_data_for_day(_parse_day(date_str), include_briefs=include_briefs)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/kallanish")
async def api_kallanish_status(user: dict = Depends(require_user)) -> dict:
    return kallanish_status()


@app.post("/api/kallanish/upload")
async def api_kallanish_upload(
    file: UploadFile = File(...),
    date_str: str = Form(""),
    user: dict = Depends(require_admin),
) -> dict:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Выберите файл.")
    content = await file.read()
    brief_date = _parse_day(date_str) if date_str.strip() else None
    try:
        return await asyncio.to_thread(
            upload_kallanish,
            content,
            file.filename,
            brief_date=brief_date,
            indexed_by=str(user.get("login", "admin")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DuplicateDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OpenRouterError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Файл сохранён, но индексация в RAG не удалась: {exc}",
        ) from exc
    except BriefIndexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/rag/reindex-kallanish")
async def api_rag_reindex_kallanish(
    date_str: str = Form(""),
    user: dict = Depends(require_user),
) -> dict:
    if not pgvector_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан.")
    brief_date = _parse_day(date_str) if date_str.strip() else date.today()
    try:
        return await asyncio.to_thread(
            index_kallanish_to_rag,
            brief_date=brief_date,
            period_end=brief_date,
            indexed_by=str(user.get("login", "user")),
        )
    except BriefIndexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, user: dict = Depends(require_user)) -> dict:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/map-digests")
async def list_job_map_digests(job_id: str, user: dict = Depends(require_user)) -> dict:
    """Сводка по map-батчам брифа (без текста дайджестов)."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    items = [
        {k: v for k, v in item.items() if k != "content"}
        for item in job.map_digests()
        if isinstance(item, dict)
    ]
    return {"job_id": job.id, "status": job.status.value, "total": len(items), "items": items}


@app.get("/api/jobs/{job_id}/map-digests.docx")
async def download_job_map_digests(
    job_id: str, user: dict = Depends(require_user)
) -> Response:
    """Все дайджесты фактографа одним Word."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    digests = [item for item in job.map_digests() if isinstance(item, dict)]
    if not digests:
        raise HTTPException(status_code=404, detail="Дайджесты батчей для этой задачи не сохранены")
    result = job.result or {}

    def _day_label(raw: str) -> str:
        parts = raw.split("-")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            year, month, day = parts
            return f"{day}.{month}.{year}"
        return raw

    start = str(result.get("period_start") or "").strip()
    end = str(result.get("period_end") or "").strip()
    start_label = _day_label(start) if start else ""
    end_label = _day_label(end) if end else ""
    if start_label and end_label and start != end:
        period_label = f"{start_label} — {end_label}"
        stem = f"Фактограф {start_label}-{end_label}"
    elif start_label:
        period_label = start_label
        stem = f"Фактограф {start_label}"
    else:
        period_label = None
        stem = "Фактограф"
    payload = write_map_digests_docx(digests, period_label=period_label)
    filename = f"{stem}.docx"
    ascii_fallback = filename.encode("ascii", "ignore").decode() or "factographer.docx"
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": disposition},
    )


@app.get("/api/jobs/{job_id}/map-digests/{index}")
async def get_job_map_digest(
    job_id: str, index: int, user: dict = Depends(require_user)
) -> dict:
    """Полный текст дайджеста одного map-батча (index — с 1)."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    digests = job.map_digests()
    if not digests:
        raise HTTPException(status_code=404, detail="Дайджесты батчей для этой задачи не сохранены")
    if index < 1 or index > len(digests):
        raise HTTPException(
            status_code=404, detail=f"Батч {index} не найден (всего {len(digests)})"
        )
    item = digests[index - 1]
    return {"job_id": job.id, "total": len(digests), **item}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, user: dict = Depends(require_user)) -> dict:
    job = job_manager.cancel(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if job.status.value in ("completed", "failed", "cancelled"):
        return {
            "ok": False,
            "job_id": job.id,
            "status": job.status.value,
            "detail": "Задача уже завершена",
        }
    return {
        "ok": True,
        "job_id": job.id,
        "status": job.status.value,
        "cancel_requested": True,
        "detail": "Остановка запрошена. Текущий запрос к модели доработает, затем генерация прервётся.",
    }


@app.get("/api/jobs/{job_id}/download")
async def download_brief(job_id: str, user: dict = Depends(require_user)) -> FileResponse:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if job.status.value != "completed" or not job.docx_path:
        raise HTTPException(status_code=400, detail="Файл ещё не готов")
    path = Path(job.docx_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден на диске")
    filename = str((job.result or {}).get("docx_filename") or path.name)
    ascii_fallback = filename.encode("ascii", "ignore").decode() or "brief.docx"
    disposition = (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": disposition},
    )


def _attachment_disposition(filename: str) -> str:
    ascii_fallback = filename.encode("ascii", "ignore").decode() or "brief.bin"
    return (
        f"attachment; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )


@app.get("/api/jobs/{job_id}/download-json")
async def download_brief_json(job_id: str, user: dict = Depends(require_user)) -> FileResponse:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if job.status.value != "completed" or not job.json_path:
        raise HTTPException(status_code=400, detail="JSON ещё не готов")
    path = Path(job.json_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="JSON-файл не найден на диске")
    return FileResponse(
        path,
        media_type="application/json",
        headers={
            "Content-Disposition": _attachment_disposition(
                str((job.result or {}).get("json_filename") or path.name)
            )
        },
    )


@app.post("/api/jobs/{job_id}/export-json")
async def export_brief_json(
    job_id: str,
    body: ExportBriefRequest,
    user: dict = Depends(require_user),
) -> dict:
    """Собрать JSON месячного брифа из отредактированного текста."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if job.status.value != "completed" or not job.result:
        raise HTTPException(status_code=400, detail="Бриф ещё не готов к экспорту")

    result = job.result
    period_start = result.get("period_start")
    period_end = result.get("period_end")
    if not period_start or not period_end:
        raise HTTPException(status_code=400, detail="В задаче нет периода для экспорта")

    try:
        exported = export_period_brief_json(
            body.content,
            period_start=_parse_day(str(period_start)),
            period_end=_parse_day(str(period_end)),
            brief_kind=str(result.get("brief_kind") or ""),
            metadata=result.get("metadata") if isinstance(result.get("metadata"), dict) else None,
            storage_key=job_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job.json_path = exported["json_path"]
    job.result = {**result, **exported, "content": body.content}
    job.append_log(
        f"JSON собран: {exported['json_filename']} ({exported.get('slides', 0)} слайдов)"
    )
    return {
        "json_filename": exported["json_filename"],
        "download_url": f"/api/jobs/{job_id}/download-json",
        "slides": exported.get("slides", 0),
        "schema": exported.get("schema"),
        "content_chars": exported["content_chars"],
    }


@app.post("/api/jobs/{job_id}/export-docx")
async def export_brief_docx(
    job_id: str,
    body: ExportBriefRequest,
    user: dict = Depends(require_user),
) -> dict:
    """Собрать Word из отредактированного текста завершённой задачи брифа."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if job.status.value != "completed" or not job.result:
        raise HTTPException(status_code=400, detail="Бриф ещё не готов к экспорту")

    result = job.result
    period_start = result.get("period_start")
    period_end = result.get("period_end")
    if not period_start or not period_end:
        raise HTTPException(status_code=400, detail="В задаче нет периода для экспорта")

    try:
        exported = export_period_brief_docx(
            body.content,
            period_start=_parse_day(str(period_start)),
            period_end=_parse_day(str(period_end)),
            brief_kind=str(result.get("brief_kind") or "full"),
            storage_key=job_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job.docx_path = exported["docx_path"]
    job.result = {**result, **exported, "content": body.content}
    job.append_log(f"Word-документ собран: {exported['docx_filename']}")
    return {
        "docx_filename": exported["docx_filename"],
        "download_url": f"/api/jobs/{job_id}/download",
        "content_chars": exported["content_chars"],
    }


@app.post("/api/jobs/{job_id}/cleanup-brief")
async def cleanup_brief_preview(
    job_id: str,
    body: CleanupBriefRequest,
    user: dict = Depends(require_user),
) -> dict:
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    result = job.result or {}
    brief_kind = str(result.get("brief_kind") or "full")
    try:
        cleaned = await asyncio.to_thread(
            cleanup_preview_brief,
            body.content,
            brief_kind=brief_kind,
            system_prompt=body.system_prompt,
            log=job.append_log,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    metadata = dict(result.get("metadata") or {})
    metadata["manual_cleanup_pass"] = bool(cleaned.get("cleanup_pass"))
    metadata["manual_cleanup_at"] = datetime.now().isoformat(timespec="seconds")
    metadata["content_chars"] = int(cleaned.get("content_chars") or len(cleaned["content"]))
    job.result = {**result, "content": cleaned["content"], "metadata": metadata}
    return {
        "content": cleaned["content"],
        "cleanup_pass": bool(cleaned.get("cleanup_pass")),
        "content_chars": int(cleaned.get("content_chars") or len(cleaned["content"])),
    }


@app.post("/api/jobs/parse")
async def start_parse(body: ParseRequest, user: dict = Depends(require_admin)) -> dict:
    day = _parse_day(body.date)

    def work(job) -> None:
        result = run_parse_for_day(day, job.append_log)
        job.result = result

    job = job_manager.submit("parse", work)
    return {"job_id": job.id}


@app.post("/api/jobs/brief")
async def start_brief(body: PipelineRequest, user: dict = Depends(require_user)) -> dict:
    day = _parse_day(body.date)
    if not openrouter_configured():
        raise HTTPException(
            status_code=503,
            detail="Не настроен OPENROUTER_API_KEY в .env",
        )

    def work(job) -> None:
        result = run_brief_for_day(
            day,
            body.system_prompt,
            model=body.model,
            relevant_only=body.relevant_only,
            include_kallanish=body.include_kallanish,
            skip_parse=True,
            brief_kind=body.brief_kind,
            log=job.append_log,
        )
        job.result = result
        job.docx_path = result.get("docx_path")

    job = job_manager.submit("brief", work)
    return {"job_id": job.id}


@app.post("/api/jobs/weekly-brief")
async def start_weekly_brief(
    body: WeeklyBriefRequest,
    user: dict = Depends(require_user),
) -> dict:
    if not openrouter_configured():
        raise HTTPException(status_code=503, detail="Не настроен OPENROUTER_API_KEY")
    if not pgvector_configured():
        raise HTTPException(status_code=503, detail="DATABASE_URL не задан для RAG.")

    start = _parse_day(body.period_start)
    end = _parse_day(body.period_end)
    login = str(user.get("login") or "")
    map_system_prompt = (body.map_system_prompt or "").strip() or get_prompt_override(
        "weekly", login, MAP_PROMPT_VARIANT
    )
    reduce_instructions = (body.reduce_instructions or "").strip() or get_prompt_override(
        "weekly", login, REDUCE_PROMPT_VARIANT
    )

    def work(job) -> None:
        # None = все новости; [] = новости не брать (только документы RAG).
        doc_source_names = {"PDF отчёт", "PMI"}
        if body.sources is None:
            selected_sources = None
        else:
            selected_sources = [
                s.strip()
                for s in body.sources
                if s and s.strip() and s.strip() not in doc_source_names
            ]
        if body.attachment_ids is None:
            selected_attachments = None
        else:
            selected_attachments = [int(i) for i in body.attachment_ids if int(i) > 0]
        result = run_weekly_brief(
            start,
            end,
            body.system_prompt,
            map_system_prompt=map_system_prompt,
            reduce_instructions=reduce_instructions,
            model=body.model,
            brief_kind=body.brief_kind,
            sources=selected_sources,
            attachment_ids=selected_attachments,
            should_cancel=job.is_cancel_requested,
            log=job.append_log,
        )
        job.result = result

    job = job_manager.submit("weekly_brief", work)
    return {"job_id": job.id}


@app.post("/api/jobs/pipeline")
async def start_pipeline(body: PipelineRequest, user: dict = Depends(require_admin)) -> dict:
    day = _parse_day(body.date)
    if not openrouter_configured():
        raise HTTPException(
            status_code=503,
            detail="Не настроен OPENROUTER_API_KEY в .env",
        )

    def work(job) -> None:
        result = run_brief_for_day(
            day,
            body.system_prompt,
            model=body.model,
            relevant_only=body.relevant_only,
            include_kallanish=body.include_kallanish,
            skip_parse=body.skip_parse,
            brief_kind=body.brief_kind,
            log=job.append_log,
        )
        job.result = result
        job.docx_path = result.get("docx_path")

    job = job_manager.submit("pipeline", work)
    return {"job_id": job.id}


# SessionMiddleware — последним, чтобы сессия была доступна в auth_guard
add_session_middleware(app)
