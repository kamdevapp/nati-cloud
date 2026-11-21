"""Tagi i okładka w pobranym pliku (Opus/Ogg, MP3, M4A, FLAC)."""

from __future__ import annotations

import base64
from pathlib import Path

import mutagen
from mutagen.flac import Picture
from mutagen.id3 import APIC
from mutagen.mp4 import MP4Cover
from ..errors import CodedError


def write_tags(path: Path, *, title: str, artist: str | None, album: str | None, album_artist: str | None,
               track_no: int | None, year: int | None, cover: bytes | None, genre: str | None = None) -> None:
    f = mutagen.File(path, easy=True)
    if f is None:
        raise CodedError("file.unknown_format", name=path.name)
    if f.tags is None:
        f.add_tags()
    values = {
        "title": title,
        "artist": artist,
        "album": album,
        "albumartist": album_artist or artist,
        "tracknumber": str(track_no) if track_no else None,
        "date": str(year) if year else None,
        "genre": [g.strip() for g in genre.split(";") if g.strip()] if genre else None,
    }
    for key, value in values.items():
        if value:
            f[key] = value
    f.save()
    if cover:
        _embed_cover(path, cover)


_EASY_KEYS = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album_artist": "albumartist",
    "track_no": "tracknumber",
    "disc_no": "discnumber",
    "year": "date",
    "genre": "genre",
}


def update_tags(path: Path, values: dict) -> None:
    """Zapisuje w pliku podane pola (klucze jak w TrackEdit); None usuwa tag.
    Rzuca wyjątek, gdy pliku nie da się zapisać (format, uprawnienia)."""
    f = mutagen.File(path, easy=True)
    if f is None:
        raise CodedError("file.unknown_format", name=path.name)
    if f.tags is None:
        f.add_tags()
    for field, value in values.items():
        key = _EASY_KEYS[field]
        if value is None or value == "":
            if key in f.tags:
                del f.tags[key]
        elif field == "genre":
            f.tags[key] = [g.strip() for g in str(value).split(";") if g.strip()]
        else:
            f.tags[key] = str(value)
    f.save()


def embed_cover(path: Path, data: bytes) -> None:
    _embed_cover(path, data)


def image_ext(data: bytes) -> str | None:
    """Rozszerzenie dla obrazka JPEG/PNG/WebP albo None, gdy to nie obrazek."""
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


def _mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _embed_cover(path: Path, data: bytes) -> None:
    f = mutagen.File(path)
    mime = _mime(data)
    ext = path.suffix.lower()
    if ext in (".opus", ".ogg", ".oga"):
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, data
        f["metadata_block_picture"] = [base64.b64encode(pic.write()).decode()]
    elif ext == ".flac":
        pic = Picture()
        pic.type, pic.mime, pic.data = 3, mime, data
        f.clear_pictures()
        f.add_picture(pic)
    elif ext == ".mp3":
        if f.tags is None:
            f.add_tags()
        f.tags.delall("APIC")  # bez tego stara okładka zostaje obok nowej
        f.tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
    elif ext in (".m4a", ".mp4"):
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        f["covr"] = [MP4Cover(data, imageformat=fmt)]
    else:
        return  # inne formaty – bez okładki w pliku
    f.save()


_FORBIDDEN = '<>:"/\\|?*'


def safe_name(s: str, limit: int = 120) -> str:
    """Nazwa pliku/folderu działająca na Linuksie, Windowsie i macOS."""
    cleaned = "".join("_" if c in _FORBIDDEN or ord(c) < 32 else c for c in s).strip(" .")
    return (cleaned or "_")[:limit]
