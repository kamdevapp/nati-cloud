"""Wyszukiwanie w internecie i pobieranie do chmury (dla każdego zalogowanego)."""

from __future__ import annotations

import hashlib
import json
import re
import time
from functools import lru_cache

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from ..errors import AppError, CodedError
from ..config import get_settings
from ..db.models import DownloadJob, Track
from ..security import DB, AdminUser, CuratorUser, CurrentUser, StreamUser
from ..services import online_search, other_sources
from ..services.downloader import ACTIVE, DownloadManager
from ..services import server_settings
from ..services.preview import PreviewCache, read_range
from ..services.ytdlp import YtDlpError


def _enabled():
    if not server_settings.online_enabled():
        raise AppError(status.HTTP_403_FORBIDDEN, "online.disabled")


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")

router = APIRouter(prefix="/api/online", tags=["online"], dependencies=[Depends(_enabled)])


def _manager(request: Request) -> DownloadManager:
    return request.app.state.downloads


Manager = Annotated[DownloadManager, Depends(_manager)]


# ---------------------------------------------------------------- schematy

Source = Literal["ytmusic", "youtube", "soundcloud", "bandcamp"]
Kind = Literal["all", "songs", "albums", "artists"]


class OnlineTrack(BaseModel):
    id: str  # videoId (YouTube Music, YouTube) albo adres strony (SoundCloud, Bandcamp)
    title: str
    artist: str
    album: str | None = None
    album_id: str | None = None
    track_no: int | None = None
    duration: float = 0
    thumbnail: str | None = None
    source: Source = "ytmusic"


class OnlineAlbum(BaseModel):
    id: str  # browseId albo adres albumu (Bandcamp)
    title: str
    artist: str
    year: int | None = None
    kind: str = "Album"
    thumbnail: str | None = None
    source: Source = "ytmusic"


class OnlineArtist(BaseModel):
    id: str
    name: str
    thumbnail: str | None = None


class SearchOut(BaseModel):
    tracks: list[OnlineTrack]
    albums: list[OnlineAlbum]
    artists: list[OnlineArtist] = []


class AlbumOut(OnlineAlbum):
    tracks: list[OnlineTrack]


class ArtistOut(OnlineArtist):
    tracks: list[OnlineTrack]
    albums: list[OnlineAlbum]
    singles: list[OnlineAlbum]


class DiscoverOut(BaseModel):
    tracks: list[OnlineTrack]
    albums: list[OnlineAlbum]


class DownloadRequest(BaseModel):
    kind: Literal["track", "album", "url"]
    track: OnlineTrack | None = None
    album_id: str | None = None
    url: str | None = Field(default=None, max_length=1024)


class JobOut(BaseModel):
    id: int
    source: str
    source_id: str
    title: str
    artist: str | None
    album: str | None
    thumbnail: str | None
    status: str
    progress: float
    error: str | None  # tekst angielski (zapasowy)
    error_code: str | None = None  # kod komunikatu (messages.py) – aplikacja tłumaczy
    error_params: dict = {}
    track_id: int | None


def _job_out(j: DownloadJob) -> JobOut:
    return JobOut(id=j.id, source=j.source, source_id=j.source_id, title=j.title, artist=j.artist, album=j.album,
                  thumbnail=j.thumbnail, status=j.status, progress=j.progress, error=j.error,
                  error_code=j.error_code, error_params=json.loads(j.error_params or "{}"), track_id=j.track_id)


