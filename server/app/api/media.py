"""Streaming plików audio i okładki."""

import asyncio
import mimetypes
import shutil
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, StreamingResponse

from ..errors import AppError
from ..config import get_settings
from ..db.models import Album, Track
from ..security import DB, StreamUser

router = APIRouter(prefix="/api", tags=["media"])

AUDIO_MIME = {
    "mp3": "audio/mpeg",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "aac": "audio/aac",
    "wav": "audio/wav",
    "wma": "audio/x-ms-wma",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
}


FFMPEG = shutil.which("ffmpeg")
CHUNK = 64 * 1024


@router.get("/stream/{track_id}")
async def stream(
    track_id: int,
    _: StreamUser,
    db: DB,
    bitrate: Annotated[int | None, Query(ge=32, le=320, description="Maks. bitrate w kbps (transkodowanie do Opus)")] = None,
):
    """Zwraca oryginalny plik z obsługą Range (przewijanie, wznawianie pobierania).

    Z ?bitrate=N plik o wyższym bitrate jest transkodowany „w locie” do Opus N kbps –
    mniej danych na telefonie i mniej miejsca na pobrane utwory. Strumień transkodowany
    nie obsługuje Range (rozmiar nie jest znany z góry)."""
    track = db.get(Track, track_id)
    if track is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "track.not_found")
    path = Path(track.path)
    if not path.is_file():
        raise AppError(status.HTTP_410_GONE, "track.file_missing")
    name = f"{track.artist_name} - {track.title}"

    # Opus jest wydajniejszy od MP3/AAC, więc „równy” bitrate i tak by nic nie dał – margines 16 kbps.
    if bitrate is not None and FFMPEG and (track.bitrate is None or track.bitrate > bitrate + 16):
        return StreamingResponse(
            _transcode(path, bitrate),
            media_type="audio/ogg",
            headers={
                "Content-Disposition": f"inline; filename*=UTF-8''{_quote(name)}.opus",
                "X-Nati-Transcoded": f"opus/{bitrate}",
                # Dla paska postępu pobierania w aplikacji.
                "X-Nati-Estimated-Size": str(int(track.duration * bitrate * 1000 / 8)),
            },
        )
    return FileResponse(
        path,
        media_type=AUDIO_MIME.get(track.format, "application/octet-stream"),
        filename=f"{name}{path.suffix}",
        content_disposition_type="inline",
    )


def _quote(s: str) -> str:
    return quote(s, safe="")


async def _transcode(path: Path, bitrate: int):
    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-nostdin", "-loglevel", "error", "-i", str(path),
        "-map", "0:a:0", "-vn", "-c:a", "libopus", "-b:a", f"{bitrate}k", "-f", "ogg", "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        while chunk := await proc.stdout.read(CHUNK):
            yield chunk
        await proc.wait()
    finally:
        # Klient się rozłączył (np. przeskoczył utwór) – nie zostawiaj ffmpeg w tle.
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


@router.get("/albums/{album_id}/cover")
def album_cover(album_id: int, _: StreamUser, db: DB):
    album = db.get(Album, album_id)
    if album is None or album.cover is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "cover.not_found")
    path = get_settings().covers_dir / album.cover
    if not path.is_file():
        raise AppError(status.HTTP_404_NOT_FOUND, "cover.not_found")
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
        # Nazwa pliku to hash treści, więc można cache'ować długo.
        headers={"Cache-Control": "private, max-age=604800"},
    )
