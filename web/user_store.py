from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .auth_crypto import hash_password, verify_password

WEB_DIR = Path(__file__).resolve().parent
ALLOWED_ACCOUNTS_PATH = WEB_DIR / "allowed_accounts.json"
REGISTERED_USERS_PATH = WEB_DIR / "users.json"

_LOGIN_RE = re.compile(r"^[a-zA-Z0-9._-]{3,32}$")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def normalize_login(login: str) -> str:
    return login.strip().lower()


def validate_login(login: str) -> None:
    value = normalize_login(login)
    if not _LOGIN_RE.match(value):
        raise ValueError(
            "Логин: 3–32 символа, латиница, цифры, точка, дефис или подчёркивание."
        )


def list_allowed_accounts() -> list[dict[str, Any]]:
    data = _read_json(ALLOWED_ACCOUNTS_PATH)
    accounts = data.get("accounts") or []
    return [item for item in accounts if item.get("enabled", True)]


def is_login_allowed(login: str) -> Optional[dict[str, Any]]:
    key = normalize_login(login)
    for account in list_allowed_accounts():
        if normalize_login(str(account.get("login", ""))) == key:
            return account
    return None


def account_role(account: dict[str, Any]) -> str:
    role = str(account.get("role", "user")).strip().lower()
    return role if role in {"admin", "user"} else "user"


def is_admin_login(login: str) -> bool:
    allowed = is_login_allowed(login)
    return allowed is not None and account_role(allowed) == "admin"


def list_registered_logins() -> list[str]:
    data = _read_json(REGISTERED_USERS_PATH)
    users = data.get("users") or []
    return [normalize_login(str(item.get("login", ""))) for item in users]


def get_registered_user(login: str) -> Optional[dict[str, Any]]:
    key = normalize_login(login)
    data = _read_json(REGISTERED_USERS_PATH)
    for user in data.get("users") or []:
        if normalize_login(str(user.get("login", ""))) == key:
            return user
    return None


def register_user(login: str, password: str) -> dict[str, Any]:
    validate_login(login)
    if len(password) < 5:
        raise ValueError("Пароль не короче 5 символов.")

    allowed = is_login_allowed(login)
    if not allowed:
        raise ValueError(
            "Этот логин не в списке разрешённых аккаунтов. "
            "Обратитесь к администратору или проверьте allowed_accounts.json."
        )

    key = normalize_login(login)
    if get_registered_user(key):
        raise ValueError("Пользователь уже зарегистрирован. Войдите или сбросьте пароль у администратора.")

    data = _read_json(REGISTERED_USERS_PATH)
    users = list(data.get("users") or [])
    record = {
        "login": key,
        "full_name": allowed.get("full_name") or key,
        "password_hash": hash_password(password),
        "registered_at": datetime.now().isoformat(timespec="seconds"),
    }
    users.append(record)
    _write_json(REGISTERED_USERS_PATH, {"users": users})
    return {
        "login": key,
        "full_name": record["full_name"],
        "role": account_role(allowed),
        "is_admin": account_role(allowed) == "admin",
    }


def authenticate(login: str, password: str) -> Optional[dict[str, Any]]:
    user = get_registered_user(login)
    if not user:
        return None
    stored = user.get("password_hash") or ""
    if not verify_password(password, stored):
        return None
    if not is_login_allowed(login):
        return None
    allowed = is_login_allowed(str(user.get("login", "")))
    role = account_role(allowed) if allowed else "user"
    return {
        "login": normalize_login(str(user.get("login", ""))),
        "full_name": user.get("full_name") or user.get("login"),
        "role": role,
        "is_admin": role == "admin",
    }


def registration_status(login: str) -> dict[str, Any]:
    key = normalize_login(login)
    allowed = is_login_allowed(key)
    registered = get_registered_user(key) is not None
    return {
        "login": key,
        "allowed": allowed is not None,
        "registered": registered,
        "can_register": allowed is not None and not registered,
        "full_name": (allowed or {}).get("full_name"),
    }
