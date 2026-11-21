"""Jednorazowa zamiana dawnych „Nieznany wykonawca / album” (po polsku) na neutralne znaczniki."""

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..text import LEGACY_UNKNOWN_ALBUM, LEGACY_UNKNOWN_ARTIST, UNKNOWN_ALBUM, UNKNOWN_ARTIST, normalize
from .models import Album, Artist, Track


def _merge_album(db: Session, old: Album, artist_id: int, title: str) -> None:
    """Album dostaje nowego wykonawcę/tytuł; gdy taki już jest – utwory przechodzą do niego."""
    target = db.scalar(select(Album).where(Album.artist_id == artist_id, Album.title == title, Album.id != old.id))
    if target is None:
        old.artist_id, old.title = artist_id, title
        db.flush()
        return
    db.execute(update(Track).where(Track.album_id == old.id).values(album_id=target.id))
    target.cover = target.cover or old.cover
    db.flush()
    db.expire(old)  # lista utworów sprzed przeniesienia – inaczej usunięcie by je „odpięło”
    db.delete(old)
    db.flush()


def migrate_unknown(db: Session) -> int:
    """Zwraca liczbę zmienionych wykonawców i albumów (0 – nic do zrobienia)."""
    changed = 0
    for legacy in LEGACY_UNKNOWN_ARTIST:
        old = db.scalar(select(Artist).where(Artist.name == legacy))
        if old is not None:
            new = db.scalar(select(Artist).where(Artist.name == UNKNOWN_ARTIST))
            if new is None:
                old.name, old.search = UNKNOWN_ARTIST, normalize(UNKNOWN_ARTIST)
                db.flush()
            else:
                for album in list(old.albums):
                    _merge_album(db, album, new.id, album.title)
                db.expire(old)
                db.delete(old)
                db.flush()
            changed += 1
        db.execute(update(Track).where(Track.artist_name == legacy).values(artist_name=UNKNOWN_ARTIST))
    for legacy in LEGACY_UNKNOWN_ALBUM:
        for album in db.scalars(select(Album).where(Album.title == legacy)).all():
            _merge_album(db, album, album.artist_id, UNKNOWN_ALBUM)
            changed += 1
    if changed:
        # Tekst do wyszukiwania – z nowymi nazwami.
        for album in db.scalars(select(Album).where(Album.title == UNKNOWN_ALBUM)):
            album.search = normalize(album.title, album.artist.name)
        for track in db.scalars(select(Track).join(Album).join(Artist).where(
                (Track.artist_name == UNKNOWN_ARTIST) | (Album.title == UNKNOWN_ALBUM))):
            track.search = normalize(track.title, track.artist_name, track.album.title, track.album.artist.name)
    db.commit()
    return changed
