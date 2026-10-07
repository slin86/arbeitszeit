from __future__ import annotations

import enum
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Role(enum.StrEnum):
    user = "user"
    approver = "approver"  # darf Monate der zugeordneten Mitarbeiter freigeben
    admin = "admin"


class AbsenceKind(enum.StrEnum):
    vacation = "vacation"
    sick = "sick"
    other = "other"  # z. B. Sonderurlaub, Gleitzeittag, Weiterbildung


ABSENCE_LABELS = {
    AbsenceKind.vacation: "Urlaub",
    AbsenceKind.sick: "Krank",
    AbsenceKind.other: "Sonstige Abwesenheit",
}


class MonthStatus(enum.StrEnum):
    open = "open"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"


STATUS_LABELS = {
    MonthStatus.open: "Offen",
    MonthStatus.submitted: "Eingereicht",
    MonthStatus.approved: "Freigegeben",
    MonthStatus.rejected: "Abgelehnt",
}


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[Role] = mapped_column(String(20), default=Role.user)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    annual_hours: Mapped[float] = mapped_column(Float, default=1700)
    vacation_days: Mapped[float] = mapped_column(Float, default=25)
    state: Mapped[str] = mapped_column(String(10), default="NW")  # Bundesland (Feiertage)
    # Soll wird erst ab diesem Tag gerechnet (Einstieg mitten im Jahr / Tool-Wechsel); leer = 1.1.
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Über-/Minusstunden-Saldo (in Stunden) zum Startdatum, z. B. aus dem alten Zeittool
    opening_balance_hours: Mapped[float] = mapped_column(Float, default=0.0)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True)  # Benachrichtigungen per E-Mail
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    approver: Mapped[User | None] = relationship(remote_side="User.id")
    identities: Mapped[list[Identity]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def is_admin(self) -> bool:
        return self.role == Role.admin

    @property
    def is_approver(self) -> bool:
        return self.role in (Role.approver, Role.admin)


class Identity(Base):
    """Verknüpfung eines Benutzers mit einem externen OIDC-Konto."""

    __tablename__ = "identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(50))
    subject: Mapped[str] = mapped_column(String(255))

    user: Mapped[User] = relationship(back_populates="identities")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def label(self) -> str:
        return f"{self.code} – {self.name}"


class TimeEntry(Base):
    __tablename__ = "time_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    start: Mapped[time | None] = mapped_column(Time, nullable=True)
    end: Mapped[time | None] = mapped_column(Time, nullable=True)
    minutes: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship()
    project: Mapped[Project] = relationship()


class Absence(Base):
    __tablename__ = "absences"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[AbsenceKind] = mapped_column(String(20), default=AbsenceKind.vacation)
    fraction: Mapped[float] = mapped_column(Float, default=1.0)  # 1.0 ganzer, 0.5 halber Tag
    note: Mapped[str] = mapped_column(String(255), default="")

    user: Mapped[User] = relationship()


class MonthApproval(Base):
    __tablename__ = "month_approvals"
    __table_args__ = (UniqueConstraint("user_id", "year", "month"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[MonthStatus] = mapped_column(String(20), default=MonthStatus.open)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    # Momentaufnahme bei Einreichung (Nachvollziehbarkeit)
    worked_minutes: Mapped[int] = mapped_column(Integer, default=0)
    target_minutes: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    decided_by: Mapped[User | None] = relationship(foreign_keys=[decided_by_id])
