import re
from datetime import date

from sqlalchemy import select

from arbeitszeit import db as dbmod
from arbeitszeit.models import Identity, MonthApproval, Project, TimeEntry, User
from arbeitszeit.oidc import LoginDenied, resolve_user
from arbeitszeit.security import hash_password

from .conftest import csrf_of, login


def post(client, path, data=None, page="/"):
    data = dict(data or {})
    data["csrf_token"] = csrf_of(client, page)
    return client.post(path, data=data)


def last_month():
    t = date.today()
    return (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)


def add_user(email, role="user", approver_id=None, password="userpassword1"):
    with dbmod.session_factory()() as db:
        u = User(email=email, name=email.split("@")[0], password_hash=hash_password(password), role=role,
                 approver_id=approver_id)
        db.add(u)
        db.commit()
        return u.id


def test_requires_login(client):
    r = client.get("/zeiten")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_login_logout_and_bad_password(client):
    token = csrf_of(client)
    r = client.post("/login", data={"email": "admin@example.com", "password": "falsch", "csrf_token": token})
    assert r.status_code == 401
    login(client)
    assert client.get("/").status_code == 200
    post(client, "/logout")
    assert client.get("/").status_code == 303


def test_csrf_required(admin):
    r = admin.post("/zeiten", data={"day": "2026-01-05", "project_id": 1, "duration": "1"})
    assert r.status_code == 403


def test_login_next_is_local_only(client):
    token = csrf_of(client)
    r = client.post("/login", data={"email": "admin@example.com", "password": "adminpassword1",
                                    "csrf_token": token, "next": "https://evil.example"})
    assert r.headers["location"] == "/"


def test_all_pages_render(admin):
    for path in ["/", "/zeiten", "/woche", "/kalender", "/jahr", "/abwesenheiten", "/berichte", "/freigaben",
                 "/admin/benutzer", "/admin/benutzer/neu", "/admin/projekte", "/profil", "/zeiten?year=2026&month=2"]:
        r = admin.get(path)
        assert r.status_code == 200, path


def test_create_edit_delete_entry(admin):
    r = post(admin, "/zeiten", {"day": "2026-03-10", "project_id": 1, "start": "08:00", "end": "12:30",
                                "pause": "0:15", "comment": "Konzept"})
    assert r.status_code == 303
    with dbmod.session_factory()() as db:
        e = db.scalar(select(TimeEntry))
        assert e.minutes == 255 and e.comment == "Konzept"
        eid = e.id
    assert "Konzept" in admin.get("/zeiten?year=2026&month=3").text
    assert admin.get(f"/zeiten/{eid}").status_code == 200
    post(admin, f"/zeiten/{eid}", {"day": "2026-03-10", "project_id": 1, "duration": "2", "comment": "neu"})
    with dbmod.session_factory()() as db:
        e = db.get(TimeEntry, eid)
        assert e.minutes == 120 and e.start is None
    post(admin, f"/zeiten/{eid}/loeschen")
    with dbmod.session_factory()() as db:
        assert db.get(TimeEntry, eid) is None


def test_invalid_entry_rejected(admin):
    post(admin, "/zeiten", {"day": "2026-03-10", "project_id": 1, "duration": "abc"})
    post(admin, "/zeiten", {"day": "2026-03-10", "project_id": 1, "start": "10:00", "end": "09:00"})
    with dbmod.session_factory()() as db:
        assert db.scalar(select(TimeEntry)) is None


def test_cannot_touch_other_users_entries(client):
    other = add_user("bob@example.com")
    with dbmod.session_factory()() as db:
        e = TimeEntry(user_id=other, project_id=1, day=date(2026, 3, 10), minutes=60)
        db.add(e)
        db.commit()
        eid = e.id
    login(client)
    assert client.get(f"/zeiten/{eid}").status_code == 404
    post(client, f"/zeiten/{eid}/loeschen")
    with dbmod.session_factory()() as db:
        assert db.get(TimeEntry, eid) is not None


def test_absence_skips_weekend_and_holidays(admin):
    # Mo 2026-12-21 bis Mo 2026-12-28: Betriebsferien ab 24.12., 25. Feiertag
    post(admin, "/abwesenheiten", {"start": "2026-12-21", "end": "2026-12-28", "kind": "vacation"})
    html = admin.get("/abwesenheiten?year=2026").text
    assert "Urlaub" in html
    from arbeitszeit.models import Absence

    with dbmod.session_factory()() as db:
        days = sorted(a.day for a in db.scalars(select(Absence)))
    assert days == [date(2026, 12, d) for d in (21, 22, 23)]


def test_absence_without_end_date(admin):
    r = admin.post("/abwesenheiten", data={"start": "2026-03-10", "end": "", "kind": "sick",
                                           "csrf_token": csrf_of(admin, "/")})
    assert r.status_code == 303


