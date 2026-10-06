from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..oidc import LoginDenied, resolve_user
from ..security import (
    DUMMY_HASH,
    csrf_protect,
    current_user,
    hash_password,
    login_blocked,
    login_failed,
    login_ok,
    verify_password,
)
from ..web import flash, redirect, render, safe_next

router = APIRouter()


def _login_page(request: Request, status: int = 200, email: str = ""):
    return render(
        request,
        "login.html",
        status_code=status,
        providers=request.app.state.providers,
        email=email,
        next=request.query_params.get("next", ""),
    )


@router.get("/login")
def login_form(request: Request):
    if request.session.get("uid"):
        return redirect("/")
    return _login_page(request)


@router.post("/login", dependencies=[Depends(csrf_protect)])
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if not settings.local_login_enabled:
        raise HTTPException(404)
    email = email.strip().lower()
    key = f"{request.client.host if request.client else '?'}|{email}"
    if login_blocked(key):
        flash(request, "Zu viele Fehlversuche. Bitte in ein paar Minuten erneut versuchen.", "error")
        return _login_page(request, 429, email)
    user = db.scalar(select(User).where(User.email == email))
    # Immer einen Hash prüfen, damit die Antwortzeit nichts über existierende Konten verrät.
    ok = verify_password(password, (user.password_hash if user else None) or DUMMY_HASH) and bool(user and user.password_hash)
    if not (user and ok and user.is_active):
        login_failed(key)
        flash(request, "E-Mail oder Passwort falsch.", "error")
        return _login_page(request, 401, email)
    login_ok(key)
    request.session.clear()
    request.session["uid"] = user.id
    return redirect(safe_next(next, "/"))


@router.post("/logout", dependencies=[Depends(csrf_protect)])
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@router.get("/auth/{provider}/login")
async def oidc_login(provider: str, request: Request):
    client = getattr(request.app.state.oauth, provider, None) if provider in request.app.state.provider_keys else None
    if client is None:
        raise HTTPException(404)
    redirect_uri = f"{get_settings().base_url.rstrip('/')}/auth/{provider}/callback"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/auth/{provider}/callback")
async def oidc_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    client = getattr(request.app.state.oauth, provider, None) if provider in request.app.state.provider_keys else None
    if client is None:
        raise HTTPException(404)
    try:
        token = await client.authorize_access_token(request)
        claims = dict(token.get("userinfo") or {})
        if not claims:
            claims = dict(await client.userinfo(token=token))
        user = resolve_user(db, provider, claims, get_settings())
    except LoginDenied as e:
        flash(request, str(e), "error")
        return redirect("/login")
    except Exception:  # noqa: BLE001 - Authlib wirft diverse Fehler (State, Netzwerk, Signatur)
        flash(request, "Anmeldung beim Provider fehlgeschlagen. Bitte erneut versuchen.", "error")
        return redirect("/login")
    request.session.clear()
    request.session["uid"] = user.id
    return redirect("/")


@router.get("/profil")
def profile(request: Request, user: User = Depends(current_user)):
    return render(request, "profile.html", user=user)


@router.post("/profil", dependencies=[Depends(csrf_protect)])
def profile_save(
    request: Request,
    name: str = Form(...),
    current_password: str = Form(""),
    new_password: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not name.strip():
        flash(request, "Name darf nicht leer sein.", "error")
        return redirect("/profil")
    user.name = name.strip()
    if new_password:
        if len(new_password) < 10:
            flash(request, "Das neue Passwort muss mindestens 10 Zeichen lang sein.", "error")
            return redirect("/profil")
        if user.password_hash and not verify_password(current_password, user.password_hash):
            flash(request, "Das aktuelle Passwort ist falsch.", "error")
            return redirect("/profil")
        user.password_hash = hash_password(new_password)
        flash(request, "Passwort geändert.", "success")
    else:
        flash(request, "Profil gespeichert.", "success")
    db.commit()
    return redirect("/profil")
