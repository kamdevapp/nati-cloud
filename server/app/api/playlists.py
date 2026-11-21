"""Playlisty: własne każdego użytkownika i udostępnione mu przez innych."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ..errors import AppError
from ..db.models import Album, Playlist, PlaylistItem, PlaylistShare, Track, User
from ..schemas import PlaylistCreate, PlaylistDetail, PlaylistOut, PlaylistUpdate, ShareIn, ShareOut
from ..security import DB, CurrentUser
from .convert import cover_id, track_out

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def display_name(u: User) -> str:
    full = " ".join(x for x in (u.first_name, u.last_name) if x)
    return full or u.username


def get_access(db, playlist_id: int, user, write: bool = False, owner: bool = False) -> Playlist:
    """Playlista, do której użytkownik ma dostęp: własna albo udostępniona mu.
    [write] – potrzebne prawo zmieniania, [owner] – tylko właściciel (usuwanie, udostępnianie)."""
    pl = db.get(Playlist, playlist_id)
    not_found = AppError(status.HTTP_404_NOT_FOUND, "playlist.not_found")
    if pl is None:
        raise not_found
    if pl.owner_id == user.id:
        return pl
    share = db.scalar(select(PlaylistShare).where(PlaylistShare.playlist_id == pl.id,
                                                  PlaylistShare.user_id == user.id))
    if share is None:
        raise not_found
    if owner or (write and not share.can_edit):
        raise AppError(status.HTTP_403_FORBIDDEN, "playlist.owner_only")
    return pl


def _set_tracks(db, pl: Playlist, track_ids: list[int]) -> None:
    existing = set(db.scalars(select(Track.id).where(Track.id.in_(track_ids))))
    missing = [t for t in track_ids if t not in existing]
    if missing:
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "playlist.unknown_tracks", ids=str(missing))
    pl.items.clear()
    db.flush()
    pl.items.extend(PlaylistItem(track_id=tid, position=i) for i, tid in enumerate(track_ids))


def _meta(db, pl: Playlist, user) -> dict:
    """Pola udostępniania dla danego użytkownika."""
    mine = pl.owner_id == user.id
    owner = db.get(User, pl.owner_id)
    if mine:
        can_edit = True
    else:
        can_edit = bool(db.scalar(select(PlaylistShare.can_edit).where(
            PlaylistShare.playlist_id == pl.id, PlaylistShare.user_id == user.id)))
    shares = db.scalar(select(func.count(PlaylistShare.id)).where(PlaylistShare.playlist_id == pl.id)) or 0
    return dict(owner_id=pl.owner_id, owner_name=display_name(owner) if owner else None, mine=mine,
                can_edit=can_edit, share_count=shares)


def playlist_out(db, pl: Playlist, user) -> PlaylistOut:
    count, duration = db.execute(
        select(func.count(PlaylistItem.id), func.coalesce(func.sum(Track.duration), 0.0))
        .select_from(PlaylistItem).join(Track, Track.id == PlaylistItem.track_id)
        .where(PlaylistItem.playlist_id == pl.id)
    ).one()
    return PlaylistOut(id=pl.id, name=pl.name, track_count=count, duration=duration, updated_at=pl.updated_at,
                       image_id=cover_id(pl.image), banner_id=cover_id(pl.banner), icon=pl.icon, color1=pl.color1, color2=pl.color2,
                       **_meta(db, pl, user))


def _detail(db, pl: Playlist, user) -> PlaylistDetail:
    items = db.scalars(
        select(PlaylistItem)
        .where(PlaylistItem.playlist_id == pl.id)
        .options(joinedload(PlaylistItem.track).joinedload(Track.album).joinedload(Album.artist))
        .order_by(PlaylistItem.position)
    ).all()
    tracks = [track_out(i.track) for i in items]
    return PlaylistDetail(
        id=pl.id,
        name=pl.name,
        track_count=len(tracks),
        duration=sum(t.duration for t in tracks),
        updated_at=pl.updated_at,
        image_id=cover_id(pl.image), banner_id=cover_id(pl.banner),
        icon=pl.icon, color1=pl.color1, color2=pl.color2,
        tracks=tracks,
        **_meta(db, pl, user),
    )


@router.get("", response_model=list[PlaylistOut])
def list_playlists(user: CurrentUser, db: DB):
    """Własne playlisty."""
    pls = db.scalars(select(Playlist).where(Playlist.owner_id == user.id).order_by(func.lower(Playlist.name)))
    return [playlist_out(db, p, user) for p in pls]


@router.get("/shared", response_model=list[PlaylistOut])
def shared_with_me(user: CurrentUser, db: DB):
    """Playlisty, które inni udostępnili temu użytkownikowi."""
    pls = db.scalars(
        select(Playlist).join(PlaylistShare, PlaylistShare.playlist_id == Playlist.id)
        .where(PlaylistShare.user_id == user.id).order_by(func.lower(Playlist.name))
    )
    return [playlist_out(db, p, user) for p in pls]


@router.post("", response_model=PlaylistDetail, status_code=status.HTTP_201_CREATED)
def create_playlist(body: PlaylistCreate, user: CurrentUser, db: DB):
    pl = Playlist(name=body.name, owner_id=user.id)
    db.add(pl)
    db.flush()
    _set_tracks(db, pl, body.track_ids)
    db.commit()
    return _detail(db, pl, user)


@router.get("/{playlist_id}", response_model=PlaylistDetail)
def get_playlist(playlist_id: int, user: CurrentUser, db: DB):
    return _detail(db, get_access(db, playlist_id, user), user)


@router.patch("/{playlist_id}", response_model=PlaylistDetail)
def update_playlist(playlist_id: int, body: PlaylistUpdate, user: CurrentUser, db: DB):
    pl = get_access(db, playlist_id, user, write=True)
    if body.name is not None:
        pl.name = body.name
    if body.track_ids is not None:
        _set_tracks(db, pl, body.track_ids)
    for field in ("icon", "color1", "color2"):
        if field in body.model_fields_set:
            setattr(pl, field, getattr(body, field) or None)
    pl.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pl)
    return _detail(db, pl, user)


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_playlist(playlist_id: int, user: CurrentUser, db: DB):
    pl = db.get(Playlist, playlist_id)
    if pl is not None and pl.owner_id != user.id:
        # Udostępniona komuś – „usunięcie” to rezygnacja z niej (u właściciela zostaje).
        share = db.scalar(select(PlaylistShare).where(PlaylistShare.playlist_id == pl.id,
                                                      PlaylistShare.user_id == user.id))
        if share is not None:
            db.delete(share)
            db.commit()
            return
    db.delete(get_access(db, playlist_id, user, owner=True))
    db.commit()


# ---------------------------------------------------------------- udostępnianie

@router.get("/{playlist_id}/shares", response_model=list[ShareOut])
def list_shares(playlist_id: int, user: CurrentUser, db: DB):
    from .auth import user_brief
    pl = get_access(db, playlist_id, user, owner=True)
    rows = db.execute(select(PlaylistShare, User).join(User, User.id == PlaylistShare.user_id)
                      .where(PlaylistShare.playlist_id == pl.id).order_by(User.username)).all()
    return [ShareOut(user=user_brief(u), can_edit=s.can_edit) for s, u in rows]


@router.put("/{playlist_id}/shares", response_model=list[ShareOut])
def set_shares(playlist_id: int, body: list[ShareIn], user: CurrentUser, db: DB):
    """Ustawia, komu playlista jest udostępniona (pełna lista – pozostałym dostęp jest odbierany)."""
    pl = get_access(db, playlist_id, user, owner=True)
    wanted = {s.user_id: s.can_edit for s in body if s.user_id != user.id}
    known = set(db.scalars(select(User.id).where(User.id.in_(wanted))))
    if unknown := set(wanted) - known:
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "playlist.unknown_users", ids=str(sorted(unknown)))
    existing = {s.user_id: s for s in db.scalars(select(PlaylistShare).where(PlaylistShare.playlist_id == pl.id))}
    for uid, share in existing.items():
        if uid not in wanted:
            db.delete(share)
        else:
            share.can_edit = wanted[uid]
    for uid, can_edit in wanted.items():
        if uid not in existing:
            db.add(PlaylistShare(playlist_id=pl.id, user_id=uid, can_edit=can_edit))
    db.commit()
    return list_shares(playlist_id, user, db)
