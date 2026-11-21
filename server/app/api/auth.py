"""Logowanie, konfiguracja pierwszego uruchomienia, profil i zarządzanie użytkownikami.

Role: administrator – pełna kontrola (biblioteka, dodawanie muzyki, konta);
użytkownik – słucha całej biblioteki i tworzy własne playlisty (każdy ma swój Home i swoje playlisty)."""

import mimetypes

import json
from datetime import timezone

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, func, select

from ..errors import AppError, error_body
from .. import __version__
from ..config import get_settings
from ..db.models import Favorite, Pin, PlayHistory, Playlist, User, UserSession
from ..schemas import (BackupCodes, Credentials, LoginIn, NewUser, PasswordChange, ProfileUpdate, ServerInfo,
                       SessionOut, TokenOut, TwoFactorCode, TwoFactorDisable, TwoFactorSetup, UserAdminUpdate,
                       UserBrief, UserOut)
from ..security import (DB, MEDIA_TTL, AdminUser, CurrentUser, StreamUser, create_media_token, create_session,
                        create_token, hash_password, verify_password)
from ..services import server_settings, totp
from ..services.login_guard import guard
from .convert import cover_id

router = APIRouter(prefix="/api", tags=["auth"])


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, is_admin=u.is_admin, role=u.role, first_name=u.first_name,
                   last_name=u.last_name, avatar_id=cover_id(u.avatar), two_factor=bool(u.totp_enabled))


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _device(request: Request, name: str | None) -> str:
    """Nazwa urządzenia z aplikacji, a bez niej – z nagłówka User-Agent (np. przeglądarka)."""
    return (name or request.headers.get("user-agent") or "Unknown device").strip()[:128]


def _logged_in(db, user: User, request: Request, device: str | None) -> TokenOut:
    session = create_session(db, user, _device(request, device), _ip(request))
    return TokenOut(access_token=create_token(user, session), user=user_out(user))


def _use_backup_code(user: User, code: str) -> bool:
    hashes = json.loads(user.backup_codes or "[]")
    h = totp.hash_backup_code(code)
    if h not in hashes:
        return False
    hashes.remove(h)
    user.backup_codes = json.dumps(hashes)
    return True


def check_second_factor(user: User, code: str | None) -> bool:
    """Kod z aplikacji albo (jednorazowy) kod zapasowy. Bez włączonej weryfikacji – zawsze tak."""
    if not user.totp_enabled:
        return True
    if not code:
        return False
    return totp.verify(user.totp_secret or "", code) or _use_backup_code(user, code)


def user_brief(u: User) -> UserBrief:
    return UserBrief(id=u.id, username=u.username, first_name=u.first_name, last_name=u.last_name,
                     avatar_id=cover_id(u.avatar))


def _user_count(db) -> int:
    return db.scalar(select(func.count(User.id))) or 0


def _admins(db) -> int:
    return db.scalar(select(func.count(User.id)).where(User.is_admin.is_(True))) or 0


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


@router.get("/info", response_model=ServerInfo)
def server_info(db: DB):
    """Publiczne – aplikacja sprawdza tu, czy pod adresem jest serwer Nati."""
    return ServerInfo(version=__version__, setup_required=_user_count(db) == 0,
                      online_enabled=server_settings.online_enabled())


@router.post("/setup", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def setup(creds: Credentials, request: Request, db: DB):
    """Tworzy pierwsze konto (administratora). Działa tylko, gdy nie ma żadnych kont."""
    if _user_count(db) > 0:
        raise AppError(status.HTTP_409_CONFLICT, "setup.already_done")
    user = User(username=creds.username, password_hash=hash_password(creds.password), is_admin=True)
    db.add(user)
    db.commit()
    return _logged_in(db, user, request, None)


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: DB):
    """Logowanie. Przy włączonej weryfikacji dwuetapowej bez kodu – 401 z `mfa_required: true`
    (aplikacja pyta wtedy o kod i wysyła logowanie jeszcze raz z polem `code`).
    Po kilku nieudanych próbach – 429 i chwila przerwy."""
    ip = _ip(request)
    if wait := guard.locked_for(body.username, ip):
        raise AppError(status.HTTP_429_TOO_MANY_REQUESTS, "auth.locked", headers={"Retry-After": str(wait)},
                       minutes=max(1, round(wait / 60)))
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(user.password_hash, body.password):
        guard.failed(body.username, ip)
        raise AppError(status.HTTP_401_UNAUTHORIZED, "auth.bad_credentials")
    if user.totp_enabled and not body.code:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED,
                            content=error_body("auth.code_required", mfa_required=True))
    if not check_second_factor(user, body.code):
        guard.failed(body.username, ip)
        db.rollback()
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED,
                            content=error_body("auth.bad_code", mfa_required=True))
    guard.succeeded(body.username)
    return _logged_in(db, user, request, body.device)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser, db: DB):
    """Wylogowuje to urządzenie (token przestaje działać)."""
    db.execute(delete(UserSession).where(UserSession.id == user.session_id))
    db.commit()


