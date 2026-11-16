from sqlalchemy import inspect, text

from arbeitszeit import db as dbmod

from .conftest import DATABASE_URL


def test_missing_columns_are_added_with_their_defaults(tmp_path):
    """An older database without users.notify_email gets the column, and existing rows keep working."""
    # in-memory SQLite would start empty on the second init, so use a file there
    url = f"sqlite:///{tmp_path}/old.db" if DATABASE_URL == "sqlite://" else DATABASE_URL
    dbmod.init_engine(url)
    engine = dbmod._engine
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE users DROP COLUMN notify_email"))
        conn.execute(
            text(
                "INSERT INTO users (email, name, role, annual_hours, vacation_days, state, is_active, "
                "opening_balance_hours, created_at) "
                "VALUES ('old@example.com', 'Old', 'user', 1700, 25, 'NW', TRUE, 0, CURRENT_TIMESTAMP)"
            )
        )
    try:
        engine.dispose()
        dbmod.init_engine(url)
        engine = dbmod._engine
        assert "notify_email" in {c["name"] for c in inspect(engine).get_columns("users")}
        with engine.connect() as conn:
            value = conn.execute(text("SELECT notify_email FROM users WHERE email = 'old@example.com'")).scalar()
        assert value in (True, 1)
    finally:
        dbmod.Base.metadata.drop_all(engine)
        engine.dispose()

