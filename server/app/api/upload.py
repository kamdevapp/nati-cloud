"""Wysyłanie muzyki z urządzenia do chmury (każdy zalogowany)."""

from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select

from ..errors import AppError
from ..config import get_settings
from ..db.models import Album, Artist, DownloadJob, Track
from ..schemas import TrackOut
from ..security import DB, AdminUser, CuratorUser, CurrentUser
from ..services.scanner import AUDIO_EXTENSIONS, read_meta
from ..services.tagging import safe_name
from .convert import track_out

router = APIRouter(prefix="/api", tags=["upload"])

MAX_UPLOAD = 2 * 1024**3  # 2 GB


def _duplicate(db, meta) -> Track | None:
    """Ten sam utwór już jest (tytuł, wykonawca, album i długość ±2 s)."""
    return db.scalar(
        select(Track).join(Album, Album.id == Track.album_id).join(Artist, Artist.id == Album.artist_id)
        .where(func.lower(Track.title) == meta.title.lower(), func.lower(Track.artist_name) == meta.artist.lower(),
               func.lower(Album.title) == meta.album.lower(),
               Track.duration.between(meta.duration - 2, meta.duration + 2))
        .limit(1))


@router.post("/upload", response_model=TrackOut, status_code=status.HTTP_201_CREATED)
async def upload(request: Request, user: CuratorUser, db: DB,
                 x_filename: Annotated[str, Header(max_length=1024)]):
    """Treść zapytania = plik audio; nazwa w nagłówku `X-Filename` (zakodowana jak w adresie URL).

    Plik trafia do `NATI_DOWNLOADS_DIR/Wykonawca/Album/` (wg tagów). Ten sam utwór drugi raz → 409."""
    settings = get_settings()
    name = Path(unquote(x_filename)).name
    ext = Path(name).suffix.lower()
    if ext not in AUDIO_EXTENSIONS:
        raise AppError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "upload.not_music", name=name)
    tmp_dir = settings.data_dir / "tmp" / "upload"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}{ext}"
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise AppError(status.HTTP_413_CONTENT_TOO_LARGE, "upload.too_large")
                f.write(chunk)
        meta = await run_in_threadpool(read_meta, tmp)
        if meta is None:
            raise AppError(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "upload.unknown_format", name=name)
        if meta.title == tmp.stem:
            meta.title = Path(name).stem  # bez tagu tytułu – nazwa pliku, nie tymczasowa
        if (dup := _duplicate(db, meta)) is not None:
            raise AppError(status.HTTP_409_CONFLICT, "upload.duplicate", headers={"X-Nati-Track-Id": str(dup.id)},
                           title=meta.title)
        folder = settings.online_dir / safe_name(meta.album_artist) / safe_name(meta.album)
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / safe_name(name)
        n = 2
        while dest.exists():
            dest = folder / f"{safe_name(Path(name).stem)} ({n}){ext}"
            n += 1
        shutil.move(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)

    track_id = await run_in_threadpool(request.app.state.scanner.index_file, dest)
    # Zapis „kto wysłał” – ta osoba może potem utwór usunąć (jak pobrany z internetu).
    db.add(DownloadJob(user_id=user.id, source="upload", source_id=name, title=meta.title, artist=meta.artist,
                       album=meta.album, album_artist=meta.album_artist, track_no=meta.track_no, year=meta.year,
                       thumbnail=None, status="done", progress=1.0, track_id=track_id,
                       finished_at=datetime.now(timezone.utc)))
    db.commit()
    if getattr(request.app.state, "enricher", None):
        request.app.state.enricher.request()
    from .library import _track_query
    return track_out(db.scalar(_track_query().where(Track.id == track_id)))
