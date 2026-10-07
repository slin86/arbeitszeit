from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        else:
            path = url.split("///", 1)[-1]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _fk(dbapi_conn, _):  # pragma: no cover - trivial
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    _SessionLocal = sessionmaker(_engine, expire_on_commit=False)
    from . import models  # noqa: F401  (registriert Tabellen)

    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    return _engine


def get_db() -> Iterator[Session]:
    assert _SessionLocal is not None, "Datenbank nicht initialisiert"
    with _SessionLocal() as session:
        yield session


def session_factory() -> sessionmaker[Session]:
    assert _SessionLocal is not None
    return _SessionLocal


def _add_missing_columns(engine: Engine) -> None:
    """Minimale Schema-Fortschreibung: fehlende Spalten bestehender Tabellen werden ergänzt
    (ersetzt keine echten Migrationen, genügt für additive Änderungen)."""
    insp = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(engine.dialect)}'
            default = col.default.arg if col.default is not None and not callable(col.default.arg) else None
            if default is not None:
                lit = ("1" if default else "0") if isinstance(default, bool) else (
                    str(default) if isinstance(default, (int, float)) else "'" + str(default).replace("'", "''") + "'"
                )
                ddl += f" DEFAULT {lit}"
            with engine.begin() as conn:
                conn.execute(text(ddl))
