import pytest
from fastapi.testclient import TestClient

from arbeitszeit.config import Settings
from arbeitszeit.main import create_app


@pytest.fixture()
def settings():
    return Settings(
        _env_file=None,
        secret_key="test",
        database_url="sqlite://",
        admin_email="admin@example.com",
        admin_password="adminpassword1",
    )


@pytest.fixture()
def client(settings):
    app = create_app(settings, database_url="sqlite://")
    with TestClient(app, follow_redirects=False) as c:
        yield c


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
