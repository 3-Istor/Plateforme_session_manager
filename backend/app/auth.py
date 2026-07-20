from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from .config import Settings, get_settings
from .schemas import User


def display_name(email: str) -> str:
    local = email.split("@", 1)[0].replace(".", " ").replace("-", " ")
    return local.title()


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    x_demo_user: Annotated[str | None, Header()] = None,
    settings: Settings = Depends(get_settings),
) -> User:
    manager_email = str(settings.manager_email).lower()

    if settings.auth_mode == "demo":
        if settings.app_env == "production":
            raise HTTPException(status_code=500, detail="Le mode démo est interdit en production")
        email = (x_demo_user or settings.member_emails[0]).strip().lower()
        if email not in settings.member_emails:
            raise HTTPException(status_code=403, detail="Utilisateur hors de l'équipe")
        return User(email=email, name=display_name(email), is_manager=email == manager_email)

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connexion Google requise")
    try:
        payload = id_token.verify_oauth2_token(
            authorization.removeprefix("Bearer "),
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


def manager_only(user: User = Depends(current_user)) -> User:
    if not user.is_manager:
        raise HTTPException(status_code=403, detail="Action réservée au manager")
    return user