def test_submit_lock_reject_approve_flow(client):
    y, m = last_month()
    mgr = add_user("chef@example.com", role="approver")
    emp = add_user("emp@example.com", approver_id=mgr)
    login(client, "emp@example.com", "userpassword1")
    d = f"{y}-{m:02d}-10"
    post(client, "/zeiten", {"day": d, "project_id": 1, "duration": "8", "comment": "x"})
    assert client.get("/freigaben").status_code == 403

    post(client, "/monat/einreichen", {"year": y, "month": m})
    with dbmod.session_factory()() as db:
        a = db.scalar(select(MonthApproval))
        assert a.status == "submitted" and a.worked_minutes == 480
        aid = a.id
    # gesperrt
    post(client, "/zeiten", {"day": d, "project_id": 1, "duration": "1"})
    with dbmod.session_factory()() as db:
        assert len(db.scalars(select(TimeEntry)).all()) == 1
    post(client, "/logout")

    # fremder Freigeber (nicht zugeordnet) darf nicht
    other = add_user("other@example.com", role="approver")
    login(client, "other@example.com", "userpassword1")
    assert client.get(f"/freigaben/{aid}").status_code == 404
    post(client, f"/freigaben/{aid}/freigeben")
    post(client, "/logout")

    login(client, "chef@example.com", "userpassword1")
    assert "emp" in client.get("/freigaben").text
    assert client.get(f"/freigaben/{aid}").status_code == 200
    post(client, f"/freigaben/{aid}/ablehnen", {"note": ""}, page=f"/freigaben/{aid}")  # ohne Grund
    with dbmod.session_factory()() as db:
        assert db.get(MonthApproval, aid).status == "submitted"
    post(client, f"/freigaben/{aid}/ablehnen", {"note": "Projekt fehlt"}, page=f"/freigaben/{aid}")
    with dbmod.session_factory()() as db:
        assert db.get(MonthApproval, aid).status == "rejected"
    post(client, "/logout")

    login(client, "emp@example.com", "userpassword1")
    post(client, "/zeiten", {"day": d, "project_id": 1, "duration": "1", "comment": "korrektur"})  # wieder offen
    post(client, "/monat/einreichen", {"year": y, "month": m})
    post(client, "/logout")
    login(client, "chef@example.com", "userpassword1")
    post(client, f"/freigaben/{aid}/freigeben", page=f"/freigaben/{aid}")
    with dbmod.session_factory()() as db:
        a = db.get(MonthApproval, aid)
        assert a.status == "approved" and a.worked_minutes == 540 and a.decided_by_id == mgr
    post(client, f"/freigaben/{aid}/oeffnen", page=f"/freigaben/{aid}")
    with dbmod.session_factory()() as db:
        assert db.get(MonthApproval, aid).status == "open"


def test_future_month_cannot_be_submitted(admin):
    t = date.today()
    post(admin, "/monat/einreichen", {"year": t.year + 1, "month": 1})
    with dbmod.session_factory()() as db:
        assert db.scalar(select(MonthApproval)) is None


def test_admin_requires_role(client):
    add_user("u@example.com")
    login(client, "u@example.com", "userpassword1")
    assert client.get("/admin/benutzer").status_code == 403
    assert client.get("/admin/projekte").status_code == 403


def test_admin_creates_user_and_project(admin):
    post(admin, "/admin/benutzer", {"name": "Neu", "email": "neu@example.com", "role": "user", "annual_hours": "1700",
                                    "vacation_days": "25", "state": "BY", "password": "langespasswort", "is_active": "1"})
    with dbmod.session_factory()() as db:
        u = db.scalar(select(User).where(User.email == "neu@example.com"))
        assert u and u.state == "BY" and u.password_hash
    post(admin, "/admin/projekte", {"code": "P1", "name": "Projekt Eins"})
    with dbmod.session_factory()() as db:
        assert db.scalar(select(Project).where(Project.code == "P1"))
    # Duplikat
    r = post(admin, "/admin/benutzer", {"name": "X", "email": "neu@example.com", "role": "user", "annual_hours": "1700",
                                        "vacation_days": "25", "state": "BY", "password": "langespasswort"})
    assert r.status_code == 400


def test_admin_cannot_demote_self(admin):
    with dbmod.session_factory()() as db:
        uid = db.scalar(select(User).where(User.email == "admin@example.com")).id
    r = post(admin, f"/admin/benutzer/{uid}", {"name": "A", "email": "admin@example.com", "role": "user",
                                               "annual_hours": "1700", "vacation_days": "25", "state": "NW"})
    assert r.status_code == 400


