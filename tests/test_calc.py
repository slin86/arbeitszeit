from datetime import date

import pytest

from arbeitszeit import calc


def cal():
    return calc.get_calendar("DE", "NW", "12-24", "12-31")


def plan(year=2026, vac_in=False):
    return calc.Plan(cal(), year, 1700, 25, vac_in)


def test_shutdown_and_holidays_are_not_workdays():
    c = cal()
    assert not c.is_workday(date(2026, 12, 24))
    assert not c.is_workday(date(2026, 12, 31))
    assert not c.is_workday(date(2026, 12, 25))  # Feiertag
    assert not c.is_workday(date(2026, 5, 1))
    assert not c.is_workday(date(2026, 10, 3))  # Samstag
    assert c.is_workday(date(2026, 12, 23))
    assert c.is_workday(date(2026, 1, 2))


def test_daily_target_hits_annual_hours_when_all_vacation_taken():
    p = plan()
    days = p.planned_days
    assert p.daily_minutes == pytest.approx(1700 * 60 / (days - 25))
    # Alle Urlaubstage genommen -> Jahres-Soll == 1700 h
    absences = {d: calc.AbsenceDay("vacation", 1.0) for d in p.cal.workdays(date(2026, 7, 1), date(2026, 8, 4))[:25]}
    s = calc.year_summary(p, {}, absences, date(2026, 1, 1))
    assert s.year_stats.target_minutes == pytest.approx(1700 * 60)
    assert s.vacation_planned == 25
    assert s.vacation_left == 0


def test_vacation_included_in_annual_hours():
    p = plan(vac_in=True)
    assert p.daily_minutes == pytest.approx(1700 * 60 / p.planned_days)


def test_month_target_reduced_by_absence_and_half_day():
    p = plan()
    start, end = calc.month_range(2026, 3)
    base = calc.period_stats(p, {}, {}, start, end)
    abs_ = {date(2026, 3, 2): calc.AbsenceDay("vacation", 1.0), date(2026, 3, 3): calc.AbsenceDay("sick", 0.5)}
    s = calc.period_stats(p, {date(2026, 3, 4): 480}, abs_, start, end)
    assert s.target_minutes == pytest.approx(base.target_minutes - 1.5 * p.daily_minutes)
    assert s.absence_days == 1.5
    assert s.saldo_minutes == pytest.approx(480 - s.target_minutes)


def test_absence_on_weekend_is_ignored():
    p = plan()
    start, end = calc.month_range(2026, 3)
    base = calc.period_stats(p, {}, {}, start, end)
    s = calc.period_stats(p, {}, {date(2026, 3, 7): calc.AbsenceDay("vacation", 1.0)}, start, end)
    assert s.target_minutes == base.target_minutes


def test_remaining_hours_per_day():
    p = plan()
    s = calc.year_summary(p, {date(2026, 1, 5): 600}, {}, date(2026, 6, 30))
    assert s.remaining_minutes == pytest.approx(s.year_stats.target_minutes - 600)
    assert s.remaining_days == len(p.cal.workdays(date(2026, 7, 1), date(2026, 12, 31)))


@pytest.mark.parametrize(
    "text,expected",
    [("1:30", 90), ("1,5", 90), ("1.5", 90), ("90m", 90), ("2h", 120), ("2h30", 150), ("0:45", 45), (" 8 ", 480)],
)
def test_parse_duration(text, expected):
    assert calc.parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "0", "25", "-1"])
def test_parse_duration_invalid(text):
    with pytest.raises(ValueError):
        calc.parse_duration(text)


def test_fmt_hm():
    assert calc.fmt_hm(90) == "1:30"
    assert calc.fmt_hm(-90, signed=True) == "-1:30"
    assert calc.fmt_hm(5, signed=True) == "+0:05"


def test_start_date_and_opening_balance():
    p = calc.Plan(cal(), 2026, 1700, 25, False, start_date=date(2026, 10, 1), opening_minutes=-120)
    # Vor dem Startdatum kein Soll in Monats-/Periodenansichten
    s = calc.period_stats(p, {}, {}, *calc.month_range(2026, 5))
    assert s.target_minutes == 0 and s.planned_days == 0
    # Jahr: Saldo bis heute = Startsaldo + Ist - Soll seit Start
    today = date(2026, 10, 6)
    ys = calc.year_summary(p, {date(2026, 10, 5): 480}, {}, today)
    since_start = len(p.cal.workdays(date(2026, 10, 1), today)) * p.daily_minutes
    assert ys.to_date.saldo_minutes == pytest.approx(-120 + 480 - since_start)
    # Jahresziel bleibt voll (nicht gekürzt), Ist enthält das bereits Geleistete
    full = calc.year_summary(calc.Plan(cal(), 2026, 1700, 25, False), {}, {}, today)
    assert ys.year_stats.target_minutes == pytest.approx(full.year_stats.target_minutes)
