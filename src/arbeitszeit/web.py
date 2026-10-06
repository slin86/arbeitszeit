from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import calc
from .config import get_settings
from .models import ABSENCE_LABELS, STATUS_LABELS, AbsenceKind, MonthStatus
from .security import csrf_token

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


def _hm(minutes, signed=False):
    return calc.fmt_hm(minutes, signed)


def _hours(minutes):
    return f"{minutes / 60:.1f}".replace(".", ",")


def _de_date(d: date) -> str:
    return d.strftime("%d.%m.%Y")


templates.env.filters["hm"] = _hm
templates.env.filters["hours"] = _hours
templates.env.filters["de_date"] = _de_date
templates.env.filters["num"] = lambda v, n=1: f"{v:.{n}f}".replace(".", ",").rstrip("0").rstrip(",") if n else str(v)
templates.env.globals.update(
    MONTH_NAMES=calc.MONTH_NAMES,
    WEEKDAY_SHORT=calc.WEEKDAY_SHORT,
    STATUS_LABELS={s.value: l for s, l in STATUS_LABELS.items()},
    ABSENCE_LABELS={k.value: l for k, l in ABSENCE_LABELS.items()},
    AbsenceKind=AbsenceKind,
    MonthStatus=MonthStatus,
    GERMAN_STATES=calc.GERMAN_STATES,
    today=date.today,
)


def flash(request: Request, message: str, level: str = "info") -> None:
    request.session.setdefault("flash", []).append([level, message])


def render(request: Request, name: str, status_code: int = 200, **ctx: Any):
    messages = request.session.pop("flash", [])
    ctx.update(
        request=request,
        user=ctx.get("user"),
        csrf=csrf_token(request),
        messages=messages,
        settings=get_settings(),
    )
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def safe_next(target: str | None, default: str) -> str:
    """Nur lokale, relative Weiterleitungsziele zulassen (Open-Redirect-Schutz)."""
    if not target:
        return default
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return default
    return target


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)
