from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import calc, mail, notifications, services
from ..config import get_settings
from ..db import get_db
from ..models import MonthApproval, MonthStatus, Role, User
from ..security import csrf_protect, require_approver
from ..web import flash, redirect, render
from .timesheet import _day_rows, _shift_month

router = APIRouter(prefix="/freigaben")


def team_for(db: Session, actor: User) -> list[User]:
    q = select(User).where(User.is_active.is_(True)).order_by(User.name)
    users = db.scalars(q).all()
    return [u for u in users if services.can_decide(actor, u)]


@router.get("")
def overview(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    actor: User = Depends(require_approver),
    db: Session = Depends(get_db),
):
    ty, tm = _shift_month(date.today().year, date.today().month, -1)
    year, month = year or ty, month or tm
    if not (1 <= month <= 12):
        raise HTTPException(404)
    settings = get_settings()
    start, end = calc.month_range(year, month)
    team = []
    for u in team_for(db, actor):
        stats = calc.period_stats(
            services.plan_for(u, year, settings),
            services.worked_by_day(db, u.id, start, end),
            services.absences_by_day(db, u.id, start, end),
            start,
            end,
        )
        team.append((u, stats, services.get_approval(db, u.id, year, month)))
    py, pm = _shift_month(year, month, -1)
    ny, nm = _shift_month(year, month, 1)
    return render(
        request,
        "approvals.html",
        user=actor,
        pending=services.pending_for(db, actor),
        team=team,
        year=year,
        month=month,
        prev=(py, pm),
        next=(ny, nm),
    )


def _load(db: Session, actor: User, approval_id: int) -> MonthApproval:
    a = db.get(MonthApproval, approval_id)
    if a is None or not (services.can_decide(actor, a.user) or a.user_id == actor.id):
        raise HTTPException(404)
    return a


@router.get("/{approval_id}")
def detail(
    approval_id: int,
    request: Request,
    actor: User = Depends(require_approver),
    db: Session = Depends(get_db),
):
    a = _load(db, actor, approval_id)
    settings = get_settings()
    days = services.month_days(a.year, a.month)
    rows = _day_rows(db, a.user, days)
    stats = calc.period_stats(
        services.plan_for(a.user, a.year, settings),
        {r.day: r.minutes for r in rows},
        {r.day: calc.AbsenceDay(str(r.absence.kind), r.absence.fraction) for r in rows if r.absence},
        days[0],
        days[-1],
    )
    by_project: dict[str, int] = {}
    for r in rows:
        for e in r.entries:
            by_project[e.project.label] = by_project.get(e.project.label, 0) + e.minutes
    return render(
        request,
        "approval_detail.html",
        user=actor,
        a=a,
        owner=a.user,
        rows=rows,
        stats=stats,
        by_project=sorted(by_project.items()),
        can_decide=services.can_decide(actor, a.user),
    )


def _act(request, db, actor, approval_id, fn, ok_msg, background=None, what=None):
    try:
        a = fn()
        if background is not None and what:
            s = get_settings()
            background.add_task(mail.send_many, notifications.decided(a, s, what), s)
        flash(request, ok_msg, "success")
    except services.RuleError as e:
        a = db.get(MonthApproval, approval_id)
        flash(request, str(e), "error")
    return redirect("/freigaben" if a is None else f"/freigaben/{a.id}")


@router.post("/{approval_id}/freigeben", dependencies=[Depends(csrf_protect)])
def approve(
    approval_id: int,
    request: Request,
    background: BackgroundTasks,
    note: str = Form(""),
    actor: User = Depends(require_approver),
    db: Session = Depends(get_db),
):
    _load(db, actor, approval_id)
    return _act(request, db, actor, approval_id, lambda: services.decide_month(db, actor, approval_id, True, note), "Monat freigegeben.", background, "approved")


@router.post("/{approval_id}/ablehnen", dependencies=[Depends(csrf_protect)])
def reject(
    approval_id: int,
    request: Request,
    background: BackgroundTasks,
    note: str = Form(""),
    actor: User = Depends(require_approver),
    db: Session = Depends(get_db),
):
    _load(db, actor, approval_id)
    return _act(request, db, actor, approval_id, lambda: services.decide_month(db, actor, approval_id, False, note), "Monat abgelehnt und zur Korrektur zurückgegeben.", background, "rejected")


@router.post("/{approval_id}/oeffnen", dependencies=[Depends(csrf_protect)])
def reopen(
    approval_id: int,
    request: Request,
    background: BackgroundTasks,
    actor: User = Depends(require_approver),
    db: Session = Depends(get_db),
):
    _load(db, actor, approval_id)
    return _act(request, db, actor, approval_id, lambda: services.reopen_month(db, actor, approval_id), "Monat wieder geöffnet.", background, "reopened")
