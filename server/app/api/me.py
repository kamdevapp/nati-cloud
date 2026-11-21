"""Rzeczy osobiste każdego użytkownika: ulubione utwory i historia słuchania."""

import mimetypes
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import joinedload

from ..errors import AppError
from ..db.models import Album, Favorite, PlayHistory, Track
from ..config import get_settings
from ..schemas import AlbumOut, HistoryEntry, HistoryIn, TrackOut
from ..security import DB, CurrentUser
from .convert import cover_id, track_out

router = APIRouter(prefix="/api", tags=["me"])

# Ten sam utwór zapisany ponownie w tak krótkim czasie to ten sam odsłuch (np. przewinięcie wstecz).
SAME_PLAY = timedelta(minutes=2)


def _track(db, track_id: int) -> Track:
    track = db.get(Track, track_id)
    if track is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "track.not_found")
    return track


def _tracks(db, stmt) -> list[Track]:
    return list(db.scalars(stmt.options(joinedload(Track.album).joinedload(Album.artist))).unique())


# ---------------------------------------------------------------- ulubione

@router.get("/favorites", response_model=list[TrackOut])
def favorites(user: CurrentUser, db: DB):
    """Ulubione utwory – w kolejności ustawionej przez użytkownika, a bez niej od ostatnio dodanych."""
    tracks = _tracks(db, select(Track).join(Favorite, Favorite.track_id == Track.id)
                     .where(Favorite.user_id == user.id)
                     .order_by(Favorite.position, Favorite.created_at.desc(), Favorite.id.desc()))
    return [track_out(t) for t in tracks]


@router.get("/favorites/ids", response_model=list[int])
def favorite_ids(user: CurrentUser, db: DB):
    return list(db.scalars(select(Favorite.track_id).where(Favorite.user_id == user.id)))


@router.put("/favorites/{track_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def add_favorite(track_id: int, user: CurrentUser, db: DB):
    _track(db, track_id)
    if db.scalar(select(Favorite).where(Favorite.user_id == user.id, Favorite.track_id == track_id)) is None:
        # Kolejność ustawiona ręcznie – nowy ulubiony trafia na początek.
        first = db.scalar(select(func.min(Favorite.position)).where(Favorite.user_id == user.id))
        db.add(Favorite(user_id=user.id, track_id=track_id, position=None if first is None else first - 1))
        db.commit()


@router.delete("/favorites/{track_id:int}", status_code=status.HTTP_204_NO_CONTENT)
def remove_favorite(track_id: int, user: CurrentUser, db: DB):
    db.execute(delete(Favorite).where(Favorite.user_id == user.id, Favorite.track_id == track_id))
    db.commit()


@router.put("/favorites/order", status_code=status.HTTP_204_NO_CONTENT)
def reorder_favorites(track_ids: list[int], user: CurrentUser, db: DB):
    """Nowa kolejność ulubionych (id utworów w docelowej kolejności; brakujące idą na koniec)."""
    favs = {f.track_id: f for f in db.scalars(select(Favorite).where(Favorite.user_id == user.id))}
    order = [t for t in dict.fromkeys(track_ids) if t in favs]
    rest = sorted((f for t, f in favs.items() if t not in set(order)),
                  key=lambda f: (f.position is None, f.position or 0, -f.created_at.timestamp()))
    for i, f in enumerate([favs[t] for t in order] + rest):
        f.position = i
    db.commit()


# ---------------------------------------------------------------- okładka „Ulubionych”

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class FavoritesLook(BaseModel):
    icon: str | None = Field(default=None, max_length=64)
    color1: str | None = Field(default=None, pattern=_HEX.pattern)
    color2: str | None = Field(default=None, pattern=_HEX.pattern)
    image_id: str | None = None
    banner_id: str | None = None  # zdjęcie w tle nagłówka


def _look(user) -> FavoritesLook:
    return FavoritesLook(icon=user.fav_icon, color1=user.fav_color1, color2=user.fav_color2,
                         image_id=cover_id(user.fav_image), banner_id=cover_id(user.fav_banner))


@router.get("/favorites/look", response_model=FavoritesLook)
def favorites_look(user: CurrentUser):
    return _look(user)


@router.put("/favorites/look", response_model=FavoritesLook)
def set_favorites_look(body: FavoritesLook, user: CurrentUser, db: DB):
    user.fav_icon, user.fav_color1, user.fav_color2 = body.icon, body.color1, body.color2
    db.commit()
    return _look(user)


# Obrazek okładki (image) i zdjęcie w tle nagłówka (banner) „Ulubionych”.
_FAV_FIELDS = {"image": "fav_image", "banner": "fav_banner"}
FavPart = Annotated[str, Path(pattern="^(image|banner)$")]


@router.get("/favorites/{part}")
def favorites_image(part: FavPart, user: CurrentUser):
    name = getattr(user, _FAV_FIELDS[part])
    path = get_settings().covers_dir / name if name else None
    if path is None or not path.is_file():
        raise AppError(status.HTTP_404_NOT_FOUND, "image.not_found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800"})


