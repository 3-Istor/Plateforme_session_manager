from functools import lru_cache
from pathlib import Path

from pydantic import EmailStr, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_secret: str = "development-only-secret"
    database_url: str = "sqlite:///./worksession.db"
    frontend_url: str = "http://localhost:5173"
    allowed_hosts: str = "localhost,127.0.0.1"
    auth_mode: str = "demo"
    session_ttl_minutes: int = Field(default=480, ge=15, le=1440)

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/google/calendar/callback"
    google_target_calendar_id: str = ""
    google_availability_calendar_ids: str = ""
    manager_email: EmailStr = "manager@3istor.fr"
    team_members: str = (
        "manager@3istor.fr,amine@3istor.fr,sarah@3istor.fr,"
        "lina@3istor.fr,yacine@3istor.fr,nora@3istor.fr"
    )

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_tls: bool = True

    model_config = SettingsConfigDict(
        env_file=Path(__file__).parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("auth_mode")
    @classmethod
    def valid_auth_mode(cls, value: str) -> str:
        if value not in {"demo", "google"}:
            raise ValueError("AUTH_MODE must be 'demo' or 'google'")
        return value

    @field_validator("app_env")
    @classmethod
    def valid_app_env(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be 'development', 'test' or 'production'")
        return normalized

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.app_env != "production":
            return self
        errors = []
        if self.auth_mode != "google":
            errors.append("AUTH_MODE=google est obligatoire")
        if len(self.app_secret) < 32 or self.app_secret == "development-only-secret":
            errors.append("APP_SECRET doit contenir au moins 32 caractères aléatoires")
        if not self.frontend_url.startswith("https://"):
            errors.append("FRONTEND_URL doit utiliser HTTPS")
        if not self.google_redirect_uri.startswith("https://"):
            errors.append("GOOGLE_REDIRECT_URI doit utiliser HTTPS")
        if not self.google_client_id or not self.google_client_secret:
            errors.append("les identifiants OAuth Google sont obligatoires")
        if not self.allowed_host_list or "*" in self.allowed_host_list:
            errors.append("ALLOWED_HOSTS doit contenir uniquement les domaines autorisés")
        if errors:
            raise ValueError("Configuration de production invalide : " + "; ".join(errors))
        return self

    @property
    def member_emails(self) -> list[str]:
        emails = [email.strip().lower() for email in self.team_members.split(",") if email.strip()]
        manager = str(self.manager_email).lower()
        return list(dict.fromkeys([manager, *emails]))

    @property
    def session_cookie_name(self) -> str:
        return "__Host-3istor_session" if self.app_env == "production" else "3istor_session"

    @property
    def allowed_host_list(self) -> list[str]:
        return [host.strip().lower() for host in self.allowed_hosts.split(",") if host.strip()]

    @property
    def availability_calendar_ids(self) -> list[str]:
        return list(
            dict.fromkeys(
                calendar_id.strip()
                for calendar_id in self.google_availability_calendar_ids.split(",")
                if calendar_id.strip()
            )
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
