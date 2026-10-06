from __future__ import annotations

import argparse
import getpass
from datetime import date

from sqlalchemy import select


def main() -> None:
    parser = argparse.ArgumentParser(prog="arbeitszeit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Server starten")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8000)
    run.add_argument("--reload", action="store_true")
    adm = sub.add_parser("create-admin", help="Administrator anlegen oder Passwort zurücksetzen")
    adm.add_argument("email")
    adm.add_argument("--name", default="Administrator")
    args = parser.parse_args()

    if args.cmd == "run":
        import uvicorn

        uvicorn.run(
            "arbeitszeit.main:app_factory", factory=True, host=args.host, port=args.port, reload=args.reload
        )
        return

    from . import db as dbmod
    from .config import get_settings
    from .models import Role, User
    from .security import hash_password

    s = get_settings()
    dbmod.init_engine()
    password = getpass.getpass("Passwort (min. 10 Zeichen): ")
    if len(password) < 10:
        raise SystemExit("Passwort zu kurz.")
    with dbmod.session_factory()() as db:
        email = args.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                name=args.name,
                annual_hours=s.default_annual_hours,
                vacation_days=s.default_vacation_days,
                state=s.default_state,
                start_date=date.today(),
            )
            db.add(user)
        user.role, user.is_active = Role.admin, True
        user.password_hash = hash_password(password)
        db.commit()
    print(f"Administrator {email} gespeichert.")
