from functools import lru_cache

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class OIDCProvider(BaseModel):
    key: str  # URL-Segment, z. B. "azure"
    label: str  # Text auf dem Login-Button
    metadata_url: str
    client_id: str
    client_secret: str
    scope: str = "openid email profile"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AZ_", extra="ignore")

    secret_key: str = "dev-secret-bitte-aendern"
    database_url: str = "sqlite:///./data/arbeitszeit.db"
    base_url: str = "http://localhost:8000"
    https_only: bool = False  # Session-Cookie nur über HTTPS (in Produktion: true)

    # Erster Administrator (wird nur angelegt, wenn noch kein Benutzer existiert)
    admin_email: str | None = None
    admin_password: str | None = None
    admin_name: str = "Administrator"

    local_login_enabled: bool = True

    # Arbeitszeit-Regeln (Vorgaben für neue Benutzer)
    default_annual_hours: float = 1700
    default_vacation_days: float = 25
    default_state: str = "NW"  # Bundesland für Feiertage
    country: str = "DE"
    shutdown_start: str = "12-24"  # Betriebsferien (MM-TT), jährlich wiederkehrend
    shutdown_end: str = "12-31"
    # False: 1700 h sind reine Arbeitszeit bei vollem Urlaubsverbrauch (Tagessoll =
    # 1700 / (Arbeitstage - Urlaub)). True: Urlaub ist in den 1700 h enthalten.
    vacation_in_annual_hours: bool = False

    # E-Mail-Versand (SMTP) – optional; ohne AZ_SMTP_HOST werden keine Mails verschickt
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_security: str = "starttls"  # starttls (Port 587) | ssl (Port 465) | none (nur lokal/Test)
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # Absenderadresse, z. B. zeit@deine-domain.de (am besten = SMTP-Postfach)
    smtp_from_name: str = "Arbeitszeit"
    smtp_reply_to: str | None = None

    @property
    def mail_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_from)

    # OIDC
    oidc_auto_create_users: bool = True
    oidc_link_by_email: bool = True
    # Microsoft Entra ID (Azure AD)
    oidc_azure_tenant_id: str | None = None
    oidc_azure_client_id: str | None = None
    oidc_azure_client_secret: str | None = None
    # Google
    oidc_google_client_id: str | None = None
    oidc_google_client_secret: str | None = None
    # Beliebiger OIDC-Provider (Keycloak, Authentik, Okta, Auth0, ...)
    oidc_generic_label: str = "Single Sign-On"
    oidc_generic_metadata_url: str | None = None
    oidc_generic_client_id: str | None = None
    oidc_generic_client_secret: str | None = None

    def oidc_providers(self) -> list[OIDCProvider]:
        out: list[OIDCProvider] = []
        if self.oidc_azure_tenant_id and self.oidc_azure_client_id and self.oidc_azure_client_secret:
            out.append(
                OIDCProvider(
                    key="azure",
                    label="Microsoft",
                    metadata_url=f"https://login.microsoftonline.com/{self.oidc_azure_tenant_id}"
                    "/v2.0/.well-known/openid-configuration",
                    client_id=self.oidc_azure_client_id,
                    client_secret=self.oidc_azure_client_secret,
                )
            )
        if self.oidc_google_client_id and self.oidc_google_client_secret:
            out.append(
                OIDCProvider(
                    key="google",
                    label="Google",
                    metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                    client_id=self.oidc_google_client_id,
                    client_secret=self.oidc_google_client_secret,
                )
            )
        if self.oidc_generic_metadata_url and self.oidc_generic_client_id and self.oidc_generic_client_secret:
            out.append(
                OIDCProvider(
                    key="sso",
                    label=self.oidc_generic_label,
                    metadata_url=self.oidc_generic_metadata_url,
                    client_id=self.oidc_generic_client_id,
                    client_secret=self.oidc_generic_client_secret,
                )
            )
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
