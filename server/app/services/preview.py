"""Odsłuch z internetu z pamięcią podręczną.

yt-dlp pobiera dźwięk do pliku (zwykle 1–3 s), a odtwarzacz dostaje go już w trakcie
pobierania. Rozmiar pliku znamy z góry, więc działa przewijanie (zapytania Range) –
fragment, którego jeszcze nie ma, jest wysyłany, gdy tylko dotrze. Ponowny odsłuch
idzie prosto z dysku. Najdawniej słuchane pliki są kasowane powyżej limitu miejsca."""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .ytdlp import YtDlp, YtDlpError

log = logging.getLogger(__name__)

MIME = {"m4a": "audio/mp4", "mp4": "audio/mp4", "opus": "audio/ogg", "ogg": "audio/ogg",
        "mp3": "audio/mpeg"}


@dataclass
class PreviewFile:
    path: Path
    size: int | None  # None – rozmiar nieznany z góry (wtedy bez przewijania w trakcie pobierania)
    ext: str
    done: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None

    video: bool = False

    @property
    def media_type(self) -> str:
        if self.ext == "webm":
            return "video/webm" if self.video else "audio/webm"
        if self.ext == "mp4" and self.video:
            return "video/mp4"
        return MIME.get(self.ext, "application/octet-stream")

    def available(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            return 0


class PreviewCache:
    AUDIO = "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio"
    # Teledysk w tle: sam obraz, do 720p (dźwięk gra z utworu).
    VIDEO = "bestvideo[height<=720][ext=mp4]/bestvideo[height<=720]/bestvideo"

    def __init__(self, ytdlp: YtDlp, cache_dir: Path, max_bytes: int, fmt: str = AUDIO, video: bool = False):
        self.ytdlp = ytdlp
        self.dir = cache_dir
        self.max_bytes = max_bytes
        self.fmt = fmt
        self.video = video
        self._active: dict[str, tuple[PreviewFile, asyncio.Task]] = {}
        shutil.rmtree(self.dir / "tmp", ignore_errors=True)  # przerwane pobierania z poprzedniego uruchomienia

    def cached(self, video_id: str) -> PreviewFile | None:
        for p in self.dir.glob(f"{video_id}.*"):
            if p.is_file():
                p.touch()  # „ostatnio słuchane” – do sprzątania
                f = PreviewFile(p, p.stat().st_size, p.suffix.lstrip("."), video=self.video)
                f.done.set()
                return f
        return None

    async def get(self, video_id: str, timeout: float = 60, url: str | None = None) -> PreviewFile:
        """Plik gotowy albo w trakcie pobierania (z już znanym rozmiarem).
        [url] – adres spoza YouTube; wtedy [video_id] to tylko klucz w pamięci podręcznej."""
        if (f := self.cached(video_id)) is not None:
            return f
        if video_id not in self._active:
            info_ready: asyncio.Future[PreviewFile] = asyncio.get_running_loop().create_future()
            task = asyncio.create_task(self._fetch(video_id, info_ready, url))
            self._active[video_id] = (None, task)  # type: ignore[assignment]
            try:
                pf = await asyncio.wait_for(asyncio.shield(info_ready), timeout)
            except Exception:
                task.cancel()
                self._active.pop(video_id, None)
                raise
            self._active[video_id] = (pf, task)
            return pf
        # Ktoś już pobiera ten utwór – czekamy, aż znany będzie plik.
        for _ in range(int(timeout * 10)):
            pf, task = self._active.get(video_id, (None, None))
            if pf is not None:
                return pf
            if task is None:
                break
            await asyncio.sleep(0.1)
        if (f := self.cached(video_id)) is not None:
            return f
        raise YtDlpError("preview.failed")

    async def _fetch(self, video_id: str, info_ready: asyncio.Future, url: str | None = None) -> None:
        workdir = self.dir / "tmp" / video_id
        pf: PreviewFile | None = None

        def on_info(ext: str, size: int | None):
            nonlocal pf
            pf = PreviewFile(workdir / f"audio.{ext}", size, ext, video=self.video)
            if not info_ready.done():
                info_ready.set_result(pf)

        try:
            source = url or f"https://www.youtube.com/watch?v={video_id}"
            fmt = self.fmt if url is None else "bestaudio/best"
            await self.ytdlp.fetch_audio(source, workdir, on_info, fmt)
            if pf is None:
                raise YtDlpError("ytdlp.no_info")
            final = self.dir / f"{video_id}.{pf.ext}"
            pf.path.replace(final)
            pf.path, pf.size = final, final.stat().st_size
        except asyncio.CancelledError:
            if pf is not None:
                pf.error = "przerwane"
            raise
        except Exception as e:
            log.warning("Odsłuch %s nie powiódł się: %s", video_id, e)
            if not info_ready.done():
                info_ready.set_exception(e if isinstance(e, YtDlpError) else YtDlpError(error=str(e)))
            if pf is not None:
                pf.error = str(e)
        finally:
            if pf is not None:
                pf.done.set()
            self._active.pop(video_id, None)
            shutil.rmtree(workdir, ignore_errors=True)
            self._prune()

    def _prune(self) -> None:
        files = sorted((p for p in self.dir.glob("*.*") if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files)
        for p in files:
            if total <= self.max_bytes:
                break
            total -= p.stat().st_size
            p.unlink(missing_ok=True)

    async def stop(self) -> None:
        tasks = [t for _, t in self._active.values()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def read_range(pf: PreviewFile, start: int, end: int, chunk: int = 64 * 1024):
    """Czyta bajty [start, end] z pliku, który może być jeszcze pobierany."""
    pos = start
    while pos <= end:
        avail = pf.available()
        if pos >= avail:
            if pf.done.is_set():
                # Pobieranie zakończone (albo plik przeniesiony do pamięci podręcznej).
                if pf.error or pos >= (pf.size or avail):
                    return
            else:
                await asyncio.sleep(0.05)
                continue
        try:
            with open(pf.path, "rb") as fh:
                fh.seek(pos)
                data = fh.read(min(chunk, end - pos + 1))
        except FileNotFoundError:
            # Właśnie przeniesiony z folderu tymczasowego do pamięci podręcznej.
            await asyncio.sleep(0.02)
            continue
        if not data:
            await asyncio.sleep(0.05)
            continue
        pos += len(data)
        yield data
