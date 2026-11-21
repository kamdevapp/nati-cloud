"""Skaner biblioteki: przechodzi po folderach z muzyką, czyta tagi i okładki,
aktualizuje bazę. Skanowanie jest przyrostowe – pliki bez zmian (mtime, rozmiar)
są pomijane."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import mutagen
from mutagen.flac import Picture
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..errors import CodedError
from ..db.models import EDIT_FIELDS, Album, Artist, Genre, Track, TrackEdit, track_genres
from ..text import UNKNOWN_ALBUM, UNKNOWN_ARTIST, normalize

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".mp4", ".aac", ".wav", ".wma", ".aiff", ".aif"}
FOLDER_COVERS = ("cover", "folder", "front", "album", "albumart")
COVER_EXTS = (".jpg", ".jpeg", ".png", ".webp")
# Podbij, gdy skaner zaczyna czytać nowe pola z tagów – wszystkie pliki zostaną odczytane ponownie.
TAGS_VERSION = 2


class ScanBusyError(CodedError, TimeoutError):
    """Biblioteka jest zajęta (skan / edycja) – spróbuj za chwilę."""


@dataclass
class ScanStatus:
    running: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    scanned: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["errors"] = self.errors[-20:]  # ostatnie błędy, żeby nie zalać odpowiedzi
        d["error_count"] = len(self.errors)
        return d


@dataclass
class TrackMeta:
    title: str
    artist: str
    album_artist: str
    album: str
    track_no: int | None
    disc_no: int | None
    year: int | None
    duration: float
    bitrate: int | None
    format: str
    genre: str | None = None  # gatunki rozdzielone „; ”


# ---------------------------------------------------------------- odczyt tagów


def _first(tags, key: str) -> str | None:
    if not tags:
        return None
    val = tags.get(key)
    if isinstance(val, list):
        val = val[0] if val else None
    if val is None:
        return None
    val = str(val).strip()
    return val or None


def _all(tags, key: str) -> list[str]:
    try:
        val = tags.get(key) if tags else None
    except (KeyError, ValueError):
        return []
    if val is None:
        return []
    return [str(v) for v in (val if isinstance(val, list) else [val])]


def _int_prefix(value: str | None) -> int | None:
    """'3/12' -> 3, '2019-05-01' -> 2019."""
    if not value:
        return None
    digits = ""
    for ch in value.strip():
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


def split_genres(value: str | None) -> list[str]:
    """„Rock; Pop/Indie, Rock” → ["Rock", "Pop", "Indie"] (bez powtórzeń)."""
    if not value:
        return []
    out: dict[str, str] = {}
    for part in value.replace("/", ";").replace(",", ";").replace("\0", ";").split(";"):
        part = part.strip()
        if part and len(part) <= 64 and normalize(part) not in out:
            out[normalize(part)] = part
    return list(out.values())


def read_meta(path: Path) -> TrackMeta | None:
    audio = mutagen.File(path, easy=True)
    if audio is None:
        return None
    tags = audio.tags
    info = audio.info
    artist = _first(tags, "artist") or UNKNOWN_ARTIST
    bitrate = getattr(info, "bitrate", None)
    return TrackMeta(
        title=_first(tags, "title") or path.stem,
        artist=artist,
        album_artist=_first(tags, "albumartist") or artist,
        album=_first(tags, "album") or UNKNOWN_ALBUM,
        track_no=_int_prefix(_first(tags, "tracknumber")),
        disc_no=_int_prefix(_first(tags, "discnumber")),
        year=_int_prefix(_first(tags, "date") or _first(tags, "year")),
        duration=float(getattr(info, "length", 0.0) or 0.0),
        bitrate=round(bitrate / 1000) if bitrate else None,
        format=path.suffix.lower().lstrip("."),
        genre="; ".join(split_genres("; ".join(_all(tags, "genre")))) or None,
    )


def apply_edit(meta: TrackMeta, edit: TrackEdit | None) -> TrackMeta:
    """Nakłada zmiany z Nati (TrackEdit) na tagi z pliku."""
    if edit is not None:
        for f in EDIT_FIELDS:
            if (v := getattr(edit, f)) is not None:
                setattr(meta, f, v)
    return meta


def _embedded_cover(path: Path) -> tuple[bytes, str] | None:
    """Zwraca (dane, rozszerzenie) okładki osadzonej w pliku."""
    try:
        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None

    def ext_for(mime: str | None) -> str:
        return ".png" if mime and "png" in mime else ".jpg"

    # FLAC
    pictures = getattr(f, "pictures", None)
    if pictures:
        pic = next((p for p in pictures if p.type == 3), pictures[0])
        return pic.data, ext_for(pic.mime)

    tags = f.tags
    if not tags:
        return None
    # ID3 (MP3, AIFF, WAV)
    if hasattr(tags, "getall"):
        apics = tags.getall("APIC")
        if apics:
            pic = next((p for p in apics if p.type == 3), apics[0])
            return pic.data, ext_for(pic.mime)
    # MP4 / M4A
    covr = tags.get("covr") if hasattr(tags, "get") else None
    if covr:
        data = bytes(covr[0])
        return data, ".png" if data[:4] == b"\x89PNG" else ".jpg"
    # Ogg Vorbis / Opus
    blocks = tags.get("metadata_block_picture") if hasattr(tags, "get") else None
    if blocks:
        try:
            pic = Picture(base64.b64decode(blocks[0]))
            return pic.data, ext_for(pic.mime)
        except Exception:
            return None
    return None


def _folder_cover(directory: Path) -> Path | None:
    try:
        entries = {p.name.lower(): p for p in directory.iterdir() if p.is_file()}
    except OSError:
        return None
    for base in FOLDER_COVERS:
        for ext in COVER_EXTS:
            if (p := entries.get(base + ext)) is not None:
                return p
    return None


def save_cover(track_path: Path, covers_dir: Path) -> str | None:
    """Zapisuje okładkę do covers_dir pod nazwą z hasha treści, zwraca nazwę pliku."""
    found = _embedded_cover(track_path)
    if found is None:
        folder = _folder_cover(track_path.parent)
        if folder is None:
            return None
        found = (folder.read_bytes(), folder.suffix.lower().replace(".jpeg", ".jpg"))
    data, ext = found
    name = hashlib.sha1(data).hexdigest() + ext
    target = covers_dir / name
    if not target.exists():
        target.write_bytes(data)
    return name


# ---------------------------------------------------------------- skaner


class LibraryScanner:
    def __init__(self, session_factory, music_dirs: list[Path], covers_dir: Path):
        self.on_finished = None  # wywoływane po każdym skanie (np. uzupełnianie z internetu)
        self._session_factory = session_factory
        self.music_dirs = music_dirs
        self.covers_dir = covers_dir
        self.status = ScanStatus()
        self._lock = threading.Lock()
        self._genres: dict[str, Genre] = {}

    @contextmanager
    def locked(self, timeout: float = 120):
        """Wyłączny dostęp do biblioteki (skan, edycja, usuwanie nie mogą iść naraz)."""
        if not self._lock.acquire(timeout=timeout):
            raise ScanBusyError("scan.busy", 409)
        try:
            yield
        finally:
            self._lock.release()

    def start_background(self) -> bool:
        """Uruchamia skan w osobnym wątku. False, jeśli skan już trwa."""
        if self.status.running:
            return False
        threading.Thread(target=self.scan, name="nati-scan", daemon=True).start()
        return True

    def scan(self) -> ScanStatus:
        if not self._lock.acquire(blocking=False):
            return self.status
        try:
            self.status = ScanStatus(running=True, started_at=datetime.now(timezone.utc))
            with self._session_factory() as db:
                self._scan(db)
        except Exception as e:  # nie pozwól, by wątek padł po cichu
            log.exception("Skan biblioteki nie powiódł się")
            self.status.errors.append(f"Fatal error: {e}")
        finally:
            self.status.running = False
            self.status.finished_at = datetime.now(timezone.utc)
            self._lock.release()
        if self.on_finished:
            self.on_finished()
        return self.status

    def _scan(self, db: Session) -> None:
        st = self.status
        known = {t.path: t for t in db.scalars(select(Track))}
        seen: set[str] = set()
        artists = {a.name: a for a in db.scalars(select(Artist))}
        albums = {(a.title, a.artist_id): a for a in db.scalars(select(Album))}
        edits = {e.path: e for e in db.scalars(select(TrackEdit))}
        self._genres = {g.search: g for g in db.scalars(select(Genre))}
        available_roots: list[str] = []

        for root in self.music_dirs:
            if not root.is_dir():
                # Folder niedostępny (np. odmontowany dysk) – nie kasujemy jego utworów.
                st.errors.append(f"Folder not available: {root}")
                continue
            available_roots.append(str(root.resolve()))
            for dirpath, _dirnames, filenames in os.walk(root, followlinks=True):
                for fname in filenames:
                    p = Path(dirpath, fname)
                    if p.suffix.lower() not in AUDIO_EXTENSIONS:
                        continue
                    key = str(p.resolve())
                    seen.add(key)
                    st.scanned += 1
                    try:
                        self._process(db, p, key, known.get(key), artists, albums, edit=edits.get(key))
                    except Exception as e:
                        st.errors.append(f"{p}: {e}")
                    if st.scanned % 200 == 0:
                        db.commit()

        # Usuń utwory, których plików już nie ma. Utwory z niedostępnego folderu zostają
        # (dysk mógł być tylko odmontowany), a z folderu usuniętego z konfiguracji – znikają.
        configured_roots = [str(r.resolve()) for r in self.music_dirs]

        def under(path: str, roots: list[str]) -> bool:
            return any(path.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

        for path, track in known.items():
            if path in seen:
                continue
            if under(path, available_roots) or not under(path, configured_roots):
                db.delete(track)
                st.removed += 1
        db.flush()

        remove_empty(db)
        db.commit()

    def index_file(self, path: Path) -> int:
        """Dodaje (albo odświeża) jeden plik w bibliotece – np. świeżo pobrany. Zwraca id utworu."""
        with self.locked(timeout=3600), self._session_factory() as db:
            [track_id] = self.reindex(db, [path])
            db.commit()
            return track_id

    def reindex(self, db: Session, paths: list[Path]) -> list[int]:
        """Odczytuje pliki na nowo (np. po edycji tagów). Wymaga locked(). Nie zatwierdza sesji."""
        artists = {a.name: a for a in db.scalars(select(Artist))}
        albums = {(a.title, a.artist_id): a for a in db.scalars(select(Album))}
        self._genres = {g.search: g for g in db.scalars(select(Genre))}
        ids = []
        for path in paths:
            key = str(path.resolve())
            existing = db.scalar(select(Track).where(Track.path == key))
            edit = db.scalar(select(TrackEdit).where(TrackEdit.path == key))
            self._process(db, path, key, existing, artists, albums, force=True, edit=edit)
            db.flush()
            ids.append(db.scalar(select(Track.id).where(Track.path == key)))
        remove_empty(db)
        return ids

    def _genre(self, db: Session, name: str) -> Genre:
        key = normalize(name)
        g = self._genres.get(key)
        if g is None:
            g = Genre(name=name, search=key)
            db.add(g)
            db.flush()
            self._genres[key] = g
        return g

    def _process(self, db, p: Path, key: str, existing: Track | None, artists, albums, force: bool = False,
                 edit: TrackEdit | None = None) -> None:
        stat = p.stat()
        if (not force and existing and existing.mtime == stat.st_mtime and existing.size == stat.st_size
                and existing.tags_version == TAGS_VERSION):
            return
        meta = read_meta(p)
        if meta is None:
            raise ValueError("nierozpoznany format audio")
        apply_edit(meta, edit)

        artist = artists.get(meta.album_artist)
        if artist is None:
            artist = Artist(name=meta.album_artist, search=normalize(meta.album_artist))
            db.add(artist)
            db.flush()
            artists[artist.name] = artist

        album = albums.get((meta.album, artist.id))
        if album is None:
            album = Album(title=meta.album, artist_id=artist.id, year=meta.year,
                          search=normalize(meta.album, artist.name))
            db.add(album)
            db.flush()
            albums[(album.title, artist.id)] = album
        if album.year is None and meta.year:
            album.year = meta.year
        if edit is not None and edit.cover:
            album.cover = edit.cover
        elif album.cover is None:
            album.cover = save_cover(p, self.covers_dir)

        track = existing or Track(path=key)
        track.title = meta.title
        track.artist_name = meta.artist
        track.album_id = album.id
        track.track_no = meta.track_no
        track.disc_no = meta.disc_no
        track.duration = meta.duration
        track.bitrate = meta.bitrate
        track.format = meta.format
        track.size = stat.st_size
        track.mtime = stat.st_mtime
        track.search = normalize(meta.title, meta.artist, meta.album, meta.album_artist)
        track.tags_version = TAGS_VERSION
        track.genres = [self._genre(db, g) for g in split_genres(meta.genre)]
        if existing is None:
            db.add(track)
            self.status.added += 1
        else:
            self.status.updated += 1


def remove_empty(db: Session) -> None:
    """Usuwa albumy bez utworów, wykonawców bez albumów i nieużywane gatunki."""
    db.flush()
    db.execute(delete(Album).where(~Album.tracks.any()))
    db.execute(delete(Artist).where(~Artist.albums.any()))
    db.execute(delete(Genre).where(Genre.id.not_in(select(track_genres.c.genre_id)),
                                   Genre.in_catalog.is_not(True), Genre.hidden.is_not(True)))