async def _yt(fn, *args, service: str = "YouTube Music", **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except CodedError as e:
        raise AppError.of(e) from e
    except Exception as e:
        raise AppError(status.HTTP_502_BAD_GATEWAY, "service.unavailable", service=service, error=str(e)) from e


_SERVICE = {"ytmusic": "YouTube Music", "youtube": "YouTube", "soundcloud": "SoundCloud", "bandcamp": "Bandcamp"}


# ---------------------------------------------------------------- wyszukiwanie

@router.get("/status")
def online_status(_: CurrentUser, manager: Manager):
    return {"enabled": True, "ytdlp_version": manager.ytdlp_version, "update_error": manager.last_update_error,
            "blocked_until": manager.blocked_until if manager.blocked else None,
            "has_cookies": manager.ytdlp.has_cookies}


@router.get("/search", response_model=SearchOut)
async def search(q: Annotated[str, Query(min_length=1, max_length=200)], _: CurrentUser, manager: Manager,
                 kind: Kind = "all", source: Source = "ytmusic"):
    """Wyszukiwanie w jednym serwisie. „Wszędzie” to kilka zapytań z aplikacji – wyniki pokazują się,
    w miarę jak przychodzą. Rodzaje, których serwis nie ma, dają puste listy."""
    empty = {"tracks": [], "albums": [], "artists": []}
    if kind not in other_sources.KINDS[source]:
        return empty
    if source == "ytmusic":
        return await _yt(online_search.search, q, kind=kind)
    if source == "bandcamp":
        return await _yt(other_sources.search_bandcamp, q, kind, service="Bandcamp")
    find = other_sources.search_youtube if source == "youtube" else other_sources.search_soundcloud
    try:
        tracks = await find(manager.ytdlp, q, 20 if kind == "all" else 40)
    except (YtDlpError, TimeoutError, ValueError) as e:
        raise AppError(status.HTTP_502_BAD_GATEWAY, "service.unavailable", service=_SERVICE[source], error=str(e)) from e
    return dict(empty, tracks=tracks)


async def _album(album_id: str) -> dict:
    if other_sources.is_url(album_id):
        if "bandcamp.com" not in album_id:
            raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "online.album_unsupported")
        return await _yt(other_sources.bandcamp_album, album_id, service="Bandcamp")
    return await _yt(online_search.album, album_id)


@router.get("/albums/{browse_id}", response_model=AlbumOut)
async def online_album(browse_id: str, _: CurrentUser):
    return await _album(browse_id)


@router.get("/album", response_model=AlbumOut)
async def online_album_by_id(id: Annotated[str, Query(min_length=1, max_length=1024)], _: CurrentUser):
    """Jak /albums/{id}, ale id może być adresem (album z Bandcampa)."""
    return await _album(id)


@router.get("/artists/{browse_id}", response_model=ArtistOut)
async def online_artist(browse_id: str, _: CurrentUser):
    return await _yt(online_search.artist, browse_id)


_services_cache: list[str] = []


@router.get("/services", response_model=list[str])
async def supported_services(_: CurrentUser, manager: Manager):
    """Serwisy, z których yt-dlp na tym serwerze potrafi pobierać (do „Wklej link”)."""
    global _services_cache
    if not _services_cache:
        try:
            _services_cache = await manager.ytdlp.extractors()
        except YtDlpError as e:
            raise AppError.of(e) from e
        except TimeoutError as e:
            raise AppError(status.HTTP_502_BAD_GATEWAY, "service.unavailable", service="yt-dlp", error=str(e)) from e
    return _services_cache


# ---------------------------------------------------------------- pobieranie

def _existing(db, user_id: int, source_id: str) -> DownloadJob | None:
    """Ten sam utwór już pobrany (plik w bibliotece) albo w kolejce – nie pobieramy drugi raz."""
    jobs = db.scalars(select(DownloadJob).where(DownloadJob.source_id == source_id,
                                                DownloadJob.status.in_((*ACTIVE, "done")))).all()
    for j in jobs:
        if j.status in ACTIVE or (j.track_id and db.get(Track, j.track_id)):
            return j
    return None


@router.post("/downloads", response_model=list[JobOut], status_code=status.HTTP_202_ACCEPTED)
async def start_download(body: DownloadRequest, user: CuratorUser, db: DB, manager: Manager):
    if body.kind == "track":
        if body.track is None:
            raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "online.missing_track")
        tracks, meta = [body.track], {}
    elif body.kind == "album":
        if not body.album_id:
            raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "online.missing_album")
        album = await _album(body.album_id)
        tracks = [OnlineTrack(**t) for t in album["tracks"]]
        meta = {"album_artist": album["artist"], "year": album["year"], "thumbnail": album["thumbnail"]}
    else:
        if not body.url or not body.url.startswith(("http://", "https://")):
            raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "url.need_http")
        job = _existing(db, user.id, body.url) or DownloadJob(
            user_id=user.id, source="url", source_id=body.url, title=body.url, artist=None, album=None,
            album_artist=None, track_no=None, year=None, thumbnail=None)
        db.add(job)
        db.commit()
        manager.notify()
        return [_job_out(job)]

    jobs = []
    for t in tracks:
        if t.source != "ytmusic":
            # Zwykły YouTube, SoundCloud, Bandcamp – jak link: tagi z serwisu (+ MusicBrainz).
            url = f"https://www.youtube.com/watch?v={t.id}" if t.source == "youtube" else t.id
            job = _existing(db, user.id, url) or DownloadJob(
                user_id=user.id, source="url", source_id=url, title=f"{t.artist} – {t.title}", artist=None,
                album=None, album_artist=None, track_no=None, year=None, thumbnail=t.thumbnail)
            db.add(job)
            jobs.append(job)
            continue
        job = _existing(db, user.id, t.id)
        if job is None:
            job = DownloadJob(
                user_id=user.id, source="ytmusic", source_id=t.id, title=t.title, artist=t.artist, album=t.album,
                album_artist=meta.get("album_artist") or t.artist, track_no=t.track_no, year=meta.get("year"),
                thumbnail=meta.get("thumbnail") or (t.thumbnail and online_search.big_thumbnail([{"url": t.thumbnail}])),
            )
            db.add(job)
        jobs.append(job)
    db.commit()
    manager.notify()
    return [_job_out(j) for j in jobs]


