"""Fachlogik mit Datenbankzugriff."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calc
from .config import Settings
from .models import (
    Absence,
    AbsenceKind,
    MonthApproval,
    MonthStatus,
    Project,
    Role,
    TimeEntry,
    User,
    utcnow,
)


class RuleError(Exception):
    """Verstoß gegen eine fachliche Regel; Meldung ist für Benutzer gedacht."""


def plan_for(user: User, year: int, settings: Settings) -> calc.Plan:
    cal = calc.get_calendar(settings.country, user.state, settings.shutdown_start, settings.shutdown_end)
    return calc.Plan(
        cal,
        year,
        user.annual_hours,
        user.vacation_days,
        settings.vacation_in_annual_hours,
        start_date=user.start_date,
        opening_minutes=user.opening_balance_hours * 60,
    )


def worked_by_day(db: Session, user_id: int, start: date, end: date) -> dict[date, int]:
    out: dict[date, int] = {}
    rows = db.execute(
        select(TimeEntry.day, TimeEntry.minutes).where(
            TimeEntry.user_id == user_id, TimeEntry.day >= start, TimeEntry.day <= end
        )
    )
    for day, minutes in rows:
        out[day] = out.get(day, 0) + minutes
    return out


def absences_by_day(db: Session, user_id: int, start: date, end: date) -> dict[date, calc.AbsenceDay]:
    rows = db.scalars(select(Absence).where(Absence.user_id == user_id, Absence.day >= start, Absence.day <= end))
    return {a.day: calc.AbsenceDay(str(a.kind), a.fraction) for a in rows}


def summary_for(db: Session, user: User, year: int, settings: Settings, today: date | None = None) -> calc.YearSummary:
    first, last = date(year, 1, 1), date(year, 12, 31)
    return calc.year_summary(
        plan_for(user, year, settings),
        worked_by_day(db, user.id, first, last),
        absences_by_day(db, user.id, first, last),
        today or date.today(),
    )


# --- Monatsstatus / Freigabe ---
def get_approval(db: Session, user_id: int, year: int, month: int) -> MonthApproval | None:
    return db.scalar(
        select(MonthApproval).where(
            MonthApproval.user_id == user_id, MonthApproval.year == year, MonthApproval.month == month
        )
    )


def month_status(db: Session, user_id: int, year: int, month: int) -> MonthStatus:
    a = get_approval(db, user_id, year, month)
    return MonthStatus(a.status) if a else MonthStatus.open


def is_locked(db: Session, user_id: int, year: int, month: int) -> bool:
    return month_status(db, user_id, year, month) in (MonthStatus.submitted, MonthStatus.approved)


def ensure_editable(db: Session, actor: User, owner_id: int, day: date) -> None:
    if is_locked(db, owner_id, day.year, day.month):
        raise RuleError(
            f"{calc.MONTH_NAMES[day.month]} {day.year} ist eingereicht bzw. freigegeben und kann nicht geändert werden. "
            "Ein Freigeber muss den Monat zuerst wieder öffnen."
        )


def can_decide(actor: User, owner: User) -> bool:
    if actor.role == Role.admin:
        return True
    return actor.role == Role.approver and owner.approver_id == actor.id and owner.id != actor.id


def submit_month(db: Session, user: User, year: int, month: int, settings: Settings) -> MonthApproval:
    today = date.today()
    if (year, month) > (today.year, today.month):
        raise RuleError("Zukünftige Monate können noch nicht eingereicht werden.")
    a = get_approval(db, user.id, year, month)
    if a and a.status in (MonthStatus.submitted, MonthStatus.approved):
        raise RuleError("Der Monat ist bereits eingereicht.")
    if a is None:
        a = MonthApproval(user_id=user.id, year=year, month=month)
        db.add(a)
    start, end = calc.month_range(year, month)
    stats = calc.period_stats(
        plan_for(user, year, settings),
        worked_by_day(db, user.id, start, end),
        absences_by_day(db, user.id, start, end),
        start,
        end,
    )
    a.status = MonthStatus.submitted
    a.submitted_at = utcnow()
    a.decided_at = None
    a.decided_by_id = None
    a.note = ""
    a.worked_minutes = round(stats.worked_minutes)
    a.target_minutes = round(stats.target_minutes)
    db.commit()
    return a


def decide_month(db: Session, actor: User, approval_id: int, approve: bool, note: str = "") -> MonthApproval:
    a = db.get(MonthApproval, approval_id)
    if a is None or a.status != MonthStatus.submitted:
        raise RuleError("Dieser Monat wartet nicht auf eine Entscheidung.")
    if not can_decide(actor, a.user):
        raise RuleError("Keine Berechtigung für diesen Mitarbeiter.")
    if not approve and not note.strip():
        raise RuleError("Bitte eine Begründung für die Ablehnung angeben.")
    a.status = MonthStatus.approved if approve else MonthStatus.rejected
    a.decided_at = utcnow()
    a.decided_by_id = actor.id
    a.note = note.strip()
    db.commit()
    return a


def reopen_month(db: Session, actor: User, approval_id: int) -> MonthApproval:
    a = db.get(MonthApproval, approval_id)
    if a is None or a.status not in (MonthStatus.submitted, MonthStatus.approved):
        raise RuleError("Monat ist nicht eingereicht oder freigegeben.")
    if not can_decide(actor, a.user):
        raise RuleError("Keine Berechtigung für diesen Mitarbeiter.")
    a.status = MonthStatus.open
    a.decided_at = utcnow()
    a.decided_by_id = actor.id
    db.commit()
    return a


def withdraw_month(db: Session, user: User, year: int, month: int) -> None:
    a = get_approval(db, user.id, year, month)
    if a is None or a.status != MonthStatus.submitted:
        raise RuleError("Nur eingereichte (noch nicht entschiedene) Monate können zurückgezogen werden.")
    a.status = MonthStatus.open
    db.commit()


# --- Zeitbuchungen ---
def build_entry_times(
    start_s: str, end_s: str, duration_s: str, pause_s: str = ""
) -> tuple[time | None, time | None, int]:
    """Leitet (start, end, minutes) aus Formularfeldern ab: entweder Von/Bis(+Pause) oder Dauer."""
    start_s, end_s, duration_s, pause_s = (s.strip() for s in (start_s, end_s, duration_s, pause_s))
    if start_s or end_s:
        if not (start_s and end_s):
            raise RuleError("Bitte Beginn und Ende angeben.")
        try:
            start = time.fromisoformat(start_s)
            end = time.fromisoformat(end_s)
        except ValueError:
            raise RuleError("Ungültige Uhrzeit.") from None
        minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
        if minutes <= 0:
            raise RuleError("Das Ende muss nach dem Beginn liegen.")
        if pause_s:
            try:
                minutes -= calc.parse_duration(pause_s)
            except ValueError as e:
                raise RuleError(f"Pause: {e}") from None
        if minutes <= 0:
            raise RuleError("Die Pause ist länger als die Arbeitszeit.")
        return start, end, minutes
    try:
        return None, None, calc.parse_duration(duration_s)
    except ValueError as e:
        raise RuleError(str(e)) from None


def active_project(db: Session, project_id: int, keep: int | None = None) -> Project:
    p = db.get(Project, project_id)
    if p is None or (not p.is_active and p.id != keep):
        raise RuleError("Bitte ein aktives Projekt auswählen.")
    return p


def add_absence_range(
    db: Session, user: User, start: date, end: date, kind: AbsenceKind, fraction: float, note: str, settings: Settings
) -> int:
    if end < start:
        raise RuleError("Das Enddatum liegt vor dem Startdatum.")
    if (end - start).days > 366:
        raise RuleError("Zeitraum ist zu lang.")
    if fraction not in (0.5, 1.0):
        raise RuleError("Ungültiger Tagesanteil.")
    cal = plan_for(user, start.year, settings).cal
    days = cal.workdays(start, end)
    if not days:
        raise RuleError("Im Zeitraum liegt kein Arbeitstag (Wochenende, Feiertag oder Betriebsferien).")
    for d in days:
        ensure_editable(db, user, user.id, d)
    existing = {a.day: a for a in db.scalars(select(Absence).where(Absence.user_id == user.id, Absence.day.in_(days)))}
    for d in days:
        a = existing.get(d)
        if a is None:
            a = Absence(user_id=user.id, day=d)
            db.add(a)
        a.kind, a.fraction, a.note = kind, fraction, note.strip()[:255]
    db.commit()
    return len(days)


def pending_for(db: Session, actor: User) -> list[MonthApproval]:
    rows = db.scalars(
        select(MonthApproval)
        .where(MonthApproval.status == MonthStatus.submitted)
        .order_by(MonthApproval.submitted_at)
    ).all()
    return [a for a in rows if can_decide(actor, a.user)]


def month_days(year: int, month: int) -> list[date]:
    start, end = calc.month_range(year, month)
    return [start + timedelta(n) for n in range((end - start).days + 1)]


def iso_week_days(year: int, week: int) -> list[date]:
    monday = date.fromisocalendar(year, week, 1)
    return [monday + timedelta(n) for n in range(7)]


def now_year_month() -> tuple[int, int]:
    n = datetime.now()
    return n.year, n.month
