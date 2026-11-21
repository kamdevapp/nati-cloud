"""Hasła, tokeny JWT i zależności autoryzacji."""

import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from .errors import AppError
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .db.models import User, UserSession

_hasher = PasswordHasher()
_bearer = HTTPBearer(auto_error=False)
ALGO = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


MEDIA_TTL = timedelta(hours=12)
# Ostatnie użycie sesji zapisujemy najwyżej co tyle (żeby nie pisać do bazy przy każdym zapytaniu).
_SEEN_EVERY = timedelta(minutes=5)


def create_session(db: Session, user: User, device: str = "", ip: str | None = None) -> UserSession:
    """Nowe zalogowane urządzenie (zatwierdza sesję bazy)."""
    session = UserSession(id=secrets.token_hex(16), user_id=user.id, device=device[:128], ip=ip)
    db.add(session)
    db.commit()
    return session


def create_token(user: User, session: UserSession) -> str:
    s = get_settings()
    payload = {
        "sub": str(user.id),
        "sid": session.id,
        "exp": datetime.now(timezone.utc) + timedelta(days=s.token_ttl_days),
    }
    return jwt.encode(payload, s.secret_key, algorithm=ALGO)


def create_media_token(user: User, session_id: str) -> str:
    """Krótki token (12 h) tylko do adresów strumieni (?token=…) – główny token nie trafia do adresów."""
    payload = {"sub": str(user.id), "sid": session_id, "scope": "media",
               "exp": datetime.now(timezone.utc) + MEDIA_TTL}
    return jwt.encode(payload, get_settings().secret_key, algorithm=ALGO)


def _user_from_token(token: str | None, db: Session, media_ok: bool = False) -> User:
    unauthorized = AppError(status.HTTP_401_UNAUTHORIZED, "auth.invalid_token")
    if not token:
        raise unauthorized
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])
        user = db.get(User, int(payload["sub"]))
        sid = payload["sid"]  # tokeny sprzed sesji urządzeń – trzeba zalogować się ponownie
    except (jwt.PyJWTError, KeyError, ValueError):
        raise unauthorized
    if user is None or (payload.get("scope") == "media" and not media_ok):
        raise unauthorized
    session = db.get(UserSession, sid)
    if session is None or session.user_id != user.id:
        raise AppError(status.HTTP_401_UNAUTHORIZED, "auth.session_expired")
    now = datetime.now(timezone.utc)
    seen = session.last_seen if session.last_seen.tzinfo else session.last_seen.replace(tzinfo=timezone.utc)
    if now - seen > _SEEN_EVERY:
        session.last_seen = now
        db.commit()
    user.session_id = sid  # do „to urządzenie” na liście sesji
    return user


def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    return _user_from_token(creds.credentials if creds else None, db)


def current_user_stream(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str | None, Query()] = None,
) -> User:
    """Jak current_user, ale przyjmuje też ?token=… — odtwarzacze audio
    (np. <audio> w przeglądarce) nie zawsze potrafią wysłać nagłówek."""
    return _user_from_token(creds.credentials if creds else token, db, media_ok=creds is None)


def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_admin:
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.admin_required")
    return user


def curator_user(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.can_curate:
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.curator_required")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
CuratorUser = Annotated[User, Depends(curator_user)]
StreamUser = Annotated[User, Depends(current_user_stream)]
AdminUser = Annotated[User, Depends(admin_user)]
DB = Annotated[Session, Depends(get_db)]
