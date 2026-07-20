from functools import lru_cache
from pathlib import Path

from pydantic import EmailStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_secret: str = "development-only-secret"
    database_url: str = "sqlite:///./worksession.db"
    frontend_url: str = "http://localhost:5173"
    auth_mode: str = "demo"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/google/calendar/callback"
    google_target_calendar_id: str = ""
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

    @property
    def member_emails(self) -> list[str]:
        emails = [email.strip().lower() for email in self.team_members.split(",") if email.strip()]
        manager = str(self.manager_email).lower()
        return list(dict.fromkeys([manager, *emails]))


@lru_cache
def get_settings() -> Settings:
    return Settings()
