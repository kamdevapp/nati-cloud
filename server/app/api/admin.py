"""Administracja chmurą: zajęte miejsce, kopie zapasowe, wyczyszczenie, ponowne uruchomienie."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..errors import AppError, CodedError
from ..config import get_settings
from ..db.models import Track
from ..security import DB, AdminUser, verify_password
from ..services import maintenance as m
from ..services import server_settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _backups(request: Request) -> m.Backups:
    if not hasattr(request.app.state, "backups"):
        request.app.state.backups = m.Backups(get_settings())
    return request.app.state.backups


def _confirm(user, password: str) -> None:
    if not verify_password(user.password_hash, password):
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.wrong_password")


# ---------------------------------------------------------------- ustawienia chmury


class CloudSettings(BaseModel):
    online_enabled: bool  # szukanie, odsłuch i pobieranie muzyki z internetu do chmury


@router.get("/settings", response_model=CloudSettings)
def get_cloud_settings(user: AdminUser):
    return CloudSettings(online_enabled=server_settings.online_enabled())


@router.put("/settings", response_model=CloudSettings)
def set_cloud_settings(body: CloudSettings, user: AdminUser, db: DB):
    """Działa od razu – bez restartu serwera i bez ponownej instalacji."""
    server_settings.set_online_enabled(db, body.online_enabled)
    return CloudSettings(online_enabled=server_settings.online_enabled())


# ---------------------------------------------------------------- miejsce

@router.get("/storage")
async def storage(_: AdminUser, db: DB):
    """Ile zajmuje muzyka, okładki, kosz, pamięć podręczna, baza i kopie + wolne miejsce na dysku."""
    sizes = [(p, n) for p, n in db.execute(select(Track.path, Track.size))]
    return await run_in_threadpool(m.storage, get_settings(), sizes)


@router.delete("/storage/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_storage(key: Literal["trash", "cache"], _: AdminUser):
    """Opróżnia kosz albo pamięć podręczną odsłuchu."""
    await run_in_threadpool(m.clear, get_settings(), key)


# ---------------------------------------------------------------- kopie zapasowe

class BackupRequest(BaseModel):
    include_downloads: bool = False  # też muzyka w chmurze – pobrana i wgrana (duży plik)


@router.get("/backups")
def list_backups(request: Request, _: AdminUser):
    return {"backups": m.list_backups(get_settings()), "status": _backups(request).as_dict(),
            "pending": m.pending(get_settings())}


@router.post("/backups", status_code=status.HTTP_202_ACCEPTED)
def create_backup(body: BackupRequest, request: Request, _: AdminUser):
    if not _backups(request).start(body.include_downloads):
        raise AppError(status.HTTP_409_CONFLICT, "backup.in_progress")
    return _backups(request).as_dict()


@router.get("/backups/{name}")
def download_backup(name: str, _: AdminUser):
    try:
        path = m.backup_path(get_settings(), name)
    except FileNotFoundError:
        raise AppError(status.HTTP_404_NOT_FOUND, "backup.not_found") from None
    return FileResponse(path, media_type="application/x-tar", filename=path.name)


@router.delete("/backups/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_backup(name: str, _: AdminUser):
    try:
        m.backup_path(get_settings(), name).unlink()
    except FileNotFoundError:
        raise AppError(status.HTTP_404_NOT_FOUND, "backup.not_found") from None


@router.put("/backups/upload")
async def upload_backup(request: Request, _: AdminUser):
    """Wgranie kopii z dysku (treść zapytania = plik .tar) – potem można ją przywrócić."""
    folder = m.backups_dir(get_settings())
    folder.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    target = folder / f"nati-wgrana-{datetime.now():%Y-%m-%d-%H%M%S}.tar"
    part = target.with_suffix(".tar.part")
    try:
        with part.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        await run_in_threadpool(m.read_manifest, part)
    except CodedError as e:  # to nie kopia Nati / z nowszej wersji
        part.unlink(missing_ok=True)
        raise AppError.of(e) from e
    part.rename(target)
    return {"name": target.name}


class DangerConfirm(BaseModel):
    password: str = Field(min_length=1, max_length=256)  # hasło administratora – potwierdzenie


@router.post("/backups/{name}/restore")
def restore_backup(name: str, body: DangerConfirm, _admin: AdminUser):
    """Zleca przywrócenie kopii i uruchamia serwer ponownie (obecne dane zostają zastąpione)."""
    _confirm(_admin, body.password)
    try:
        manifest = m.schedule_restore(get_settings(), name)
    except FileNotFoundError:
        raise AppError(status.HTTP_404_NOT_FOUND, "backup.not_found") from None
    except CodedError as e:
        raise AppError.of(e) from e
    m.restart_later()
    return {"restarting": True, "manifest": manifest}


class WipeRequest(DangerConfirm):
    include_downloads: bool = False  # usuń też muzykę w chmurze (pobraną i wgraną)
    confirm: Annotated[str, Field(pattern="^WYCZYŚĆ$")]  # wpisane słowo – żeby nie kliknąć przypadkiem


@router.post("/wipe")
def wipe(body: WipeRequest, _admin: AdminUser):
    """Całkowite wyczyszczenie chmury: konta, playlisty, biblioteka, okładki, kosz, pamięć podręczna.
    Twój folder z muzyką zostaje nietknięty; kopie zapasowe też zostają. Serwer uruchamia się ponownie
    jak nowy (pierwsze konto = administrator)."""
    _confirm(_admin, body.password)
    m.schedule_wipe(get_settings(), body.include_downloads)
    m.restart_later()
    return {"restarting": True}


@router.post("/restart")
def restart(_: AdminUser):
    m.restart_later()
    return {"restarting": True}
