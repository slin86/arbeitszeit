from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .. import calc, services
from ..db import get_db
from ..models import Project, Role, TimeEntry, User
from ..security import current_user
from ..web import render

router = APIRouter(prefix="/berichte")


def _visible_users(db: Session, actor: User) -> list[User]:
    users = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.name)).all()
    return [u for u in users if u.id == actor.id or services.can_decide(actor, u)]


def _query(db, actor, start, end, project_id, user_id):
    visible = {u.id: u for u in _visible_users(db, actor)}
    ids = [user_id] if user_id in visible else list(visible)
    q = (
        select(TimeEntry)
        .options(joinedload(TimeEntry.project), joinedload(TimeEntry.user))
        .where(TimeEntry.user_id.in_(ids), TimeEntry.day >= start, TimeEntry.day <= end)
        .order_by(TimeEntry.day, TimeEntry.user_id, TimeEntry.start, TimeEntry.id)
    )
    if project_id:
        q = q.where(TimeEntry.project_id == project_id)
    return list(visible.values()), db.scalars(q).all()


def _range(frm: date | None, to: date | None) -> tuple[date, date]:
    t = date.today()
    start, end = calc.month_range(t.year, t.month)
    return frm or start, to or end


@router.get("")
def report(
    request: Request,
    frm: date | None = None,
    to: date | None = None,
    project_id: int | None = None,
    user_id: int | None = None,
    actor: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _range(frm, to)
    users, entries = _query(db, actor, start, end, project_id, user_id or (actor.id if not actor.is_approver else None))
    by_project: dict[str, int] = {}
    by_user: dict[str, int] = {}
    for e in entries:
        by_project[e.project.label] = by_project.get(e.project.label, 0) + e.minutes
        by_user[e.user.name] = by_user.get(e.user.name, 0) + e.minutes
    return render(
        request,
        "reports.html",
        user=actor,
        entries=entries,
        by_project=sorted(by_project.items(), key=lambda kv: -kv[1]),
        by_user=sorted(by_user.items(), key=lambda kv: -kv[1]),
        total=sum(by_project.values()),
        users=users,
        projects=list(db.scalars(select(Project).order_by(Project.code))),
        frm=start,
        to=end,
        project_id=project_id,
        user_id=user_id,
    )


def _csv_safe(value: str) -> str:
    # Schutz vor CSV-/Formel-Injection beim Öffnen in Excel
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@router.get("/export.csv")
def export_csv(
    frm: date | None = None,
    to: date | None = None,
    project_id: int | None = None,
    user_id: int | None = None,
    actor: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    start, end = _range(frm, to)
    _, entries = _query(db, actor, start, end, project_id, user_id or (actor.id if not actor.is_approver else None))
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Datum", "Mitarbeiter", "Projektnummer", "Projekt", "Beginn", "Ende", "Stunden", "Dezimal", "Kommentar"])
    for e in entries:
        w.writerow(
            [
                e.day.isoformat(),
                _csv_safe(e.user.name),
                _csv_safe(e.project.code),
                _csv_safe(e.project.name),
                e.start.strftime("%H:%M") if e.start else "",
                e.end.strftime("%H:%M") if e.end else "",
                calc.fmt_hm(e.minutes),
                f"{e.minutes / 60:.2f}".replace(".", ","),
                _csv_safe(e.comment.replace("\n", " ")),
            ]
        )
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="zeiten_{start}_{end}.csv"'},
    )
