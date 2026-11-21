"""Strona główna (Home): przypięci wykonawcy, albumy i playlisty (osobno dla każdego konta)."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select

from ..errors import AppError
from ..db.models import Album, Artist, Pin
from ..schemas import HomeOut, PinOut, PinRequest, PlaylistOut
from ..security import DB, CurrentUser
from .convert import cover_id
from .me import favorite_count, recently_played_albums
from .playlists import get_access, playlist_out
from .library import _genre_counts, _top, albums_with_counts, artist_out, genre_list

router = APIRouter(prefix="/api", tags=["home"])

# „Ostatnio słuchane” i „Ostatnio dodane” – tyle, ile ma „Mix dla Ciebie”.
HOME_LIMIT = 30


def _resolve(db, user, pin: Pin) -> PinOut | None:
    if pin.kind == "album":
        found = albums_with_counts(db, select(Album).where(Album.id == pin.ref_id))
        return PinOut(kind="album", id=pin.ref_id, album=found[0]) if found else None
    if pin.kind == "artist":
        a = db.get(Artist, pin.ref_id)
        if a is None:
            return None
        genres = _top(_genre_counts(db, Album.artist_id, [a.id]), a.id)
        return PinOut(kind="artist", id=a.id, artist=artist_out(a, len(a.albums), genres))
    try:
        p = get_access(db, pin.ref_id, user)
    except HTTPException:
        return None
    return PinOut(kind="playlist", id=p.id, playlist=playlist_out(db, p, user))


@router.get("/home", response_model=HomeOut)
def home(user: CurrentUser, db: DB):
    pins = db.scalars(select(Pin).where(Pin.user_id == user.id).order_by(Pin.position, Pin.id)).all()
    out = []
    for pin in pins:
        resolved = _resolve(db, user, pin)
        if resolved is None:
            db.delete(pin)  # przypięty element został usunięty
        else:
            out.append(resolved)
    db.commit()
    recent = albums_with_counts(db, select(Album), limit=HOME_LIMIT, sort="added")
    return HomeOut(pins=out, recent_albums=recent, genres=genre_list(db, limit=16),
                   recently_played=recently_played_albums(db, user, HOME_LIMIT), favorite_count=favorite_count(db, user))


@router.post("/pins", response_model=PinOut, status_code=status.HTTP_201_CREATED)
def add_pin(body: PinRequest, user: CurrentUser, db: DB):
    pin = db.scalar(select(Pin).where(Pin.user_id == user.id, Pin.kind == body.kind, Pin.ref_id == body.id))
    if pin is None:
        last = db.scalar(select(func.max(Pin.position)).where(Pin.user_id == user.id)) or 0
        pin = Pin(user_id=user.id, kind=body.kind, ref_id=body.id, position=last + 1)
        db.add(pin)
        db.flush()
    resolved = _resolve(db, user, pin)
    if resolved is None:
        db.rollback()
        raise AppError(status.HTTP_404_NOT_FOUND, "pin.nothing")
    db.commit()
    return resolved


@router.delete("/pins/{kind}/{ref_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_pin(kind: str, ref_id: int, user: CurrentUser, db: DB):
    db.execute(delete(Pin).where(Pin.user_id == user.id, Pin.kind == kind, Pin.ref_id == ref_id))
    db.commit()


@router.put("/pins", status_code=status.HTTP_204_NO_CONTENT)
def reorder_pins(body: list[PinRequest], user: CurrentUser, db: DB):
    """Nowa kolejność przypiętych (lista w docelowej kolejności)."""
    pins = {(p.kind, p.ref_id): p for p in db.scalars(select(Pin).where(Pin.user_id == user.id))}
    for i, item in enumerate(body):
        if (p := pins.get((item.kind, item.id))) is not None:
            p.position = i
    db.commit()
