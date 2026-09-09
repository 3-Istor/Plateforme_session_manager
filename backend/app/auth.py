import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import get_db
from .models import UserSession, UserProfile
from .schemas import User


def display_name(email: str) -> str:
    local = email.split("@", 1)[0].replace(".", " ").replace("-", " ")
    return local.title()


def profile_user(db: Session, email: str, name: str, settings: Settings) -> User:
    profile = db.get(UserProfile, email) if db is not None else None
    bootstrap = email == str(settings.manager_email).lower()
    return User(
        email=email,
        name=f"{profile.first_name} {profile.last_name}".strip() if profile else name,
        first_name=profile.first_name if profile else "",
        last_name=profile.last_name if profile else "",
        is_manager=bootstrap or bool(profile and profile.manager_status == "approved"),
        manager_status="approved" if bootstrap else profile.manager_status if profile else "member",
    )


def authenticate_google_credential(credential: str, settings: Settings) -> User:
    manager_email = str(settings.manager_email).lower()
    try:
        payload = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            settings.google_client_id,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Jeton Google invalide") from exc

    email = str(payload.get("email", "")).lower()
    if not payload.get("email_verified") or email not in settings.member_emails:
        raise HTTPException(status_code=403, detail="Compte non autorisé")
    return User(
        email=email,
        name=str(payload.get("name") or display_name(email)),
        avatar_url=payload.get("picture"),
        is_manager=email == manager_email,
    )


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user_session(db: Session, user: User, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    db.execute(delete(UserSession).where(UserSession.expires_at <= now))
    db.execute(delete(UserSession).where(UserSession.email == str(user.email).lower()))
    token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            token_hash=_token_hash(token),
            email=str(user.email).lower(),
            name=user.name,
            expires_at=now + timedelta(minutes=settings.session_ttl_minutes),
        )
    )
    db.commit()
    return token


def delete_user_session(db: Session, token: str | None) -> None:
    if not token:
        return
    session = db.get(UserSession, _token_hash(token))
    if session:
        db.delete(session)
        db.commit()


def current_user(
    request: Request,
    x_demo_user: Annotated[str | None, Header()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    manager_email = str(settings.manager_email).lower()

    if settings.auth_mode == "demo":
        if settings.app_env == "production":
            raise HTTPException(status_code=500, detail="Le mode démo est interdit en production")
        email = (x_demo_user or settings.member_emails[0]).strip().lower()
        if email not in settings.member_emails:
            raise HTTPException(status_code=403, detail="Utilisateur hors de l'équipe")
        return profile_user(db, email, display_name(email), settings)

    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connexion Google requise")
    session = db.get(UserSession, _token_hash(token))
    if not session or session.expires_at <= datetime.now(timezone.utc):
        if session:
            db.delete(session)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expirée")
    email = session.email.lower()
    if email not in settings.member_emails:
        db.delete(session)
        db.commit()
        raise HTTPException(status_code=403, detail="Compte non autorisé")
    return profile_user(db, email, session.name, settings)


def manager_only(user: User = Depends(current_user)) -> User:
    if not user.is_manager:
        raise HTTPException(status_code=403, detail="Action réservée au manager")
    return user
