"""Berechnung von Soll- und Ist-Arbeitszeit.

Regeln
------
* Arbeitstag = Mo–Fr, kein gesetzlicher Feiertag (Bundesland des Benutzers), keine Betriebsferien.
* Tagessoll = Jahresstunden / Anzahl Arbeitstage des Jahres. Standardmäßig werden die
  Urlaubstage vorher abgezogen (``vacation_in_annual_hours=False``): Wer genau seinen
  Jahresurlaub nimmt, kommt dann exakt auf die vertraglichen Jahresstunden.
* Abwesenheiten (Urlaub, Krank, Sonstiges) auf Arbeitstagen reduzieren das Soll um
  ``Anteil * Tagessoll``.
* Saldo = Ist - Soll.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import lru_cache

import holidays as holidays_lib

GERMAN_STATES = {
    "BW": "Baden-Württemberg",
    "BY": "Bayern",
    "BE": "Berlin",
    "BB": "Brandenburg",
    "HB": "Bremen",
    "HH": "Hamburg",
    "HE": "Hessen",
    "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen",
    "RP": "Rheinland-Pfalz",
    "SL": "Saarland",
    "SN": "Sachsen",
    "ST": "Sachsen-Anhalt",
    "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}

MONTH_NAMES = [
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]  # fmt: skip
WEEKDAY_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _mmdd(value: str) -> tuple[int, int]:
    m, d = value.split("-")
    return int(m), int(d)


class WorkCalendar:
    def __init__(self, country: str, state: str | None, shutdown_start: str, shutdown_end: str):
        self._hol = holidays_lib.country_holidays(country, subdiv=state or None, language="de")
        self._ss = _mmdd(shutdown_start)
        self._se = _mmdd(shutdown_end)

    def holiday(self, d: date) -> str | None:
        return self._hol.get(d)

    def is_shutdown(self, d: date) -> bool:
        return self._ss <= (d.month, d.day) <= self._se

    def is_workday(self, d: date) -> bool:
        return d.weekday() < 5 and self.holiday(d) is None and not self.is_shutdown(d)

    def workdays(self, start: date, end: date) -> list[date]:
        if end < start:
            return []
        return [start + timedelta(n) for n in range((end - start).days + 1) if self.is_workday(start + timedelta(n))]

    def day_label(self, d: date) -> str | None:
        if (name := self.holiday(d)) is not None:
            return name
        if self.is_shutdown(d):
            return "Betriebsferien"
        return None


@lru_cache(maxsize=64)
def get_calendar(country: str, state: str | None, shutdown_start: str, shutdown_end: str) -> WorkCalendar:
    return WorkCalendar(country, state, shutdown_start, shutdown_end)


@dataclass(frozen=True)
class AbsenceDay:
    kind: str
    fraction: float


@dataclass
class Plan:
    """Persönliche Planungsgrundlage eines Benutzers für ein Jahr."""

    cal: WorkCalendar
    year: int
    annual_hours: float
    vacation_days: float
    vacation_in_annual_hours: bool
    start_date: date | None = None  # Soll zählt erst ab hier
    opening_minutes: float = 0.0  # Saldo zum Startdatum

    @property
    def planned_days(self) -> int:
        return len(self.cal.workdays(date(self.year, 1, 1), date(self.year, 12, 31)))

    @property
    def daily_minutes(self) -> float:
        base = self.planned_days - (0 if self.vacation_in_annual_hours else self.vacation_days)
        if base <= 0:
            return 0.0
        return self.annual_hours * 60 / base


@dataclass
class Stats:
    planned_days: int = 0
    absence_days: float = 0.0
    target_minutes: float = 0.0
    worked_minutes: float = 0

    @property
    def saldo_minutes(self) -> float:
        return self.worked_minutes - self.target_minutes


def period_stats(
    plan: Plan,
    worked: dict[date, int],
    absences: dict[date, AbsenceDay],
    start: date,
    end: date,
) -> Stats:
    if plan.start_date:
        start = max(start, plan.start_date)
    days = plan.cal.workdays(start, end)
    absent = sum(absences[d].fraction for d in days if d in absences)
    target = (len(days) - absent) * plan.daily_minutes
    return Stats(
        planned_days=len(days),
        absence_days=absent,
        target_minutes=max(target, 0.0),
        worked_minutes=sum(m for d, m in worked.items() if start <= d <= end),
    )


def month_range(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


@dataclass
class YearSummary:
    year: int
    daily_minutes: float
    planned_days: int
    year_stats: Stats  # ganzes Jahr (inkl. geplanter Abwesenheiten)
    to_date: Stats  # bis heute
    months: list[Stats]
    vacation_taken: float
    vacation_planned: float  # nach heute eingetragen
    vacation_entitlement: float
    remaining_minutes: float
    remaining_days: float

    @property
    def vacation_left(self) -> float:
        return self.vacation_entitlement - self.vacation_taken - self.vacation_planned

    @property
    def needed_per_day_minutes(self) -> float:
        return self.remaining_minutes / self.remaining_days if self.remaining_days > 0 else 0.0


def year_summary(
    plan: Plan,
    worked: dict[date, int],
    absences: dict[date, AbsenceDay],
    today: date,
) -> YearSummary:
    y = plan.year
    first, last = date(y, 1, 1), date(y, 12, 31)
    year_stats = period_stats(plan, worked, absences, first, last)
    cutoff = min(max(today, first - timedelta(1)), last)
    to_date = period_stats(plan, worked, absences, first, cutoff)
    if plan.start_date and first < plan.start_date <= last:
        # Zeit vor dem Startdatum gilt als "wie geplant gearbeitet" plus Startsaldo.
        before_plan = replace(plan, start_date=None)
        target_before = period_stats(before_plan, {}, absences, first, plan.start_date - timedelta(1)).target_minutes
        prior = target_before + plan.opening_minutes
        year_stats.target_minutes += target_before
        year_stats.worked_minutes += prior
        if cutoff >= plan.start_date:
            to_date.target_minutes += target_before
            to_date.worked_minutes += prior
    future = period_stats(plan, worked, absences, cutoff + timedelta(1), last)
    months = [period_stats(plan, worked, absences, *month_range(y, m)) for m in range(1, 13)]
    workdays = set(plan.cal.workdays(first, last))
    vac = [(d, a.fraction) for d, a in absences.items() if a.kind == "vacation" and d in workdays and d.year == y]
    return YearSummary(
        year=y,
        daily_minutes=plan.daily_minutes,
        planned_days=plan.planned_days,
        year_stats=year_stats,
        to_date=to_date,
        months=months,
        vacation_taken=sum(f for d, f in vac if d <= today),
        vacation_planned=sum(f for d, f in vac if d > today),
        vacation_entitlement=plan.vacation_days,
        remaining_minutes=max(year_stats.target_minutes - year_stats.worked_minutes, 0.0),
        remaining_days=future.planned_days - future.absence_days,
    )


def fmt_hm(minutes: float | int, signed: bool = False) -> str:
    m = round(minutes)
    sign = "-" if m < 0 else ("+" if signed and m > 0 else "")
    m = abs(m)
    return f"{sign}{m // 60}:{m % 60:02d}"


def parse_duration(text: str) -> int:
    """Akzeptiert '1:30', '1,5', '1.5', '90m', '2h', '2h30'. Gibt Minuten zurück."""
    t = text.strip().lower().replace(" ", "")
    if not t:
        raise ValueError("Dauer fehlt")
    try:
        if ":" in t:
            h, m = t.split(":", 1)
            minutes = int(h or 0) * 60 + int(m or 0)
        elif t.endswith("m") and "h" not in t:
            minutes = int(t[:-1])
        elif "h" in t:
            h, _, m = t.partition("h")
            minutes = int(float(h.replace(",", ".")) * 60) + (int(m.rstrip("m")) if m else 0)
        else:
            minutes = round(float(t.replace(",", ".")) * 60)
    except ValueError:
        raise ValueError(f"Ungültige Dauer: {text!r}") from None
    if minutes <= 0 or minutes > 24 * 60:
        raise ValueError("Dauer muss zwischen 0 und 24 Stunden liegen")
    return minutes
