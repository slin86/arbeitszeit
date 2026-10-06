from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..calc import GERMAN_STATES
from ..config import get_settings
from ..db import get_db
from ..models import Project, Role, TimeEntry, User
from ..security import csrf_protect, hash_password, require_admin
from ..web import flash, redirect, render

router = APIRouter(prefix="/admin")


def _users_form(request: Request, admin: User, db: Session, u: User | None, status: int = 200):
    approvers = db.scalars(
        select(User).where(User.role.in_([Role.approver, Role.admin]), User.is_active.is_(True)).order_by(User.name)
    ).all()
    return render(request, "admin_user.html", status_code=status, user=admin, u=u, approvers=approvers, roles=list(Role))


@router.get("/benutzer")
def users(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    all_users = db.scalars(select(User).order_by(User.name)).all()
    return render(request, "admin_users.html", user=admin, users=all_users)


@router.get("/benutzer/neu")
def user_new(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return _users_form(request, admin, db, None)


@router.get("/benutzer/{user_id}")
def user_edit(user_id: int, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    return _users_form(request, admin, db, u)


@router.post("/benutzer", dependencies=[Depends(csrf_protect)])
@router.post("/benutzer/{user_id}", dependencies=[Depends(csrf_protect)])
def user_save(
    request: Request,
    user_id: int | None = None,
    name: str = Form(...),
    email: str = Form(...),
    role: Role = Form(Role.user),
    approver_id: str = Form(""),
    annual_hours: float = Form(...),
    vacation_days: float = Form(...),
    state: str = Form("NW"),
    start_date: date | None = Form(None),
    opening_balance_hours: float = Form(0.0),
    password: str = Form(""),
    is_active: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    u = db.get(User, user_id) if user_id else User()
    if user_id and u is None:
        raise HTTPException(404)
    clash = db.scalar(select(User).where(User.email == email, User.id != (user_id or 0)))
    problem = None
    if "@" not in email:
        problem = "Ungültige E-Mail-Adresse."
    elif clash:
        problem = "Diese E-Mail-Adresse ist bereits vergeben."
    elif not (0 < annual_hours <= 3000) or not (0 <= vacation_days <= 100):
        problem = "Jahresstunden bzw. Urlaubstage sind nicht plausibel."
    elif abs(opening_balance_hours) > 1000:
        problem = "Der Startsaldo ist nicht plausibel."
    elif state not in GERMAN_STATES:
        problem = "Unbekanntes Bundesland."
    elif password and len(password) < 10:
        problem = "Das Passwort muss mindestens 10 Zeichen lang sein."
    elif user_id is None and not password and not get_settings().oidc_providers():
        problem = "Für einen neuen lokalen Benutzer ist ein Passwort nötig."
    elif user_id == admin.id and (role != Role.admin or not is_active):
        problem = "Sie können sich nicht selbst degradieren oder deaktivieren."
    if problem:
        flash(request, problem, "error")
        return _users_form(request, admin, db, u if user_id else None, 400)
    u.name, u.email, u.role = name.strip(), email, role
    u.approver_id = int(approver_id) if approver_id and int(approver_id) != user_id else None
    u.annual_hours, u.vacation_days, u.state = annual_hours, vacation_days, state
    u.start_date, u.opening_balance_hours = start_date, opening_balance_hours
    u.is_active = bool(is_active)
    if password:
        u.password_hash = hash_password(password)
    db.add(u)
    db.commit()
    flash(request, "Benutzer gespeichert.", "success")
    return redirect("/admin/benutzer")


@router.get("/projekte")
def projects(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Project, func.coalesce(func.sum(TimeEntry.minutes), 0))
        .outerjoin(TimeEntry, TimeEntry.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.code)
    ).all()
    return render(request, "admin_projects.html", user=admin, rows=rows)


@router.post("/projekte", dependencies=[Depends(csrf_protect)])
def project_create(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    code, name = code.strip(), name.strip()
    if not code or not name:
        flash(request, "Nummer und Name sind Pflicht.", "error")
    elif db.scalar(select(Project).where(Project.code == code)):
        flash(request, "Diese Projektnummer existiert bereits.", "error")
    else:
        db.add(Project(code=code[:30], name=name[:200]))
        db.commit()
        flash(request, "Projekt angelegt.", "success")
    return redirect("/admin/projekte")


@router.post("/projekte/{project_id}", dependencies=[Depends(csrf_protect)])
def project_update(
    project_id: int,
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    is_active: str = Form(""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404)
    code = code.strip()
    if not code or not name.strip():
        flash(request, "Nummer und Name sind Pflicht.", "error")
    elif db.scalar(select(Project).where(Project.code == code, Project.id != project_id)):
        flash(request, "Diese Projektnummer existiert bereits.", "error")
    else:
        p.code, p.name, p.is_active = code[:30], name.strip()[:200], bool(is_active)
        db.commit()
        flash(request, "Projekt gespeichert.", "success")
    return redirect("/admin/projekte")
