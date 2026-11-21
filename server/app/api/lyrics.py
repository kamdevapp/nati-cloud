"""Teksty utworów: odczyt, edycja, usuwanie i pobieranie z internetu."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..errors import AppError
from ..config import get_settings
from ..db.models import Album, Track, TrackLyrics
from ..schemas import LyricsIn, LyricsOut
from ..security import DB, AdminUser, CuratorUser, CurrentUser
from ..services import lyrics as service

router = APIRouter(prefix="/api", tags=["lyrics"])
log = logging.getLogger(__name__)

# Gdy w internecie nie było tekstu, sprawdzamy ponownie dopiero po tylu dniach.
RETRY_DAYS = 30
NOT_FOUND = "lyrics.not_found"


def _track(db: Session, track_id: int) -> Track:
    track = db.scalar(select(Track).options(joinedload(Track.album).joinedload(Album.artist))
                      .where(Track.id == track_id))
    if track is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "track.not_found")
    return track


def _row(db: Session, track: Track) -> TrackLyrics | None:
    return db.scalar(select(TrackLyrics).where(TrackLyrics.path == track.path))


def _out(row: TrackLyrics) -> LyricsOut:
    return LyricsOut(text=row.text or "", synced=row.synced, source=row.source, updated_at=row.updated_at)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite gubi strefę czasową


async def _fetch_and_store(db: Session, track: Track, row: TrackLyrics | None) -> TrackLyrics:
    """Szuka tekstu w internecie i zapisuje wynik (także „nie znaleziono”)."""
    found = await service.fetch(track.artist_name, track.title, track.album.title, track.duration)
    if row is None:
        row = TrackLyrics(path=track.path)
        db.add(row)
    row.checked_at = datetime.now(timezone.utc)
    if found:
        row.text, row.synced, row.source = found.text, found.synced, "lrclib"
    else:
        row.text, row.synced, row.source = None, False, "none"
    db.commit()
    return row


@router.get("/tracks/{track_id}/lyrics", response_model=LyricsOut)
async def get_lyrics(track_id: int, _: CurrentUser, db: DB, auto: bool = True):
    """Tekst utworu. Kolejność: zapisany w chmurze → plik .lrc obok utworu → (auto) internet."""
    track = _track(db, track_id)
    row = _row(db, track)
    if row and row.text:
        return _out(row)
    if row and row.source == "removed":
        raise AppError(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if text := service.sidecar(track.path):
        return LyricsOut(text=text, synced=service.is_synced(text), source="file")
    recently = row is not None and row.checked_at is not None and \
        datetime.now(timezone.utc) - _aware(row.checked_at) < timedelta(days=RETRY_DAYS)
    if auto and get_settings().lyrics and not recently:
        try:
            row = await _fetch_and_store(db, track, row)
        except httpx.HTTPError as e:
            log.info("LRCLIB: %s", e)  # brak sieci – spróbujemy przy następnym otwarciu
        else:
            if row.text:
                return _out(row)
    raise AppError(status.HTTP_404_NOT_FOUND, NOT_FOUND)


@router.put("/tracks/{track_id}/lyrics", response_model=LyricsOut)
def save_lyrics(track_id: int, body: LyricsIn, _: CuratorUser, db: DB):
    """Zapisuje tekst (wpisany albo wczytany z pliku .lrc / .txt)."""
    track = _track(db, track_id)
    text = service.clean(body.text)
    if not text:
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "lyrics.empty")
    row = _row(db, track)
    if row is None:
        row = TrackLyrics(path=track.path)
        db.add(row)
    row.text, row.synced, row.source = text, service.is_synced(text), "user"
    db.commit()
    return _out(row)


@router.delete("/tracks/{track_id}/lyrics", status_code=status.HTTP_204_NO_CONTENT)
def delete_lyrics(track_id: int, _: CuratorUser, db: DB):
    """Usuwa tekst. Nie jest potem pobierany samoczynnie (można go pobrać ręcznie)."""
    track = _track(db, track_id)
    row = _row(db, track)
    if row is None:
        row = TrackLyrics(path=track.path)
        db.add(row)
    row.text, row.synced, row.source = None, False, "removed"
    db.commit()


@router.post("/tracks/{track_id}/lyrics/fetch", response_model=LyricsOut)
async def fetch_lyrics(track_id: int, _: CuratorUser, db: DB):
    """Pobiera tekst z internetu na żądanie (zastępuje dotychczasowy, jeśli coś znajdzie)."""
    track = _track(db, track_id)
    row = _row(db, track)
    previous = (row.text, row.synced, row.source) if row else None
    try:
        row = await _fetch_and_store(db, track, row)
    except httpx.HTTPError as e:
        raise AppError(status.HTTP_502_BAD_GATEWAY, "lyrics.service_unavailable", error=str(e)) from e
    if not row.text:
        if previous and previous[0]:  # nic nie znaleziono – zostaje to, co było
            row.text, row.synced, row.source = previous
            db.commit()
        raise AppError(status.HTTP_404_NOT_FOUND, "lyrics.not_online")
    return _out(row)