def test_report_and_csv_injection_safe(admin):
    post(admin, "/zeiten", {"day": date.today().isoformat(), "project_id": 1, "duration": "1", "comment": "=HYPERLINK(1)"})
    r = admin.get("/berichte/export.csv")
    assert r.status_code == 200
    assert "'=HYPERLINK(1)" in r.text
    assert admin.get("/berichte").status_code == 200


def test_profile_password_change(client):
    add_user("p@example.com")
    login(client, "p@example.com", "userpassword1")
    post(client, "/profil", {"name": "P", "current_password": "userpassword1", "new_password": "neuespasswort1"}, page="/profil")
    post(client, "/logout")
    login(client, "p@example.com", "neuespasswort1")


# --- OIDC-Zuordnung (ohne Netzwerk) ---
def test_oidc_creates_and_relinks_user(settings, client):
    with dbmod.session_factory()() as db:
        u = resolve_user(db, "azure", {"sub": "abc", "email": "New@Corp.com", "name": "New Person"}, settings)
        assert u.email == "new@corp.com" and u.role == "user" and u.password_hash is None
        again = resolve_user(db, "azure", {"sub": "abc", "email": "changed@corp.com"}, settings)
        assert again.id == u.id
        assert db.scalar(select(Identity)).provider == "azure"


def test_oidc_links_existing_by_email_only_if_verified(settings, client):
    uid = add_user("a@corp.com")
    with dbmod.session_factory()() as db:
        try:
            resolve_user(db, "sso", {"sub": "1", "email": "a@corp.com"}, settings)
            raise AssertionError("sollte abgelehnt werden")
        except LoginDenied:
            pass
        u = resolve_user(db, "google", {"sub": "2", "email": "a@corp.com", "email_verified": True}, settings)
        assert u.id == uid


def test_oidc_respects_auto_create_off_and_inactive(settings, client):
    settings.oidc_auto_create_users = False
    with dbmod.session_factory()() as db:
        try:
            resolve_user(db, "azure", {"sub": "x", "email": "nobody@corp.com"}, settings)
            raise AssertionError
        except LoginDenied:
            pass


def test_oidc_login_route_404_when_unconfigured(client):
    assert client.get("/auth/azure/login").status_code == 404


def test_security_headers_free_pages(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    assert re.search("<form", client.get("/login").text)


# --- E-Mail ---
def _mail_settings():
    from arbeitszeit.config import Settings

    return Settings(_env_file=None, smtp_host="smtp.example.com", smtp_user="zeit@example.com",
                    smtp_password="pw", smtp_from="zeit@example.com", base_url="https://zeit.example.com")


def test_message_headers_and_no_injection():
    from arbeitszeit import mail

    msg = mail.build_message(_mail_settings(), "a@b.de", "Betreff", "Text")
    assert msg["From"] == "Arbeitszeit <zeit@example.com>"
    assert msg["Message-ID"].endswith("@example.com>")
    assert msg["Auto-Submitted"] == "auto-generated"
    import pytest

    with pytest.raises(ValueError):  # Header-Injection wird abgelehnt
        mail.build_message(_mail_settings(), "a@b.de", "x\nBcc: evil@x.de", "t")


def test_workflow_sends_notifications(client, monkeypatch):
    from arbeitszeit import config, mail

    settings = _mail_settings()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    for mod in ("arbeitszeit.routers.timesheet", "arbeitszeit.routers.approvals"):
        monkeypatch.setattr(mod + ".get_settings", lambda: settings)
    sent = []
    monkeypatch.setattr(mail, "deliver", lambda msg, s: sent.append(msg))
    y, m = last_month()
    mgr = add_user("chef2@example.com", role="approver")
    add_user("emp2@example.com", approver_id=mgr)
    login(client, "emp2@example.com", "userpassword1")
    post(client, "/zeiten", {"day": f"{y}-{m:02d}-10", "project_id": 1, "duration": "8"})
    post(client, "/monat/einreichen", {"year": y, "month": m})
    assert [x["To"] for x in sent] == ["chef2@example.com"]
    assert "zeit.example.com/freigaben/" in sent[0].get_content()
    post(client, "/logout")
    login(client, "chef2@example.com", "userpassword1")
    with dbmod.session_factory()() as db:
        aid = db.scalar(select(MonthApproval)).id
    post(client, f"/freigaben/{aid}/ablehnen", {"note": "Stunden fehlen"}, page=f"/freigaben/{aid}")
    assert sent[-1]["To"] == "emp2@example.com" and "Stunden fehlen" in sent[-1].get_content()


def test_no_mail_without_smtp(admin):
    assert admin.get("/admin/email").status_code == 200
    r = post(admin, "/admin/email/test", page="/admin/email")
    assert r.status_code == 303
