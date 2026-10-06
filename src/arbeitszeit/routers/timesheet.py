from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .. import calc, services
from ..config import get_settings
from ..db import get_db
from ..models import Absence, AbsenceKind, MonthStatus, Project, TimeEntry, User
from ..security import csrf_protect, current_user
from ..web import flash, redirect, render, safe_next

router = APIRouter()


@dataclass
class DayRow:
    day: date
    label: str | None
    workday: bool
    entries: list[TimeEntry] = field(default_factory=list)
    absence: Absence | None = None
    minutes: int = 0
    target: float = 0.0


def _day_rows(db: Session, user: User, days: list[date]) -> list[DayRow]:
    settings = get_settings()
    start, end = days[0], days[-1]
    plan = services.plan_for(user, start.year, settings)
    entries = db.scalars(
        select(TimeEntry)
        .options(joinedload(TimeEntry.project))
        .where(TimeEntry.user_id == user.id, TimeEntry.day >= start, TimeEntry.day <= end)
        .order_by(TimeEntry.day, TimeEntry.start, TimeEntry.id)
    ).all()
    absences = {
        a.day: a
        for a in db.scalars(select(Absence).where(Absence.user_id == user.id, Absence.day >= start, Absence.day <= end))
    }
    rows = {
        d: DayRow(d, plan.cal.day_label(d), plan.cal.is_workday(d), absence=absences.get(d)) for d in days
    }
    for e in entries:
        rows[e.day].entries.append(e)
        rows[e.day].minutes += e.minutes
    for r in rows.values():
        # Jahreswechsel innerhalb einer Woche: Tagessoll des jeweiligen Jahres verwenden
        dm = plan.daily_minutes if r.day.year == plan.year else services.plan_for(user, r.day.year, settings).daily_minutes
        if r.workday:
            r.target = dm * (1 - (r.absence.fraction if r.absence else 0))
    return list(rows.values())


def _projects(db: Session, keep: int | None = None) -> list[Project]:
    q = select(Project).where((Project.is_active.is_(True)) | (Project.id == keep)).order_by(Project.code)
    return list(db.scalars(q))


def _last_project(db: Session, user: User) -> int | None:
    return db.scalar(
        select(TimeEntry.project_id).where(TimeEntry.user_id == user.id).order_by(TimeEntry.id.desc()).limit(1)
    )


def _clamp_month(year: int | None, month: int | None) -> tuple[int, int]:
    ty, tm = services.now_year_month()
    year = year or ty
    month = month or tm
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        raise HTTPException(404)
    return year, month


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + month - 1 + delta
    return idx // 12, idx % 12 + 1


def _sheet_ctx(db: Session, user: User, rows: list[DayRow], year: int, month: int | None, **extra):
    """Gemeinsamer Kontext für Monats- und Wochenansicht."""
    settings = get_settings()
    first, last = rows[0].day, rows[-1].day
    plan = services.plan_for(user, first.year, settings)
    worked = {r.day: r.minutes for r in rows}
    absences = {r.day: calc.AbsenceDay(str(r.absence.kind), r.absence.fraction) for r in rows if r.absence}
    stats = calc.period_stats(plan, worked, absences, first, last)
    status = services.month_status(db, user.id, first.year, first.month)
    locked = services.is_locked(db, user.id, first.year, first.month) or (
        last.month != first.month and services.is_locked(db, user.id, last.year, last.month)
    )
    approval = services.get_approval(db, user.id, first.year, first.month)
    return dict(
        user=user,
        rows=rows,
        stats=stats,
        status=status.value,
        approval=approval,
        locked=locked,
        projects=_projects(db),
        default_project=_last_project(db, user),
        year=year,
        month=month,
        **extra,
    )


@router.get("/")
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    settings = get_settings()
    today = date.today()
    s = services.summary_for(db, user, today.year, settings, today)
    month_stats = s.months[today.month - 1]
    week_days = services.iso_week_days(*today.isocalendar()[:2])
    week_rows = _day_rows(db, user, week_days)
    week_stats = calc.period_stats(
        services.plan_for(user, today.year, settings),
        {r.day: r.minutes for r in week_rows},
        {r.day: calc.AbsenceDay(str(r.absence.kind), r.absence.fraction) for r in week_rows if r.absence},
        week_days[0],
        week_days[-1],
    )
    today_entries = next((r for r in week_rows if r.day == today), None)
    # Vormonat noch offen?
    py, pm = _shift_month(today.year, today.month, -1)
    prev_status = services.month_status(db, user.id, py, pm)
    if user.start_date and calc.month_range(py, pm)[1] < user.start_date:
        prev_status = MonthStatus.approved  # Monat liegt vor dem Startdatum – nichts einzureichen
    pending = services.pending_for(db, user) if user.is_approver else []
    return render(
        request,
        "dashboard.html",
        user=user,
        s=s,
        month_stats=month_stats,
        week_stats=week_stats,
        week_rows=week_rows,
        today_row=today_entries,
        projects=_projects(db),
        default_project=_last_project(db, user),
        prev=(py, pm, prev_status.value),
        pending_count=len(pending),
        year=today.year,
        month=today.month,
    )


