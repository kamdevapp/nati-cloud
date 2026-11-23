"""Uzupełnianie biblioteki z internetu (w tle): gatunki i zdjęcia wykonawców.

- Gatunki: najpierw gatunek albumu z iTunes (dokładny, dla albumu), potem gatunki wykonawcy
  z MusicBrainz – tylko dla utworów, które gatunku nie mają. Zapisywane tylko w bazie Nati
  (TrackEdit), pliki zostają nietknięte.
- Zdjęcie: miniatura wykonawcy z YouTube Music (tylko przy zgodnej nazwie).

Każdy wykonawca jest sprawdzany raz (Artist.enriched_at); „Uzupełnij ponownie” zeruje znaczniki."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
from collections import Counter

from sqlalchemy import select, update

from ..db.models import Album, Artist, Track, TrackEdit
from ..text import normalize
from . import online_search
from .musicbrainz import USER_AGENT
from .scanner import LibraryScanner

log = logging.getLogger(__name__)

MB = "https://musicbrainz.org/ws/2"


@dataclass
class EnrichStatus:
    running: bool = False
    done: int = 0
    total: int = 0
    genres_added: int = 0
    images_added: int = 0
    last_error: str | None = None


class Enricher:
    def __init__(self, session_factory, scanner: LibraryScanner, editor, enabled: bool = True):
        self._session_factory = session_factory
        self.scanner = scanner
        self.editor = editor
        self.enabled = enabled
        self.status = EnrichStatus()
        self._wake = threading.Event()
        self._last_mb = 0.0
        self._thread: threading.Thread | None = None

    # Podbij, gdy dochodzi nowe źródło danych – wszyscy wykonawcy zostaną sprawdzeni jeszcze raz.
    VERSION = 3  # 3: gatunek albumu dla utworów pobranych po pierwszym

    def start(self, data_dir: Path | None = None) -> None:
        if not self.enabled or self._thread is not None:
            return
        if data_dir is not None:
            marker = data_dir / ".enrich-version"
            if marker.read_text(encoding="utf-8").strip() != str(self.VERSION) if marker.exists() else True:
                with self._session_factory() as db:
                    db.execute(update(Artist).values(enriched_at=None))
                    db.commit()
                marker.write_text(str(self.VERSION), encoding="utf-8")
        self._thread = threading.Thread(target=self._loop, name="nati-enrich", daemon=True)
        self._thread.start()
        self.request()

    def request(self, again: bool = False) -> None:
        """Uruchom (np. po skanie). [again] – sprawdź też już sprawdzonych wykonawców."""
        if again:
            with self._session_factory() as db:
                db.execute(update(Artist).values(enriched_at=None))
                db.commit()
        self._wake.set()

    def _loop(self) -> None:
        while True:
            self._wake.wait()
            self._wake.clear()
            # Najpierw niech skończy się skan – inaczej wykonawców jeszcze nie ma.
            while self.scanner.status.running:
                time.sleep(2)
            try:
                self._run()
            except Exception as e:  # brak internetu itp. – spróbujemy przy następnym wywołaniu
                log.warning("Uzupełnianie z internetu: %s", e)
                self.status.last_error = str(e)[:300]
            finally:
                self.status.running = False

    def _run(self) -> None:
        with self._session_factory() as db:
            todo = [(a.id, a.name, a.image is None) for a in db.scalars(
                select(Artist).where(Artist.enriched_at.is_(None)).order_by(Artist.id))]
        if not todo:
            return
        self.status = EnrichStatus(running=True, total=len(todo))
        with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
            for artist_id, name, needs_image in todo:
                try:
                    self._artist(client, artist_id, name, needs_image)
                except httpx.HTTPError as e:
                    self.status.last_error = str(e)[:300]
                    if isinstance(e, httpx.ConnectError):
                        return  # bez internetu – zostaw na później
                with self._session_factory() as db:
                    db.execute(update(Artist).where(Artist.id == artist_id)
                               .values(enriched_at=datetime.now(timezone.utc)))
                    db.commit()
                self.status.done += 1

    # ---------------------------------------------------------------- jeden wykonawca

    def _artist(self, client: httpx.Client, artist_id: int, name: str, needs_image: bool) -> None:
        if name.lower().startswith("nieznany"):
            return
        # 1. Gatunek każdego albumu z iTunes.
        with self._session_factory() as db:
            albums = [(a.id, a.title) for a in db.scalars(select(Album).where(Album.artist_id == artist_id))]
        for album_id, title in albums:
            if title.lower().startswith("nieznany"):
                continue
            with self._session_factory() as db:
                ids = list(db.scalars(select(Track.id).where(Track.album_id == album_id, ~Track.genres.any())))
                # Część albumu ma już gatunek (np. pobierany utwór po utworze) – ten sam dla reszty, bez sieci.
                genre = album_genre(db, album_id)
            if not ids:
                continue
            genre = genre or self._itunes_genre(client, name, title)
            if genre:
                self._set_genres(ids, genre)
                self.status.genres_added += len(ids)
        # 2. Reszta (albumy nieznane w iTunes) – gatunki wykonawcy z MusicBrainz.
        with self._session_factory() as db:
            missing = list(db.scalars(
                select(Track.id).join(Album, Album.id == Track.album_id)
                .where(Album.artist_id == artist_id, ~Track.genres.any())))
        if missing:
            genres = self._mb_genres(client, name)
            if genres:
                self._set_genres(missing, "; ".join(genres))
                self.status.genres_added += len(missing)
        if needs_image:
            url = self._image_url(name)
            if url:
                self.editor.set_artist_image(artist_id, self._download(client, url))
                self.status.images_added += 1

    @staticmethod
    def _download(client: httpx.Client, url: str) -> bytes:
        r = client.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.content

    def _mb_get(self, client: httpx.Client, path: str, params: dict) -> dict:
        wait = 1.1 - (time.monotonic() - self._last_mb)
        if wait > 0:
            time.sleep(wait)
        self._last_mb = time.monotonic()
        r = client.get(f"{MB}/{path}", params={**params, "fmt": "json"})
        if r.status_code == 503:  # „za szybko”
            time.sleep(3)
            r = client.get(f"{MB}/{path}", params={**params, "fmt": "json"})
        r.raise_for_status()
        return r.json()

    def _mb_genres(self, client: httpx.Client, name: str) -> list[str]:
        found = self._mb_get(client, "artist", {"query": f'artist:"{name}"', "limit": 3}).get("artists") or []
        best = next((a for a in found if (a.get("score") or 0) >= 95 and normalize(a.get("name", "")) == normalize(name)),
                    None)
        if best is None:
            return []
        data = self._mb_get(client, f"artist/{best['id']}", {"inc": "genres"})
        genres = sorted(data.get("genres") or [], key=lambda g: -(g.get("count") or 0))
        return [_title(g["name"]) for g in genres[:3] if g.get("name")]

    @staticmethod
    def _itunes_genre(client: httpx.Client, artist: str, album: str) -> str | None:
        """Gatunek albumu z iTunes (primaryGenreName) – tylko przy zgodnym wykonawcy i tytule."""
        r = client.get("https://itunes.apple.com/search",
                       params={"term": f"{artist} {album}", "entity": "album", "limit": 10})
        if r.status_code != 200:
            return None
        want_artist, want_album = normalize(artist), normalize(album)
        for res in r.json().get("results", []):
            name = normalize(res.get("collectionName", "").split(" - ")[0])
            if normalize(res.get("artistName", "")) != want_artist:
                continue
            if name == want_album or want_album in name or name in want_album:
                return _itunes_title(res.get("primaryGenreName"))
        return None

    @staticmethod
    def _image_url(name: str) -> str | None:
        for c in online_search.artist_image_candidates(name, limit=3):
            if normalize(c.get("title", "")) == normalize(name):
                return c["url"]
        return None

    def _set_genres(self, track_ids: list[int], genre: str) -> None:
        """Gatunek tylko w bazie Nati (pliki bez zmian) i ponowny odczyt utworów."""
        with self.scanner.locked(timeout=600), self._session_factory() as db:
            paths = []
            for t in db.scalars(select(Track).where(Track.id.in_(track_ids))):
                edit = db.scalar(select(TrackEdit).where(TrackEdit.path == t.path))
                if edit is None:
                    edit = TrackEdit(path=t.path)
                    db.add(edit)
                edit.genre = genre
                if Path(t.path).is_file():
                    paths.append(Path(t.path))
            db.flush()
            self.scanner.reindex(db, paths)
            db.commit()

    def as_dict(self) -> dict:
        return asdict(self.status)


def album_genre(db, album_id: int) -> str | None:
    """Najczęstszy zestaw gatunków utworów albumu („Pop; Rock”); None – żaden utwór go nie ma."""
    counts: Counter[str] = Counter()
    for t in db.scalars(select(Track).where(Track.album_id == album_id, Track.genres.any())):
        counts["; ".join(g.name for g in t.genres)] += 1
    return counts.most_common(1)[0][0] if counts else None


_ITUNES = {"Hip-Hop/Rap": "Hip-Hop", "R&B/Soul": "R&B", "Singer/Songwriter": "Singer-Songwriter",
           "Dance": "Dance", "Electronic": "Electronic", "Alternative": "Alternative"}


def _itunes_title(g: str | None) -> str | None:
    if not g or g.lower() in ("music", "muzyka"):
        return None
    return _ITUNES.get(g, g)


def _title(g: str) -> str:
    special = {"edm": "EDM", "r&b": "R&B", "hip hop": "Hip-Hop", "k-pop": "K-Pop"}
    return special.get(g.lower(), g[:1].upper() + g[1:])
