from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from news_parsers.llm.openrouter import OpenRouterError
from news_parsers.rag.store import BriefIndexError, DuplicateDocumentError
from news_parsers.custom_telegram import (
    add_custom_channel,
    list_custom_channels,
    list_known_topic_categories,
    parse_telegram_channel,
    remove_custom_channel,
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
)
from .config import STATIC_DIR, default_model, openrouter_configured
from .jobs import job_manager
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
from .services_rag import (
    ask_news_agent,
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

PUBLIC_PAGES = {"/login", "/register"}
PUBLIC_API_PREFIX = "/api/auth"


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static"):
        return await call_next(request)
    if path in PUBLIC_PAGES or path.startswith(PUBLIC_API_PREFIX):
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


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Неверный формат даты. Используйте YYYY-MM-DD.") from exc


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
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/register")
async def register_page(request: Request) -> FileResponse:
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
async def api_logout(request: Request) -> dict:
    clear_session_user(request)
    return {"ok": True}


@app.get("/api/auth/me")
async def api_me(user: dict = Depends(require_user)) -> dict:
    return {"user": user}


@app.get("/api/auth/check-login")
async def api_check_login(login: str) -> dict:
    return registration_status(login)


@app.get("/api/auth/accounts-overview")
async def api_accounts_overview() -> dict:
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
    return {"variant": variant, "prompt": get_default_system_prompt(variant)}


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
    return {
        "variant": variant,
        "prompt": get_default_weekly_system_prompt(
            variant,
            period_start=start,
            period_end=end,
        ),
    }


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
    return {
        "channels": list_custom_channels(),
        "categories": list_known_topic_categories(),
    }


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
        removed = remove_custom_channel(channel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "channel": removed}


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
        return ask_news_agent(
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
        return index_document_upload(
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
        return index_documents_upload(
            items,
            brief_date=_parse_day(date_str),
            period_end=end,
            document_type=document_type.strip(),
            indexed_by=str(user.get("login", "user")),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
        return index_pdf_upload(
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
        return upload_kallanish(
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
        return index_kallanish_to_rag(
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
    filename = path.name
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
        headers={"Content-Disposition": _attachment_disposition(path.name)},
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

    def work(job) -> None:
        # None = все новости; [] = новости не брать (только документы RAG).
        doc_source_names = {"Kallanish", "PDF отчёт", "PMI"}
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
