"""Edycja biblioteki: tagi utworów, albumy, wykonawcy, okładki i usuwanie.

Zmiany trafiają do plików (gdy są zapisywalne i włączone jest NATI_WRITE_TAGS),
a jeśli się nie da (np. folder z muzyką podpięty tylko do odczytu) – do tabeli
TrackEdit, którą skaner nakłada na tagi z pliku. Id albumów i wykonawców zostają
bez zmian przy zmianie nazwy (chyba że łączymy z już istniejącym), dzięki czemu
albumy pobrane offline na urządzeniach nie znikają po edycji."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..errors import CodedError
from ..db.models import (EDIT_FIELDS, Album, Artist, DownloadJob, Genre, Playlist, Track, TrackEdit, TrackLyrics, User,
                         track_genres)
from ..text import normalize
from .scanner import LibraryScanner, apply_edit, read_meta, remove_empty
from .tagging import embed_cover, image_ext, update_tags

log = logging.getLogger(__name__)

MAX_IMAGE = 15 * 1024 * 1024
# Większych okładek nie wpisujemy do każdego pliku albumu – zostają tylko w Nati.
MAX_EMBED = 3 * 1024 * 1024


class EditError(CodedError):
    """Błąd edycji z kodem komunikatu (messages.py); status – kod HTTP."""

    def __init__(self, code: str, status: int = 400, **params):
        super().__init__(code, status, **params)


class LibraryEditor:
    def __init__(self, session_factory, scanner: LibraryScanner, covers_dir: Path, trash_dir: Path,
                 write_tags: bool = True):
        self._session_factory = session_factory
        self.scanner = scanner
        self.covers_dir = covers_dir
        self.trash_dir = trash_dir
        self.write_tags = write_tags

    # ---------------------------------------------------------------- tagi

    def _apply(self, db: Session, changes: list[tuple[Track, dict]]) -> list[int]:
        """Zapisuje zmiany tagów (plik albo TrackEdit) i odczytuje utwory na nowo."""
        paths = []
        for track, change in changes:
            path = Path(track.path)
            if not path.is_file():
                raise EditError("track.file_missing_named", 410, title=track.title)
            change = {k: v for k, v in change.items() if k in EDIT_FIELDS}
            edit = db.scalar(select(TrackEdit).where(TrackEdit.path == track.path))
            from_file = read_meta(path)
            if from_file is None:
                raise EditError("file.unknown_format", name=path.name)
            current = apply_edit(read_meta(path), edit)
            wanted = {f: getattr(current, f) for f in EDIT_FIELDS} | change

            if self._try_write(path, wanted):
                # Tagi są w pliku – w bazie zostaje najwyżej okładka.
                if edit is not None:
                    for f in EDIT_FIELDS:
                        setattr(edit, f, None)
            else:
                if edit is None:
                    edit = TrackEdit(path=track.path)
                    db.add(edit)
                for f in EDIT_FIELDS:
                    setattr(edit, f, wanted[f] if wanted[f] != getattr(from_file, f) else None)
            if edit is not None and edit.is_empty():
                db.delete(edit)
            paths.append(path)
        db.flush()
        return self.scanner.reindex(db, paths)

    def _try_write(self, path: Path, values: dict) -> bool:
        if not self.write_tags or not os.access(path, os.W_OK):
            return False
        try:
            update_tags(path, values)
            return True
        except Exception as e:  # format bez obsługi tagów, dysk tylko do odczytu…
            log.info("Nie udało się zapisać tagów w %s: %s", path, e)
            return False

    def edit_track(self, track_id: int, change: dict) -> int:
        with self.scanner.locked(), self._session_factory() as db:
            track = _get(db, Track, track_id, "track.not_found")
            if "album" in change or "album_artist" in change:
                album = track.album
                change.setdefault("album", album.title)
                change.setdefault("album_artist", album.artist.name)
            [new_id] = self._apply(db, [(track, change)])
            db.commit()
            return new_id

    def edit_album(self, album_id: int, title: str | None, artist: str | None, year: int | None,
                   set_year: bool, genre: str | None = None) -> int:
        """Zmienia tytuł / wykonawcę / rok albumu we wszystkich jego utworach. Zwraca id albumu
        (inne tylko, gdy album połączył się z już istniejącym o tej samej nazwie)."""
        with self.scanner.locked(), self._session_factory() as db:
            album = _get(db, Album, album_id, "album.not_found")
            old_artist = album.artist.name
            new_title = (title or album.title).strip()
            new_artist = (artist or old_artist).strip()
            target = self._move_album(
                db, album, new_title, self._artist_for(db, album.artist, new_artist, len(album.artist.albums) == 1))
            if set_year:
                target.year = year
            changes = []
            for t in list(album.tracks):
                change = {"album": new_title, "album_artist": new_artist}
                if set_year:
                    change["year"] = year
                if genre is not None:
                    change["genre"] = genre
                if new_artist != old_artist and t.artist_name == old_artist:
                    change["artist"] = new_artist  # typowo: poprawiona nazwa zespołu
                changes.append((t, change))
            self._apply(db, changes)
            db.commit()
            return target.id

    def rename_artist(self, artist_id: int, name: str) -> int:
        name = name.strip()
        with self.scanner.locked(), self._session_factory() as db:
            artist = _get(db, Artist, artist_id, "artist.not_found")
            old = artist.name
            target = self._artist_for(db, artist, name, rename_ok=True)
            changes = []
            for album in list(artist.albums):
                self._move_album(db, album, album.title, target)
                for t in list(album.tracks):
                    change = {"album_artist": name}
                    if t.artist_name == old:
                        change["artist"] = name
                    changes.append((t, change))
            if target is not artist and target.image is None:
                target.image = artist.image  # zdjęcie przechodzi na połączonego wykonawcę
            self._apply(db, changes)
            db.commit()
            return target.id

    @staticmethod
    def _artist_for(db: Session, current: Artist, name: str, rename_ok: bool) -> Artist:
        """Wykonawca o nazwie [name]: istniejący, albo [current] przemianowany w miejscu
        (zachowuje id i zdjęcie), gdy wolno, albo nowy."""
        if not name:
            raise EditError("edit.artist_name_empty")
        if name == current.name:
            return current
        existing = db.scalar(select(Artist).where(Artist.name == name))
        if existing is not None:
            return existing
        if rename_ok:
            current.name, current.search = name, normalize(name)
            db.flush()
            return current
        artist = Artist(name=name, search=normalize(name))
        db.add(artist)
        db.flush()
        return artist

    @staticmethod
    def _move_album(db: Session, album: Album, title: str, artist: Artist) -> Album:
        """Przenosi album pod nowy tytuł/wykonawcę. Zachowuje id, chyba że taki album już jest."""
        if not title:
            raise EditError("edit.album_title_empty")
        if album.title == title and album.artist_id == artist.id:
            return album
        existing = db.scalar(select(Album).where(Album.title == title, Album.artist_id == artist.id))
        if existing is not None and existing.id != album.id:
            if existing.cover is None:
                existing.cover = album.cover
            if existing.banner is None:
                existing.banner = album.banner
            return existing  # utwory przejdą do niego przy ponownym odczycie
        old_artist = album.artist
        album.title = title
        album.artist_id = artist.id
        album.artist = artist
        album.search = normalize(title, artist.name)
        db.flush()
        # Stary wykonawca bez albumów – przenosimy jego zdjęcie, jeśli nowy go nie ma.
        if old_artist is not artist and artist.image is None and not old_artist.albums:
            artist.image = old_artist.image
        if old_artist is not artist and artist.banner is None and not old_artist.albums:
            artist.banner = old_artist.banner
        return album

    # ---------------------------------------------------------------- gatunki

    def replace_genre(self, genre_id: int, new_name: str | None) -> int | None:
        """Zmienia nazwę gatunku (new_name) albo usuwa go (None) – także z tagów wszystkich jego utworów.

        Nowa nazwa istniejącego już gatunku łączy oba w jeden. Usunięty wbudowany gatunek zostaje
        w bazie jako ukryty, żeby nie wrócił do katalogu przy następnym starcie. Zwraca id gatunku
        po zmianie (None po usunięciu)."""
        with self.scanner.locked(timeout=600), self._session_factory() as db:
            genre = _get(db, Genre, genre_id, "genre.not_found")
            old_key = genre.search
            target: Genre | None = None
            if new_name is not None:
                new_name = " ".join(new_name.split())
                if not new_name:
                    raise EditError("edit.genre_name_empty")
                key = normalize(new_name)
                target = db.scalar(select(Genre).where(Genre.search == key))
                if target is None or target.id == genre.id:
                    builtin_renamed = not genre.custom and key != old_key
                    old_name = genre.name
                    genre.name, genre.search = new_name, key
                    genre.in_catalog, genre.hidden = True, False
                    if builtin_renamed:
                        genre.custom = True
                    db.flush()
                    if builtin_renamed:
                        # Wbudowana nazwa nie może wrócić przy starcie – zostaje jako ukryta.
                        db.add(Genre(name=old_name, search=old_key, in_catalog=False, custom=False, hidden=True))
                        db.flush()
                    target = genre
                else:
                    target.in_catalog, target.hidden = True, False
                    for f in ("icon", "color1", "color2", "image"):
                        if getattr(target, f) is None:
                            setattr(target, f, getattr(genre, f))

            tracks = list(db.scalars(select(Track).join(track_genres, track_genres.c.track_id == Track.id)
                                     .where(track_genres.c.genre_id == genre.id)))
            changes = []
            for t in tracks:
                names: list[str] = []
                for g in t.genres:
                    name = (target.name if target is not None else None) if g.id == genre.id else g.name
                    if name is not None and normalize(name) not in {normalize(n) for n in names}:
                        names.append(name)
                changes.append((t, {"genre": "; ".join(names) or None}))
            if changes:
                self._apply(db, changes)

            if target is None or target.id != genre.id:
                if genre.custom:
                    db.delete(genre)
                else:
                    genre.in_catalog, genre.hidden, genre.image = False, True, None
                    genre.icon = genre.color1 = genre.color2 = None
            remove_empty(db)
            db.commit()
            result = target.id if target is not None else None
        self.cleanup_images()
        return result

    # ---------------------------------------------------------------- obrazki

    def save_image(self, data: bytes) -> str:
        if len(data) > MAX_IMAGE:
            raise EditError("image.too_large", 413)
        ext = image_ext(data)
        if ext is None:
            raise EditError("image.bad_type", 415)
        name = hashlib.sha1(data).hexdigest() + ext
        target = self.covers_dir / name
        if not target.exists():
            tmp = target.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
        return name

    def set_album_cover(self, album_id: int, data: bytes) -> str:
        name = self.save_image(data)
        with self.scanner.locked(), self._session_factory() as db:
            album = _get(db, Album, album_id, "album.not_found")
            album.cover = name
            paths = []
            for t in album.tracks:
                path = Path(t.path)
                edit = db.scalar(select(TrackEdit).where(TrackEdit.path == t.path))
                if self._try_embed(path, data):
                    if edit is not None:
                        edit.cover = None
                        if edit.is_empty():
                            db.delete(edit)
                else:
                    if edit is None:
                        edit = TrackEdit(path=t.path)
                        db.add(edit)
                    edit.cover = name
                if path.is_file():
                    paths.append(path)
            db.flush()
            self.scanner.reindex(db, paths)  # nowy rozmiar / czas zmiany plików
            album.cover = name
            db.commit()
        self.cleanup_images()
        return name

    def _try_embed(self, path: Path, data: bytes) -> bool:
        if not self.write_tags or len(data) > MAX_EMBED or not os.access(path, os.W_OK):
            return False
        try:
            embed_cover(path, data)
            return True
        except Exception as e:
            log.info("Nie udało się zapisać okładki w %s: %s", path, e)
            return False

    def set_artist_image(self, artist_id: int, data: bytes | None) -> str | None:
        name = self.save_image(data) if data is not None else None
        with self._session_factory() as db:
            artist = _get(db, Artist, artist_id, "artist.not_found")
            artist.image = name
            db.commit()
        self.cleanup_images()
        return name

    def cleanup_images(self) -> None:
        """Kasuje pliki okładek, do których nic się już nie odwołuje."""
        with self._session_factory() as db:
            used = set(db.scalars(select(Album.cover).where(Album.cover.is_not(None))))
            used |= set(db.scalars(select(Artist.image).where(Artist.image.is_not(None))))
            used |= set(db.scalars(select(TrackEdit.cover).where(TrackEdit.cover.is_not(None))))
            used |= set(db.scalars(select(Playlist.image).where(Playlist.image.is_not(None))))
            used |= set(db.scalars(select(User.avatar).where(User.avatar.is_not(None))))
            used |= set(db.scalars(select(User.fav_image).where(User.fav_image.is_not(None))))
            used |= set(db.scalars(select(User.fav_banner).where(User.fav_banner.is_not(None))))
            used |= set(db.scalars(select(Genre.image).where(Genre.image.is_not(None))))
            for model in (Album, Artist, Playlist):
                used |= set(db.scalars(select(model.banner).where(model.banner.is_not(None))))
        for f in self.covers_dir.iterdir():
            # Świeże pliki pomijamy – skaner mógł je właśnie zapisać, a nie zatwierdzić jeszcze w bazie.
            if f.is_file() and f.name not in used and time.time() - f.stat().st_mtime > 60:
                f.unlink(missing_ok=True)

    # ---------------------------------------------------------------- usuwanie

    def delete_tracks(self, user: User, track_ids: list[int], roots: list[Path]) -> int:
        """Przenosi pliki do kosza (data/trash) i usuwa utwory z biblioteki.

        Usuwać może administrator, a pozostali – tylko utwory, które sami pobrali z internetu."""
        with self.scanner.locked(), self._session_factory() as db:
            tracks = [_get(db, Track, i, "track.not_found") for i in dict.fromkeys(track_ids)]
            if not user.is_admin:
                own = set(db.scalars(select(DownloadJob.track_id).where(
                    DownloadJob.user_id == user.id, DownloadJob.track_id.in_([t.id for t in tracks]))))
                if any(t.id not in own for t in tracks):
                    raise EditError("delete.not_allowed", 403)
            files = [Path(t.path) for t in tracks]
            for f in files:
                if f.exists() and not os.access(f.parent, os.W_OK):
                    raise EditError("delete.read_only_music", 409)
            bin_dir = self.trash_dir / datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
            for f in files:
                if f.exists():
                    bin_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(f, _unique(bin_dir / f.name))
                    _remove_empty_dirs(f.parent, roots)
            ids = [t.id for t in tracks]
            db.execute(delete(TrackEdit).where(TrackEdit.path.in_([t.path for t in tracks])))
            db.execute(delete(TrackLyrics).where(TrackLyrics.path.in_([t.path for t in tracks])))
            db.execute(delete(DownloadJob).where(DownloadJob.track_id.in_(ids)))
            for t in tracks:
                db.delete(t)
            remove_empty(db)
            db.commit()
        self.cleanup_images()
        return len(tracks)

    def purge_trash(self, days: int) -> None:
        if not self.trash_dir.is_dir():
            return
        limit = time.time() - days * 86400
        for d in self.trash_dir.iterdir():
            if d.stat().st_mtime >= limit:
                continue
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
            else:
                d.unlink(missing_ok=True)


def _get(db: Session, model, id_: int, missing: str):
    obj = db.get(model, id_)
    if obj is None:
        raise EditError(missing, 404)
    return obj


def _unique(path: Path) -> Path:
    n, candidate = 2, path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


def _remove_empty_dirs(directory: Path, roots: list[Path]) -> None:
    """Usuwa puste foldery po skasowanym pliku, w górę aż do folderu z muzyką."""
    stops = {r.resolve() for r in roots}
    d = directory.resolve()
    while d not in stops and any(d.is_relative_to(r) for r in stops):
        try:
            d.rmdir()  # tylko gdy pusty
        except OSError:
            return
        d = d.parent