@router.get("/downloads", response_model=list[JobOut])
def list_downloads(user: CurrentUser, db: DB, limit: Annotated[int, Query(ge=1, le=500)] = 100):
    jobs = db.scalars(select(DownloadJob).where(DownloadJob.user_id == user.id)
                      .order_by(DownloadJob.id.desc()).limit(limit)).all()
    return [_job_out(j) for j in jobs]


def _own_job(db, job_id: int, user) -> DownloadJob:
    job = db.get(DownloadJob, job_id)
    if job is None or (job.user_id != user.id and not user.is_admin):
        raise AppError(status.HTTP_404_NOT_FOUND, "job.not_found")
    return job


@router.delete("/downloads/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_download(job_id: int, user: CurrentUser, db: DB, manager: Manager):
    """Anuluje (jeśli trwa) i usuwa z listy. Pobrany utwór zostaje w bibliotece."""
    job = _own_job(db, job_id, user)
    if job.status in ACTIVE:
        await manager.cancel(job_id)
    db.execute(delete(DownloadJob).where(DownloadJob.id == job_id))
    db.commit()


@router.delete("/downloads", status_code=status.HTTP_204_NO_CONTENT)
def clear_finished(user: CurrentUser, db: DB):
    """Czyści z listy zakończone (pobrane, błędy, anulowane)."""
    db.execute(delete(DownloadJob).where(DownloadJob.user_id == user.id, DownloadJob.status.not_in(ACTIVE)))
    db.commit()


@router.post("/downloads/{job_id}/retry", response_model=JobOut)
def retry_download(job_id: int, user: CuratorUser, db: DB, manager: Manager):
    job = _own_job(db, job_id, user)
    if job.status in ("error", "cancelled"):
        job.status, job.progress, job.error, job.finished_at = "queued", 0.0, None, None
        db.commit()
        manager.notify()
    return _job_out(job)


@router.post("/downloads/resume", status_code=status.HTTP_204_NO_CONTENT)
def resume_downloads(_: CuratorUser, manager: Manager):
    """Wznów kolejkę wstrzymaną przez blokadę YouTube, bez czekania."""
    manager.resume()


@router.put("/cookies", status_code=status.HTTP_204_NO_CONTENT)
async def upload_cookies(_: AdminUser, manager: Manager, file: Annotated[UploadFile, File()]):
    """Plik cookies.txt (format Netscape) z konta YouTube – używany tylko awaryjnie."""
    data = await file.read(2 * 1024 * 1024 + 1)
    text = data.decode(errors="replace")
    if len(data) > 2 * 1024 * 1024 or not (
            text.lstrip().startswith(("# Netscape HTTP Cookie File", "# HTTP Cookie File"))
            or any(line.count("\t") >= 6 for line in text.splitlines()[:50])):
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "cookies.invalid")
    if "youtube.com" not in text:
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "cookies.no_youtube")
    path = get_settings().cookies_file
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.chmod(0o600)
    tmp.replace(path)
    manager.ytdlp.cookies_file = path
    if manager.blocked:
        manager.ytdlp.cookies_until = time.time() + 3600
        manager.resume()


@router.delete("/cookies", status_code=status.HTTP_204_NO_CONTENT)
def remove_cookies(_: AdminUser, manager: Manager):
    get_settings().cookies_file.unlink(missing_ok=True)
    manager.ytdlp.cookies_until = 0.0


@router.post("/ytdlp/update")
async def update_ytdlp(_: AdminUser, manager: Manager):
    try:
        manager.ytdlp_version = await manager.ytdlp.update()
    except YtDlpError as e:
        raise AppError.of(e) from e
    return {"ytdlp_version": manager.ytdlp_version}


