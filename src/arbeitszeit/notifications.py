"""Benachrichtigungen im Freigabe-Workflow (nur wenn SMTP konfiguriert ist)."""
from __future__ import annotations

from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import calc, mail
from .config import Settings
from .models import MonthApproval, Role, User


def _link(settings: Settings, path: str) -> str:
    return settings.base_url.rstrip("/") + path


def _period(a: MonthApproval) -> str:
    return f"{calc.MONTH_NAMES[a.month]} {a.year}"


def submitted(db: Session, a: MonthApproval, settings: Settings) -> list[EmailMessage]:
    if not settings.mail_enabled:
        return []
    owner = a.user
    recipients = [owner.approver] if owner.approver else list(
        db.scalars(select(User).where(User.role == Role.admin, User.is_active.is_(True)))
    )
    text = (
        f"{owner.name} hat {_period(a)} zur Freigabe eingereicht.\n\n"
        f"Ist: {calc.fmt_hm(a.worked_minutes)} h · Soll: {calc.fmt_hm(a.target_minutes)} h\n\n"
        f"Prüfen und freigeben: {_link(settings, f'/freigaben/{a.id}')}\n"
    )
    return [
        mail.build_message(settings, r.email, f"Freigabe angefordert: {owner.name}, {_period(a)}", text)
        for r in recipients
        if r and r.is_active and r.notify_email and r.id != owner.id
    ]


def decided(a: MonthApproval, settings: Settings, what: str) -> list[EmailMessage]:
    """what: approved | rejected | reopened"""
    owner = a.user
    if not settings.mail_enabled or not owner.notify_email:
        return []
    by = a.decided_by.name if a.decided_by else "ein Freigeber"
    if what == "approved":
        subject, body = f"{_period(a)} freigegeben", f"{by} hat {_period(a)} freigegeben."
    elif what == "rejected":
        subject = f"{_period(a)} abgelehnt – bitte korrigieren"
        body = f"{by} hat {_period(a)} abgelehnt.\n\nBegründung: {a.note}\n\nBitte korrigieren und erneut einreichen."
    else:
        subject, body = f"{_period(a)} wieder geöffnet", f"{by} hat {_period(a)} zur Bearbeitung wieder geöffnet."
    text = f"Hallo {owner.name},\n\n{body}\n\n{_link(settings, f'/zeiten?year={a.year}&month={a.month}')}\n"
    return [mail.build_message(settings, owner.email, subject, text)]