@router.get("/auth/media-token")
def media_token(user: CurrentUser):
    """Krótki token (12 h) do adresów strumieni – zamiast głównego tokenu w adresie."""
    return {"token": create_media_token(user, user.session_id), "expires_in": int(MEDIA_TTL.total_seconds())}


# ---------------------------------------------------------------- zalogowane urządzenia

@router.get("/auth/sessions", response_model=list[SessionOut])
def sessions(user: CurrentUser, db: DB):
    rows = db.scalars(select(UserSession).where(UserSession.user_id == user.id)
                      .order_by(UserSession.last_seen.desc()))
    def utc(t):  # SQLite zwraca czas bez strefy – zapisujemy UTC, więc tak go oznaczamy
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    return [SessionOut(id=s.id, device=s.device, ip=s.ip, created_at=utc(s.created_at), last_seen=utc(s.last_seen),
                       current=s.id == user.session_id) for s in rows]


@router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def end_session(session_id: str, user: CurrentUser, db: DB):
    """Wylogowuje wybrane urządzenie."""
    n = db.execute(delete(UserSession).where(UserSession.id == session_id, UserSession.user_id == user.id)).rowcount
    if not n:
        raise AppError(status.HTTP_404_NOT_FOUND, "session.not_found")
    db.commit()


@router.delete("/auth/sessions", status_code=status.HTTP_204_NO_CONTENT)
def end_other_sessions(user: CurrentUser, db: DB):
    """Wylogowuje wszystkie inne urządzenia (to zostaje zalogowane)."""
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.id != user.session_id))
    db.commit()


# ---------------------------------------------------------------- weryfikacja dwuetapowa

@router.post("/auth/2fa/setup", response_model=TwoFactorSetup)
def two_factor_setup(user: CurrentUser, db: DB):
    """Nowy sekret (jeszcze nieaktywny) – aplikacja pokazuje kod QR, potem potwierdzenie kodem."""
    if user.totp_enabled:
        raise AppError(status.HTTP_409_CONFLICT, "twofa.already_enabled")
    user.totp_secret = totp.new_secret()
    db.commit()
    return TwoFactorSetup(secret=user.totp_secret, uri=totp.uri(user.totp_secret, user.username))


@router.post("/auth/2fa/enable", response_model=BackupCodes)
def two_factor_enable(body: TwoFactorCode, user: CurrentUser, db: DB):
    """Włącza weryfikację po podaniu kodu z aplikacji. Zwraca kody zapasowe (pokazywane tylko raz)."""
    if user.totp_enabled:
        raise AppError(status.HTTP_409_CONFLICT, "twofa.already_enabled")
    if not user.totp_secret or not totp.verify(user.totp_secret, body.code):
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "twofa.code_mismatch_time")
    codes = totp.new_backup_codes()
    user.totp_enabled = True
    user.backup_codes = json.dumps([totp.hash_backup_code(c) for c in codes])
    # Inne urządzenia logowały się bez drugiego kroku – wylogowujemy je.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.id != user.session_id))
    db.commit()
    return BackupCodes(codes=codes)


@router.post("/auth/2fa/backup-codes", response_model=BackupCodes)
def two_factor_new_codes(body: TwoFactorCode, user: CurrentUser, db: DB):
    """Nowe kody zapasowe (stare przestają działać)."""
    if not user.totp_enabled or not totp.verify(user.totp_secret or "", body.code):
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "twofa.code_mismatch")
    codes = totp.new_backup_codes()
    user.backup_codes = json.dumps([totp.hash_backup_code(c) for c in codes])
    db.commit()
    return BackupCodes(codes=codes)


@router.post("/auth/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
def two_factor_disable(body: TwoFactorDisable, user: CurrentUser, db: DB):
    if not verify_password(user.password_hash, body.password):
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.wrong_password")
    if not check_second_factor(user, body.code):
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "twofa.code_mismatch")
    user.totp_enabled, user.totp_secret, user.backup_codes = False, None, None
    db.commit()