async def _save_fav(request: Request, user, db, part: str, data: bytes | None) -> FavoritesLook:
    from .edit import _errors

    editor = request.app.state.editor
    with _errors():
        name = await run_in_threadpool(editor.save_image, data) if data is not None else None
    setattr(user, _FAV_FIELDS[part], name)
    db.commit()
    editor.cleanup_images()
    return _look(user)


@router.put("/favorites/{part}", response_model=FavoritesLook)
async def upload_favorites_image(part: FavPart, request: Request, user: CurrentUser, db: DB):
    from .edit import _body_image

    return await _save_fav(request, user, db, part, await _body_image(request))


class _Url(BaseModel):
    url: str = Field(max_length=2048)


@router.post("/favorites/{part}/url", response_model=FavoritesLook)
async def favorites_image_from_url(part: FavPart, body: _Url, request: Request, user: CurrentUser, db: DB):
    from .edit import _download_image

    return await _save_fav(request, user, db, part, await _download_image(body.url))


@router.delete("/favorites/{part}", response_model=FavoritesLook)
async def remove_favorites_image(part: FavPart, request: Request, user: CurrentUser, db: DB):
    return await _save_fav(request, user, db, part, None)


def favorite_count(db, user) -> int:
    return db.scalar(select(func.count(Favorite.id)).where(Favorite.user_id == user.id)) or 0


# ---------------------------------------------------------------- historia

@router.post("/history", status_code=status.HTTP_204_NO_CONTENT)
def add_history(body: HistoryIn, user: CurrentUser, db: DB):
    """Aplikacja zgłasza utwór, którego słuchano dłuższą chwilę."""
    _track(db, body.track_id)
    last = db.scalar(select(PlayHistory).where(PlayHistory.user_id == user.id)
                     .order_by(PlayHistory.played_at.desc()).limit(1))
    now = datetime.now(timezone.utc)
    if last and last.track_id == body.track_id:
        played = last.played_at if last.played_at.tzinfo else last.played_at.replace(tzinfo=timezone.utc)
        if now - played < SAME_PLAY:
            return
    db.add(PlayHistory(user_id=user.id, track_id=body.track_id, played_at=now))
    db.commit()


@router.get("/history", response_model=list[HistoryEntry])
def history(user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    rows = db.execute(
        select(PlayHistory, Track).join(Track, Track.id == PlayHistory.track_id)
        .options(joinedload(Track.album).joinedload(Album.artist))
        .where(PlayHistory.user_id == user.id)
        .order_by(PlayHistory.played_at.desc(), PlayHistory.id.desc()).limit(limit)
    ).unique().all()
    return [HistoryEntry(track=track_out(t), played_at=h.played_at) for h, t in rows]


@router.delete("/history", status_code=status.HTTP_204_NO_CONTENT)
def clear_history(user: CurrentUser, db: DB):
    db.execute(delete(PlayHistory).where(PlayHistory.user_id == user.id))
    db.commit()


def recently_played_albums(db, user, limit: int = 12) -> list[AlbumOut]:
    """Albumy z historii użytkownika, od ostatnio słuchanego (każdy raz)."""
    from .library import albums_with_counts
    last = (select(Track.album_id, func.max(PlayHistory.played_at).label("at"))
            .join(Track, Track.id == PlayHistory.track_id)
            .where(PlayHistory.user_id == user.id).group_by(Track.album_id)
            .order_by(func.max(PlayHistory.played_at).desc()).limit(limit))
    order = [row.album_id for row in db.execute(last)]
    if not order:
        return []
    found = {a.id: a for a in albums_with_counts(db, select(Album).where(Album.id.in_(order)))}
    return [found[i] for i in order if i in found]
