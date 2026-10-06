from __future__ import annotations

import hmac
import secrets
import time
from collections import defaultdict, deque

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import Role, User


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode()[:72], password_hash.encode())
    except ValueError:
        return False


DUMMY_HASH = hash_password("dummy-password")


# --- Anmelde-Drosselung (pro Prozess; hinter mehreren Workern ggf. Reverse-Proxy nutzen) ---
_attempts: dict[str, deque[float]] = defaultdict(deque)
MAX_ATTEMPTS, WINDOW = 8, 300


def login_blocked(key: str) -> bool:
    q = _attempts[key]
    now = time.monotonic()
    while q and now - q[0] > WINDOW:
        q.popleft()
    return len(q) >= MAX_ATTEMPTS


def login_failed(key: str) -> None:
    _attempts[key].append(time.monotonic())


def login_ok(key: str) -> None:
    _attempts.pop(key, None)


# --- CSRF ---
def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = request.session["csrf"] = secrets.token_urlsafe(32)
    return token


async def csrf_protect(request: Request) -> None:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    sent = request.headers.get("x-csrf-token")
    if not sent:
        form = await request.form()
        sent = form.get("csrf_token")
    expected = request.session.get("csrf", "")
    if not expected or not isinstance(sent, str) or not hmac.compare_digest(sent, expected):
        raise HTTPException(403, "Ungültiges CSRF-Token – bitte Seite neu laden.")


# --- Benutzer ---
class NotAuthenticated(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or not user.is_active:
        request.session.pop("uid", None)
        raise NotAuthenticated()
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != Role.admin:
        raise HTTPException(403, "Nur für Administratoren")
    return user


def require_approver(user: User = Depends(current_user)) -> User:
    if user.role not in (Role.approver, Role.admin):
        raise HTTPException(403, "Nur für Freigeber")
    return user