# ---------------------------------------------------------------- własny profil

@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser):
    return user_out(user)


@router.patch("/auth/me", response_model=UserOut)
def update_me(body: ProfileUpdate, user: CurrentUser, db: DB):
    for field in body.model_fields_set:
        setattr(user, field, _clean(getattr(body, field)))
    db.commit()
    return user_out(user)


@router.post("/auth/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChange, user: CurrentUser, db: DB):
    if not verify_password(user.password_hash, body.current_password):
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.current_password_wrong")
    user.password_hash = hash_password(body.new_password)
    # Nowe hasło – pozostałe urządzenia muszą zalogować się ponownie.
    db.execute(delete(UserSession).where(UserSession.user_id == user.id, UserSession.id != user.session_id))
    db.commit()


@router.put("/auth/me/avatar", response_model=UserOut)
async def upload_avatar(request: Request, user: CurrentUser, db: DB):
    data = await request.body()
    if not data:
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "image.missing_body")
    editor = request.app.state.editor
    from .edit import _errors
    with _errors():
        user.avatar = editor.save_image(data)
    db.commit()
    editor.cleanup_images()
    return user_out(user)


@router.delete("/auth/me/avatar", response_model=UserOut)
def remove_avatar(request: Request, user: CurrentUser, db: DB):
    user.avatar = None
    db.commit()
    request.app.state.editor.cleanup_images()
    return user_out(user)


@router.get("/users/{user_id}/avatar")
def avatar(user_id: int, _: StreamUser, db: DB):
    u = db.get(User, user_id)
    path = get_settings().covers_dir / u.avatar if u and u.avatar else None
    if path is None or not path.is_file():
        raise AppError(status.HTTP_404_NOT_FOUND, "photo.not_found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800"})


@router.get("/users/directory", response_model=list[UserBrief])
def directory(user: CurrentUser, db: DB):
    """Inni użytkownicy chmury – komu można udostępnić playlistę."""
    return [user_brief(u) for u in db.scalars(select(User).where(User.id != user.id).order_by(User.username))]


# ---------------------------------------------------------------- konta (administrator)

@router.get("/users", response_model=list[UserOut])
def list_users(_: AdminUser, db: DB):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.username))]


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: NewUser, _: AdminUser, db: DB):
    if db.scalar(select(User).where(User.username == body.username)):
        raise AppError(status.HTTP_409_CONFLICT, "user.exists")
    role = body.role or ("admin" if body.is_admin else "user")
    user = User(username=body.username, password_hash=hash_password(body.password), is_admin=role == "admin",
                is_curator=role == "curator", first_name=_clean(body.first_name), last_name=_clean(body.last_name))
    db.add(user)
    db.commit()
    return user_out(user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserAdminUpdate, admin: AdminUser, db: DB):
    user = db.get(User, user_id)
    if user is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "user.not_found")
    role = body.role or (None if body.is_admin is None else "admin" if body.is_admin else "user")
    if role is not None and role != "admin" and user.is_admin and _admins(db) <= 1:
        raise AppError(status.HTTP_409_CONFLICT, "user.last_admin")
    if role is not None:
        user.is_admin = role == "admin"
        user.is_curator = role == "curator"
    if body.password:
        user.password_hash = hash_password(body.password)
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))  # wylogowanie wszędzie
    if body.disable_two_factor:
        user.totp_enabled, user.totp_secret, user.backup_codes = False, None, None
    for field in ("first_name", "last_name"):
        if field in body.model_fields_set:
            setattr(user, field, _clean(getattr(body, field)))
    db.commit()
    return user_out(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, request: Request, admin: AdminUser, db: DB):
    """Usuwa konto razem z jego playlistami, przypiętymi, ulubionymi i historią. Muzyka w chmurze zostaje."""
    user = db.get(User, user_id)
    if user is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "user.not_found")
    if user.id == admin.id:
        raise AppError(status.HTTP_409_CONFLICT, "user.cannot_delete_self")
    for pl in db.scalars(select(Playlist).where(Playlist.owner_id == user.id)):
        db.delete(pl)
    db.execute(delete(Pin).where(Pin.user_id == user.id))
    db.execute(delete(Favorite).where(Favorite.user_id == user.id))
    db.execute(delete(PlayHistory).where(PlayHistory.user_id == user.id))
    db.delete(user)
    db.commit()
    request.app.state.editor.cleanup_images()
