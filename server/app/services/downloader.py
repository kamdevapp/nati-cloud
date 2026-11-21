"""Kolejka pobierania z internetu do chmury."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import func, select, update

from ..db.models import Album, Artist, DownloadJob, Track
from . import musicbrainz
from .scanner import LibraryScanner
from .tagging import safe_name, write_tags
from .ytdlp import YtDlp, YtDlpError
from ..text import UNKNOWN_ARTIST

log = logging.getLogger(__name__)

ACTIVE = ("queued", "downloading", "processing")

# Grzeczne pobieranie (żeby YouTube nie uznał serwera za bota): przerwa między kolejnymi pobraniami,
# a przy dużej kolejce dłuższa i tylko jedno pobieranie naraz.
PAUSE = (2.0, 5.0)
BIG_QUEUE = 10
PAUSE_BIG = (6.0, 14.0)
# Po blokadzie kolejka czeka coraz dłużej: 30 min, 1 h, 2 h… do 6 h.
BLOCK_WAIT = 30 * 60
BLOCK_WAIT_MAX = 6 * 3600
COOKIES_FOR = 3600  # tyle po blokadzie używamy ciasteczek (jeśli są)


def job_url(job: DownloadJob) -> str:
    return f"https://music.youtube.com/watch?v={job.source_id}" if job.source == "ytmusic" else job.source_id


def _error(e: Exception) -> dict:
    """Błąd zadania do bazy: tekst angielski i – dla błędów z kodem – kod i parametry (aplikacja tłumaczy)."""
    return {"error": str(e)[:500], "error_code": getattr(e, "code", None),
            "error_params": json.dumps(getattr(e, "params", {}) or {}, ensure_ascii=False)[:2000]}


class DownloadManager:
    def __init__(self, session_factory, ytdlp: YtDlp, scanner: LibraryScanner, online_dir: Path, tmp_dir: Path,
                 workers: int = 2, auto_update: bool = True, use_musicbrainz: bool = True):
        self._session_factory = session_factory
        self.ytdlp = ytdlp
        self.scanner = scanner
        self.online_dir = online_dir
        self.tmp_dir = tmp_dir
        self.workers = workers
        self.auto_update = auto_update
        self.use_musicbrainz = use_musicbrainz
        self.on_done = None  # po dodaniu utworu do biblioteki (np. uzupełnianie z internetu)
        self._wake = asyncio.Event()
        self._claim_lock = asyncio.Lock()
        self._running: dict[int, asyncio.Task] = {}
        self._tasks: list[asyncio.Task] = []
        self.ytdlp_version: str | None = None
        self._stopping = False
        self.last_update_error: str | None = None
        self.pace = True  # przerwy między pobraniami (w testach wyłączane)
        self._gate = asyncio.Lock()
        self._next_at = 0.0
        self.blocked_until = 0.0  # kolejka wstrzymana przez blokadę YouTube
        self._block_wait = BLOCK_WAIT

    # ---------------------------------------------------------------- cykl życia

    def start(self) -> None:
        # Po restarcie serwera przerwane zadania wracają do kolejki.
        with self._session_factory() as db:
            db.execute(update(DownloadJob).where(DownloadJob.status.in_(("downloading", "processing")))
                       .values(status="queued", progress=0.0))
            db.commit()
        self._tasks = [asyncio.create_task(self._worker(i)) for i in range(self.workers)]
        self._tasks.append(asyncio.create_task(self._maintenance()))
        self._wake.set()

    async def stop(self) -> None:
        self._stopping = True  # przerwane zadania zostaną w kolejce na następny start
        for t in [*self._tasks, *self._running.values()]:
            t.cancel()
        await asyncio.gather(*self._tasks, *self._running.values(), return_exceptions=True)

    def notify(self) -> None:
        """Nowe zadanie w kolejce."""
        self._wake.set()

    @property
    def blocked(self) -> bool:
        return time.time() < self.blocked_until

    def resume(self) -> None:
        """Wznów kolejkę od razu (np. po dodaniu ciasteczek)."""
        self.blocked_until = 0.0
        self._wake.set()

    def _on_blocked(self) -> None:
        self.blocked_until = time.time() + self._block_wait
        log.warning("YouTube blokuje pobieranie – kolejka wstrzymana na %d min", self._block_wait // 60)
        self._block_wait = min(self._block_wait * 2, BLOCK_WAIT_MAX)

    def _queued(self) -> int:
        with self._session_factory() as db:
            return db.scalar(select(func.count(DownloadJob.id)).where(DownloadJob.status == "queued")) or 0

    async def _wait_turn(self, n: int) -> None:
        """Czeka na swoją kolej: koniec blokady, przerwa od poprzedniego pobrania, przy dużej kolejce – jeden naraz."""
        while self.blocked:
            await asyncio.sleep(min(self.blocked_until - time.time(), 30) + 0.1)
        if not self.pace:
            return
        big = self._queued() >= BIG_QUEUE
        while big and n > 0 and self._running:
            await asyncio.sleep(2)
            big = self._queued() >= BIG_QUEUE
        async with self._gate:
            wait = self._next_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_at = time.monotonic() + random.uniform(*(PAUSE_BIG if big else PAUSE))

    async def cancel(self, job_id: int) -> None:
        task = self._running.get(job_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ---------------------------------------------------------------- yt-dlp

    async def _maintenance(self) -> None:
        """Wersja yt-dlp i automatyczna aktualizacja raz na dobę."""
        while True:
            try:
                if self.auto_update and self.ytdlp.update_due():
                    self.ytdlp_version = await self.ytdlp.update()
                    self.last_update_error = None
                elif self.ytdlp_version is None:
                    self.ytdlp_version = await self.ytdlp.version()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # brak internetu itp. – spróbujemy za godzinę
                self.last_update_error = str(e)[:300]
                log.warning("yt-dlp: %s", e)
                if self.ytdlp_version is None:
                    try:
                        self.ytdlp_version = await self.ytdlp.version()
                    except Exception:
                        pass
            await asyncio.sleep(3600)

    # ---------------------------------------------------------------- praca

    async def _claim(self) -> int | None:
        async with self._claim_lock:
            with self._session_factory() as db:
                job = db.scalar(select(DownloadJob).where(DownloadJob.status == "queued")
                                .order_by(DownloadJob.id).limit(1))
                if job is None:
                    return None
                job.status = "downloading"
                db.commit()
                return job.id

    async def _worker(self, n: int) -> None:
        while True:
            await self._wait_turn(n)
            job_id = await self._claim()
            if job_id is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            task = asyncio.create_task(self._process(job_id))
            self._running[job_id] = task
            try:
                await task
            except asyncio.CancelledError:
                if self._stopping or not task.cancelled():
                    raise  # zatrzymanie serwera
                self._set(job_id, status="cancelled", finished_at=_now())
            except Exception as e:
                log.exception("Zadanie %s nie powiodło się", job_id)
                self._set(job_id, status="error", finished_at=_now(), **_error(e))
            finally:
                self._running.pop(job_id, None)
                shutil.rmtree(self.tmp_dir / f"job-{job_id}", ignore_errors=True)

    def _set(self, job_id: int, **values) -> None:
        if values.get("error", "") is None:  # błąd minął – bez kodu i parametrów
            values.update(error_code=None, error_params=None)
        with self._session_factory() as db:
            db.execute(update(DownloadJob).where(DownloadJob.id == job_id).values(**values))
            db.commit()

    async def _process(self, job_id: int) -> None:
        with self._session_factory() as db:
            job = db.get(DownloadJob, job_id)
            db.expunge(job)
        last = -1.0

        def progress(p: float):
            nonlocal last
            if p - last >= 0.05 or p >= 1.0:  # zapis co 5%
                last = p
                self._set(job_id, progress=round(p, 3))

        workdir = self.tmp_dir / f"job-{job_id}"
        try:
            try:
                result = await self.ytdlp.download(job_url(job), workdir, progress)
            except YtDlpError as e:
                # Blokada albo utwór tylko dla zalogowanych – druga próba z ciasteczkami, jeśli są.
                if not ((e.blocked or e.needs_login) and self.ytdlp.has_cookies and not self.ytdlp.cookies_active()):
                    raise
                if e.blocked:
                    self.ytdlp.cookies_until = time.time() + COOKIES_FOR
                shutil.rmtree(workdir, ignore_errors=True)
                result = await self.ytdlp.download(job_url(job), workdir, progress, cookies=True)
        except YtDlpError as e:
            if e.blocked:
                # Zadanie wraca do kolejki; spróbujemy po przerwie.
                self._on_blocked()
                self._set(job_id, status="queued", progress=0.0, **_error(e))
                return
            self._set(job_id, status="error", finished_at=_now(), **_error(e))
            return
        self._block_wait = BLOCK_WAIT  # udane pobranie – blokada minęła
        self._set(job_id, status="processing", progress=1.0)

        info = result.info
        # Dla linku tytuł zadania to tymczasowo sam adres – prawdziwe dane bierzemy z yt-dlp.
        given_title = job.title if job.source != "url" else None
        title = given_title or info.get("track") or info.get("title") or "Untitled"
        artist = job.artist or info.get("artist") or info.get("uploader") or info.get("channel")
        album = job.album or info.get("album")
        album_artist = job.album_artist or artist
        year = job.year or _int(info.get("release_year")) or _int((info.get("upload_date") or "")[:4])
        track_no = job.track_no
        thumbnail = job.thumbnail or info.get("thumbnail")
        cover = None
        genre = None

        # Link spoza YouTube Music bez danych o utworze (zwykły film, SoundCloud…) –
        # próbujemy rozpoznać utwór w MusicBrainz.
        if job.source == "url" and self.use_musicbrainz and not (info.get("track") and info.get("artist")):
            m = await musicbrainz.lookup(info.get("title") or "", info.get("uploader") or info.get("channel"))
            if m is not None:
                title, artist, album = m.title, m.artist, m.album or album
                album_artist = m.album_artist or m.artist
                year, track_no = m.year or year, m.track_no
                genre = "; ".join(m.genres) or None
                if m.cover_url:
                    cover = await _fetch(m.cover_url)
            elif "Music" in (info.get("categories") or []) and (
                    pair := musicbrainz.split_artist_title(info.get("title") or "")):
                artist, title = pair  # przynajmniej „Wykonawca - Tytuł” z tytułu filmu
                album_artist = job.album_artist or artist
            elif info.get("title"):
                title = musicbrainz.clean_title(info["title"])  # bez „(Official Video)”
        elif self.use_musicbrainz and job.source == "ytmusic" and artist:
            # YouTube Music podaje dobre tagi, ale bez gatunku – ten bierzemy z MusicBrainz.
            m = await musicbrainz.lookup_pairs([(artist.split(",")[0].strip(), title)])
            genre = "; ".join(m.genres) if m and m.genres else None
        if cover is None and thumbnail:
            cover = await _fetch(thumbnail)
        # Cały album z tym samym gatunkiem: jeśli inne utwory albumu są już w chmurze z gatunkiem – ten sam.
        if album and (same := await asyncio.to_thread(self._album_genre, album, album_artist or artist)):
            genre = same

        def finish() -> int:
            write_tags(result.path, title=title, artist=artist, album=album, album_artist=album_artist,
                       track_no=track_no, year=year, cover=cover, genre=genre)
            folder = self.online_dir / safe_name(album_artist or UNKNOWN_ARTIST) / safe_name(album or "Single")
            folder.mkdir(parents=True, exist_ok=True)
            base = f"{track_no:02d} - {title}" if track_no else title
            dest = _unique(folder / f"{safe_name(base)}{result.path.suffix}")
            shutil.move(result.path, dest)
            return self.scanner.index_file(dest)

        track_id = await asyncio.to_thread(finish)
        await asyncio.to_thread(self._recheck_if_no_genre, track_id)
        if self.on_done:
            self.on_done()
        self._set(job_id, status="done", track_id=track_id, title=title, artist=artist, album=album,
                  error=None, finished_at=_now())
        if job.playlist_id and track_id:
            from ..api.extras import append_to_playlist

            with self._session_factory() as db:
                append_to_playlist(db, job.playlist_id, track_id)
                db.commit()


    def _album_genre(self, album: str, album_artist: str | None) -> str | None:
        from .enrich import album_genre

        with self._session_factory() as db:
            album_id = db.scalar(select(Album.id).join(Artist, Artist.id == Album.artist_id)
                                 .where(Album.title == album, Artist.name == (album_artist or "")))
            return album_genre(db, album_id) if album_id else None

    def _recheck_if_no_genre(self, track_id: int) -> None:
        """Utwór bez gatunku – jego wykonawca do ponownego uzupełnienia z internetu (iTunes / reszta albumu).
        Inaczej utwory albumu pobrane po pierwszym zostawały bez gatunku: wykonawca był już „sprawdzony”."""
        with self._session_factory() as db:
            track = db.get(Track, track_id)
            if track is None or track.genres:
                return
            db.execute(update(Artist).where(Artist.id == track.album.artist_id).values(enriched_at=None))
            db.commit()


def _unique(path: Path) -> Path:
    n = 2
    candidate = path
    while candidate.exists():
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return candidate


async def _fetch(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except httpx.HTTPError:
        return None  # bez okładki – trudno


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)
