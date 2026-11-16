import os

import pytest
from fastapi.testclient import TestClient

from arbeitszeit.config import Settings
from arbeitszeit.main import create_app

# The tests run on in-memory SQLite. Set AZ_TEST_DATABASE_URL (for example
# postgresql+psycopg://postgres@localhost/az_test) to run them against PostgreSQL instead;
# the tables are dropped after every test.
DATABASE_URL = os.environ.get("AZ_TEST_DATABASE_URL", "sqlite://")


@pytest.fixture()
def settings():
    return Settings(
        _env_file=None,
        secret_key="test",
        database_url=DATABASE_URL,
        admin_email="admin@example.com",
        admin_password="adminpassword1",
    )


@pytest.fixture()
def client(settings):
    app = create_app(settings, database_url=DATABASE_URL)
    with TestClient(app, follow_redirects=False) as c:
        yield c
    if not DATABASE_URL.startswith("sqlite"):
        from arbeitszeit import db as dbmod

        dbmod.Base.metadata.drop_all(dbmod._engine)
        dbmod._engine.dispose()


def csrf_of(client, path="/login"):
    import re

    html = client.get(path).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def login(client, email="admin@example.com", password="adminpassword1"):
    token = csrf_of(client)
    r = client.post("/login", data={"email": email, "password": password, "csrf_token": token})
    assert r.status_code == 303, r.text
    return client


@pytest.fixture()
def admin(client):
    return login(client)
