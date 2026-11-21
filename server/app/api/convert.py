"""Zamiana modeli bazy na schematy API."""

from ..db.models import Album, Track
from ..schemas import AlbumOut, TrackOut


def track_out(t: Track) -> TrackOut:
    return TrackOut(
        id=t.id,
        title=t.title,
        artist_name=t.artist_name,
        album_id=t.album_id,
        album_title=t.album.title,
        album_artist=t.album.artist.name,
        track_no=t.track_no,
        disc_no=t.disc_no,
        duration=t.duration,
        bitrate=t.bitrate,
        format=t.format,
        size=t.size,
        year=t.album.year,
        cover_id=cover_id(t.album.cover),
        genres=[g.name for g in t.genres],
        artist_id=t.album.artist_id,
        artist_image_id=cover_id(t.album.artist.image),
    )


def cover_id(cover: str | None) -> str | None:
    """Krótka wersja obrazka (z hasha w nazwie pliku) – klient po niej wie, że trzeba pobrać nowy."""
    return cover.split(".")[0][:12] if cover else None


def album_out(a: Album, track_count: int = 0, genres: list[str] | None = None) -> AlbumOut:
    return AlbumOut(
        id=a.id,
        title=a.title,
        artist_id=a.artist_id,
        artist_name=a.artist.name,
        year=a.year,
        has_cover=a.cover is not None,
        cover_id=cover_id(a.cover),
        track_count=track_count,
        genres=genres or [],
        banner_id=cover_id(a.banner),
    )