@router.get("/zeiten")
def month_view(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    year, month = _clamp_month(year, month)
    rows = _day_rows(db, user, services.month_days(year, month))
    py, pm = _shift_month(year, month, -1)
    ny, nm = _shift_month(year, month, 1)
    return render(
        request,
        "month.html",
        **_sheet_ctx(db, user, rows, year, month, prev=(py, pm), next=(ny, nm), view="month"),
    )


@router.get("/woche")
def week_view(
    request: Request,
    year: int | None = None,
    week: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if year is None or week is None:
        year, week = date.today().isocalendar()[:2]
    try:
        days = services.iso_week_days(year, week)
    except ValueError:
        raise HTTPException(404) from None
    rows = _day_rows(db, user, days)
    prev = days[0] - timedelta(7)
    nxt = days[0] + timedelta(7)
    return render(
        request,
        "week.html",
        **_sheet_ctx(
            db,
            user,
            rows,
            days[0].year,
            days[0].month,
            week=week,
            iso_year=year,
            prev=prev.isocalendar()[:2],
            next=nxt.isocalendar()[:2],
            view="week",
        ),
    )


@router.get("/kalender")
def calendar_view(
    request: Request,
    year: int | None = None,
    month: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    year, month = _clamp_month(year, month)
    rows = _day_rows(db, user, services.month_days(year, month))
    by_day = {r.day: r for r in rows}
    lead = rows[0].day.weekday()
    cells: list[DayRow | None] = [None] * lead + rows
    cells += [None] * (-len(cells) % 7)
    py, pm = _shift_month(year, month, -1)
    ny, nm = _shift_month(year, month, 1)
    ctx = _sheet_ctx(db, user, rows, year, month, prev=(py, pm), next=(ny, nm), view="calendar")
    return render(request, "calendar.html", cells=cells, by_day=by_day, **ctx)


@router.get("/jahr")
def year_view(
    request: Request,
    year: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    year = year or date.today().year
    if not (2000 <= year <= 2100):
        raise HTTPException(404)
    s = services.summary_for(db, user, year, get_settings())
    statuses = [services.month_status(db, user.id, year, m).value for m in range(1, 13)]
    # Kumulierter Saldo: nur abgelaufene Monate bzw. Monate mit Buchungen
    today = date.today()
    cumulative, run = [], 0.0
    sd = user.start_date
    if sd and sd.year == year:
        run = user.opening_balance_hours * 60
    for m, st in enumerate(s.months, start=1):
        if (year, m) <= (today.year, today.month) and not (sd and (year, m) < (sd.year, sd.month)):
            run += st.saldo_minutes
            cumulative.append(run)
        else:
            cumulative.append(None)
    return render(
        request, "year.html", user=user, s=s, statuses=statuses, cumulative=cumulative, year=year
    )


# --- Zeitbuchungen ---
def _back(next_: str | None, day: date | None = None) -> str:
    default = f"/zeiten?year={day.year}&month={day.month}" if day else "/zeiten"
    return safe_next(next_, default)


@router.post("/zeiten", dependencies=[Depends(csrf_protect)])
def create_entry(
    request: Request,
    day: date = Form(...),
    project_id: int = Form(...),
    start: str = Form(""),
    end: str = Form(""),
    pause: str = Form(""),
    duration: str = Form(""),
    comment: str = Form(""),
    next: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        services.ensure_editable(db, user, user.id, day)
        services.active_project(db, project_id)
        t_start, t_end, minutes = services.build_entry_times(start, end, duration, pause)
        db.add(
            TimeEntry(
                user_id=user.id,
                project_id=project_id,
                day=day,
                start=t_start,
                end=t_end,
                minutes=minutes,
                comment=comment.strip()[:2000],
            )
        )
        db.commit()
        flash(request, f"{calc.fmt_hm(minutes)} h gebucht.", "success")
    except services.RuleError as e:
        flash(request, str(e), "error")
    return redirect(_back(next, day))


def _own_entry(db: Session, user: User, entry_id: int) -> TimeEntry:
    e = db.get(TimeEntry, entry_id)
    if e is None or e.user_id != user.id:
        raise HTTPException(404)
    return e


@router.get("/zeiten/{entry_id}")
def edit_entry_form(
    entry_id: int,
    request: Request,
    next: str = "",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    e = _own_entry(db, user, entry_id)
    return render(
        request,
        "entry_edit.html",
        user=user,
        e=e,
        projects=_projects(db, keep=e.project_id),
        next=_back(next, e.day),
        locked=services.is_locked(db, user.id, e.day.year, e.day.month),
    )


@router.post("/zeiten/{entry_id}", dependencies=[Depends(csrf_protect)])
def update_entry(
    entry_id: int,
    request: Request,
    day: date = Form(...),
    project_id: int = Form(...),
    start: str = Form(""),
    end: str = Form(""),
    pause: str = Form(""),
    duration: str = Form(""),
    comment: str = Form(""),
    next: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    e = _own_entry(db, user, entry_id)
    try:
        services.ensure_editable(db, user, user.id, e.day)
        services.ensure_editable(db, user, user.id, day)
        services.active_project(db, project_id, keep=e.project_id)
        e.start, e.end, e.minutes = services.build_entry_times(start, end, duration, pause)
        e.day, e.project_id, e.comment = day, project_id, comment.strip()[:2000]
        db.commit()
        flash(request, "Buchung gespeichert.", "success")
    except services.RuleError as err:
        flash(request, str(err), "error")
        return redirect(f"/zeiten/{entry_id}?next={safe_next(next, '/zeiten')}")
    return redirect(_back(next, day))


@router.post("/zeiten/{entry_id}/loeschen", dependencies=[Depends(csrf_protect)])
def delete_entry(
    entry_id: int,
    request: Request,
    next: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    e = _own_entry(db, user, entry_id)
    try:
        services.ensure_editable(db, user, user.id, e.day)
        day = e.day
        db.delete(e)
        db.commit()
        flash(request, "Buchung gelöscht.", "success")
    except services.RuleError as err:
        day = e.day
        flash(request, str(err), "error")
    return redirect(_back(next, day))


# --- Abwesenheiten ---
@router.get("/abwesenheiten")
def absences_view(
    request: Request,
    year: int | None = None,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    year = year or date.today().year
    items = db.scalars(
        select(Absence)
        .where(Absence.user_id == user.id, Absence.day >= date(year, 1, 1), Absence.day <= date(year, 12, 31))
        .order_by(Absence.day)
    ).all()
    # Aufeinanderfolgende Tage gleicher Art zu Zeiträumen zusammenfassen (Anzeige)
    cal = services.plan_for(user, year, get_settings()).cal
    groups: list[dict] = []
    for a in items:
        g = groups[-1] if groups else None
        if (
            g
            and g["kind"] == str(a.kind)
            and g["note"] == a.note
            and not cal.workdays(g["end"] + timedelta(1), a.day - timedelta(1))
            and (a.day - g["end"]).days <= 4
        ):
            g["end"] = a.day
            g["days"] += a.fraction
            g["ids"].append(a.id)
        else:
            groups.append(
                dict(kind=str(a.kind), note=a.note, start=a.day, end=a.day, days=a.fraction, ids=[a.id])
            )
    s = services.summary_for(db, user, year, get_settings())
    return render(request, "absences.html", user=user, groups=groups, s=s, year=year)


@router.post("/abwesenheiten", dependencies=[Depends(csrf_protect)])
def add_absence(
    request: Request,
    start: date = Form(...),
    end: date | None = Form(None),
    kind: AbsenceKind = Form(AbsenceKind.vacation),
    half: str = Form(""),
    note: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        n = services.add_absence_range(
            db, user, start, end or start, kind, 0.5 if half else 1.0, note, get_settings()
        )
        flash(request, f"{n} Tag(e) eingetragen.", "success")
    except services.RuleError as e:
        flash(request, str(e), "error")
    return redirect(f"/abwesenheiten?year={start.year}")


@router.post("/abwesenheiten/loeschen", dependencies=[Depends(csrf_protect)])
def delete_absences(
    request: Request,
    ids: str = Form(...),
    year: int = Form(...),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        id_list = [int(i) for i in ids.split(",") if i]
    except ValueError:
        raise HTTPException(400) from None
    items = db.scalars(select(Absence).where(Absence.user_id == user.id, Absence.id.in_(id_list))).all()
    try:
        for a in items:
            services.ensure_editable(db, user, user.id, a.day)
        for a in items:
            db.delete(a)
        db.commit()
        flash(request, "Abwesenheit gelöscht.", "success")
    except services.RuleError as e:
        flash(request, str(e), "error")
    return redirect(f"/abwesenheiten?year={year}")


# --- Monatsfreigabe ---
@router.post("/monat/einreichen", dependencies=[Depends(csrf_protect)])
def submit(
    request: Request,
    year: int = Form(...),
    month: int = Form(...),
    next: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        services.submit_month(db, user, year, month, get_settings())
        flash(request, f"{calc.MONTH_NAMES[month]} {year} zur Freigabe eingereicht.", "success")
    except services.RuleError as e:
        flash(request, str(e), "error")
    return redirect(safe_next(next, f"/zeiten?year={year}&month={month}"))


@router.post("/monat/zurueckziehen", dependencies=[Depends(csrf_protect)])
def withdraw(
    request: Request,
    year: int = Form(...),
    month: int = Form(...),
    next: str = Form(""),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    try:
        services.withdraw_month(db, user, year, month)
        flash(request, "Einreichung zurückgezogen.", "success")
    except services.RuleError as e:
        flash(request, str(e), "error")
    return redirect(safe_next(next, f"/zeiten?year={year}&month={month}"))