# ---------------------------------------------------------------- odsłuch bez pobierania

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


@router.get("/clip")
async def clip(artist: Annotated[str, Query(max_length=300)], title: Annotated[str, Query(max_length=300)],
               _: CurrentUser, db: DB, video_id: Annotated[str | None, Query(max_length=20)] = None,
               track_id: int | None = None):
    """Teledysk do utworu (tło ekranu odtwarzania): {video_id, title}; 404 – brak klipu.

    Utwór pobrany z YouTube – jego własny film (albo oficjalny klip, jeśli to „art track”)."""
    if video_id is None and track_id is not None:
        video_id = _source_video(db, track_id)
        if video_id and not _is_ytmusic(db, track_id):
            return {"video_id": video_id, "title": title}  # zwykły film z YouTube – to on jest klipem
    found = await _yt(_find_clip, artist, title, video_id)
    if found is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "video.not_found")
    return found


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})")


def _source_video(db, track_id: int) -> str | None:
    """Id filmu z YouTube, z którego pobrano utwór (jeśli tak było)."""
    job = db.scalar(select(DownloadJob).where(DownloadJob.track_id == track_id, DownloadJob.status == "done"))
    if job is None:
        return None
    if job.source == "ytmusic":
        return job.source_id
    m = _YT_ID.search(job.source_id) if "youtu" in job.source_id else None
    return m[1] if m else None


def _is_ytmusic(db, track_id: int) -> bool:
    return db.scalar(select(DownloadJob.source).where(DownloadJob.track_id == track_id)) == "ytmusic"


@lru_cache(maxsize=512)
def _find_clip(artist: str, title: str, video_id: str | None):
    return online_search.find_clip(artist, title, video_id)


@router.get("/video/{video_id}")
async def video(video_id: str, request: Request, _: StreamUser):
    """Obraz teledysku (bez dźwięku) – z przewijaniem i pamięcią podręczną."""
    return await _serve(request, request.app.state.video, video_id)


@router.get("/preview/{video_id}")
async def preview(video_id: str, request: Request, _: StreamUser):
    """Odsłuch utworu z YouTube Music przez serwer – z przewijaniem i pamięcią podręczną."""
    return await _serve(request, request.app.state.preview, video_id)


@router.get("/preview-url")
async def preview_url(url: Annotated[str, Query(min_length=8, max_length=1024)], request: Request, _: StreamUser):
    """Odsłuch utworu spoza YouTube (SoundCloud, Bandcamp…) po adresie strony."""
    if not other_sources.is_url(url):
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "url.invalid")
    key = "u" + hashlib.sha1(url.encode()).hexdigest()[:20]
    return await _serve(request, request.app.state.preview, key, url)


async def _serve(request: Request, cache: PreviewCache, video_id: str, url: str | None = None):
    if url is None and not _VIDEO_ID.match(video_id):
        raise AppError(status.HTTP_404_NOT_FOUND, "online.bad_track_id")
    try:
        pf = await cache.get(video_id, url=url)
    except YtDlpError as e:
        raise AppError.of(e) from e
    except TimeoutError as e:
        raise AppError(status.HTTP_502_BAD_GATEWAY, "youtube.unavailable") from e
    if pf.done.is_set() and pf.error is None:
        return FileResponse(pf.path, media_type=pf.media_type, headers={"Cache-Control": "no-store"})
    if pf.size is None:
        # Rozmiar nieznany – zwykły strumień bez przewijania.
        return StreamingResponse(read_range(pf, 0, 1 << 62), media_type=pf.media_type,
                                 headers={"Cache-Control": "no-store"})

    size = pf.size
    start, end, code = 0, size - 1, status.HTTP_200_OK
    if (m := _RANGE.match(request.headers.get("range", "").strip())) and (m[1] or m[2]):
        if m[1]:
            start, end = int(m[1]), int(m[2]) if m[2] else size - 1
        else:  # ostatnie N bajtów
            start = max(size - int(m[2]), 0)
        end = min(end, size - 1)
        if start > end:
            raise HTTPException(status.HTTP_416_RANGE_NOT_SATISFIABLE, headers={"Content-Range": f"bytes */{size}"})
        code = status.HTTP_206_PARTIAL_CONTENT
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "no-store"}
    if code == status.HTTP_206_PARTIAL_CONTENT:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(read_range(pf, start, end), status_code=code, media_type=pf.media_type, headers=headers)
