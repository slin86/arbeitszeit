from __future__ import annotations

from datetime import date

from authlib.integrations.starlette_client import OAuth
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import OIDCProvider, Settings
from .models import Identity, Role, User


class LoginDenied(Exception):
    pass


def build_oauth(providers: list[OIDCProvider]) -> OAuth:
    oauth = OAuth()
    for p in providers:
        oauth.register(
            name=p.key,
            server_metadata_url=p.metadata_url,
            client_id=p.client_id,
            client_secret=p.client_secret,
            client_kwargs={"scope": p.scope},
        )
    return oauth


def resolve_user(db: Session, provider: str, claims: dict, settings: Settings) -> User:
    """Ordnet OIDC-Claims einem lokalen Benutzer zu (verknüpfen oder anlegen)."""
    subject = str(claims.get("sub") or "")
    if not subject:
        raise LoginDenied("Der Provider hat keine Benutzerkennung (sub) geliefert.")

    ident = db.scalar(select(Identity).where(Identity.provider == provider, Identity.subject == subject))
    if ident:
        if not ident.user.is_active:
            raise LoginDenied("Dieses Konto ist deaktiviert.")
        return ident.user

    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").strip().lower()
    if "@" not in email:
        raise LoginDenied("Der Provider hat keine E-Mail-Adresse geliefert.")
    name = claims.get("name") or email.split("@")[0]

    user = db.scalar(select(User).where(User.email == email))
    if user:
        # Verknüpfung per E-Mail nur, wenn der Provider die Adresse als verifiziert meldet
        # (Entra ID ist mandantenspezifisch und wird hier als vertrauenswürdig behandelt).
        verified = claims.get("email_verified", provider == "azure")
        if not (settings.oidc_link_by_email and verified in (True, "true")):
            raise LoginDenied(
                "Es existiert bereits ein Konto mit dieser E-Mail-Adresse, "
                "das nicht automatisch verknüpft werden darf. Bitte Administrator kontaktieren."
            )
        if not user.is_active:
            raise LoginDenied("Dieses Konto ist deaktiviert.")
    else:
        if not settings.oidc_auto_create_users:
            raise LoginDenied("Für diese Adresse existiert kein Konto. Bitte Administrator kontaktieren.")
        user = User(
            email=email,
            name=name,
            role=Role.user,
            annual_hours=settings.default_annual_hours,
            vacation_days=settings.default_vacation_days,
            state=settings.default_state,
            start_date=date.today(),
        )
        db.add(user)
        db.flush()
    db.add(Identity(user_id=user.id, provider=provider, subject=subject))
    db.commit()
    return user
