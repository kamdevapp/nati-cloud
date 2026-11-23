"""Punkt wejścia serwera Nati.

Uruchomienie deweloperskie:  uvicorn app.main:app --reload
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse

from . import __version__, db
from .api import (admin, auth, edit, extras, home, library, lyrics, me, media, online, order, pairing, playlists,
                  remote, upload)
from .config import get_settings
from .errors import AppError, error_body
from .services.downloader import DownloadManager
from .services import genres as genre_catalog
from .services import server_settings
from .services.enrich import Enricher
from .services.library_edit import LibraryEditor
from .services.preview import PreviewCache
from .services.scanner import LibraryScanner
from .services.ytdlp import YtDlp

STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("nati")

_TOKEN_IN_URL = re.compile(r"(token=)[^&\s\"]+")


class _RedactTokens(logging.Filter):
    """Token logowania z adresu (?token=…) nie może trafiać do logów serwera."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_TOKEN_IN_URL.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactTokens())


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Zlecone przywrócenie kopii / wyczyszczenie – zanim baza zostanie otwarta.
    from .services import maintenance
    maintenance.apply_pending(settings)
    db.init_db(f"sqlite:///{settings.db_path}")
    with db.new_session() as session:
        genre_catalog.seed(session)
        server_settings.load(session)  # ustawienia zmieniane w aplikacji (np. pobieranie z internetu)
    app.state.scanner = LibraryScanner(db.new_session, settings.library_roots, settings.covers_dir)
    log.info("Nati %s – foldery z muzyką: %s", __version__, ", ".join(map(str, settings.library_roots)))
    if settings.scan_on_startup:
        app.state.scanner.start_background()
    app.state.editor = LibraryEditor(db.new_session, app.state.scanner, settings.covers_dir, settings.trash_dir,
                                     write_tags=settings.write_tags)
    ytdlp = YtDlp(settings.tools_dir)
    ytdlp.cookies_file = settings.cookies_file
    app.state.downloads = DownloadManager(
        db.new_session,
        ytdlp,
        app.state.scanner,
        settings.online_dir,
        settings.data_dir / "tmp",
        workers=settings.download_workers,
        auto_update=settings.ytdlp_auto_update,
        use_musicbrainz=settings.musicbrainz,
    )
    app.state.downloads.pace = settings.download_pause
    app.state.enricher = Enricher(db.new_session, app.state.scanner, app.state.editor,
                                  enabled=settings.enrich)
    app.state.enricher.start(settings.data_dir)
    app.state.scanner.on_finished = app.state.enricher.request
    app.state.downloads.on_done = app.state.enricher.request
    app.state.preview = PreviewCache(app.state.downloads.ytdlp, settings.cache_dir / "preview",
                                     settings.preview_cache_mb * 1024 * 1024)
    app.state.video = PreviewCache(app.state.downloads.ytdlp, settings.cache_dir / "video",
                                   settings.preview_cache_mb * 1024 * 1024, PreviewCache.VIDEO, video=True)
    app.state.downloads.start()  # kolejka – pobiera tylko, gdy pobieranie jest włączone (przełącznik w aplikacji)
    housekeeping = asyncio.create_task(_housekeeping(app, settings.trash_days))
    yield
    housekeeping.cancel()
    await app.state.downloads.stop()
    await app.state.preview.stop()
    await app.state.video.stop()
    db.close_db()


async def _housekeeping(app: FastAPI, trash_days: int):
    """Raz na dobę: opróżnianie kosza ze starych usuniętych utworów."""
    while True:
        try:
            await asyncio.to_thread(app.state.editor.purge_trash, trash_days)
        except Exception:
            log.exception("Opróżnianie kosza nie powiodło się")
        await asyncio.sleep(86400)


app = FastAPI(title="Nati", version=__version__, lifespan=lifespan)


@app.exception_handler(AppError)
async def _app_error(_request, exc: AppError):
    """Błąd dla aplikacji: tekst angielski (detail) + kod i parametry – aplikacja tłumaczy kod."""
    return JSONResponse(status_code=exc.status_code, content=error_body(exc.code, exc.params),
                        headers=exc.headers)
for r in (auth.router, pairing.router, remote.router, library.router, home.router, me.router, edit.router, lyrics.router, upload.router, media.router,
          playlists.router, online.router, extras.router, order.router, admin.router):
    app.include_router(r)


@app.get("/", include_in_schema=False)
def web_ui():
    """Prosty panel WWW: konfiguracja pierwszego uruchomienia i podgląd biblioteki."""
    return FileResponse(STATIC_DIR / "index.html")
