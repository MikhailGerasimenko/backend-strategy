from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware

from .config import load_dotenv
from .user_store import authenticate, register_user

SESSION_USER_KEY = "user"


def auth_secret() -> str:
    load_dotenv()
    secret = os.getenv("AUTH_SECRET", "").strip()
    if not secret:
        secret = os.getenv("OPENROUTER_API_KEY", "")[:32] or "change-me-strategic-navigator-secret"
    return secret


def add_session_middleware(app) -> None:
    app.add_middleware(
        SessionMiddleware,
        secret_key=auth_secret(),
        session_cookie="sn_session",
        max_age=60 * 60 * 24 * 14,
        same_site="lax",
        https_only=False,
    )


def get_session_user(request: Request) -> Optional[dict[str, Any]]:
    raw = request.session.get(SESSION_USER_KEY)
    if not isinstance(raw, dict) or not raw.get("login"):
        return None
    return raw


def set_session_user(request: Request, user: dict[str, Any]) -> None:
    request.session[SESSION_USER_KEY] = {
        "login": user["login"],
        "full_name": user.get("full_name") or user["login"],
        "role": user.get("role", "user"),
        "is_admin": bool(user.get("is_admin")),
    }


def clear_session_user(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def login_user(request: Request, login: str, password: str) -> dict[str, Any]:
    user = authenticate(login, password)
    if not user:
        raise HTTPException(status_code=401, detail="Неверный логин или пароль.")
    set_session_user(request, user)
    return user


def register_and_login(request: Request, login: str, password: str) -> dict[str, Any]:
    try:
        user = register_user(login, password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    set_session_user(request, user)
    return user


def require_user(request: Request) -> dict[str, Any]:
    user = get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Требуется вход в систему.")
    return user


def require_admin(request: Request) -> dict[str, Any]:
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Доступ только для администратора.")
    return user
