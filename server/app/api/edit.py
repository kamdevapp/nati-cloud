"""Edycja biblioteki: tagi, okładki, zdjęcia wykonawców, usuwanie z chmury."""

from __future__ import annotations

import mimetypes
from contextlib import contextmanager
from typing import Annotated
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from ..errors import AppError, CodedError
from ..config import get_settings
from ..db.models import Album, Artist, Genre, Playlist, Track
from ..schemas import (AlbumEdit, AlbumOut, ArtistEdit, ArtistOut, DeleteRequest, ImageCandidate, ImageUrl,
                       TrackEdit, TrackOut)
from ..security import DB, AdminUser, CuratorUser, CurrentUser, StreamUser
from ..services import online_search
from ..services.musicbrainz import USER_AGENT
from ..services.library_edit import MAX_IMAGE, EditError, LibraryEditor
from .convert import album_out, cover_id, track_out
from .library import artist_out as _artist_out

router = APIRouter(prefix="/api", tags=["edit"])


def _editor(request: Request) -> LibraryEditor:
    return request.app.state.editor


Editor = Annotated[LibraryEditor, Depends(_editor)]

# Okładki „z internetu” pobieramy tylko z serwisów, z których pochodzą propozycje –
# serwer nie może służyć do odpytywania dowolnych adresów (np. w sieci domowej).
_IMAGE_HOSTS = ("googleusercontent.com", "ggpht.com", "ytimg.com", "mzstatic.com", "coverartarchive.org",
                "archive.org", "wikimedia.org", "openverse.org")


@contextmanager
def _errors():
    try:
        yield
    except CodedError as e:  # EditError, biblioteka zajęta (skan)
        raise AppError.of(e) from e


def _album(db, album_id: int) -> AlbumOut:
    from .library import albums_with_counts
    return albums_with_counts(db, select(Album).where(Album.id == album_id))[0]


async def _body_image(request: Request) -> bytes:
    data = await request.body()
    if not data:
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "image.missing_body")
    return data


async def _download_image(url: str) -> bytes:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not any(host == h or host.endswith("." + h) for h in _IMAGE_HOSTS):
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "image.url_unsupported")
    try:
        # Wikimedia odrzuca (403) zapytania bez przedstawienia się – jak przy wyszukiwaniu.
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(url)
            r.raise_for_status()
    except httpx.HTTPError as e:
        raise AppError(status.HTTP_502_BAD_GATEWAY, "image.download_failed", error=str(e)) from e
    if len(r.content) > MAX_IMAGE:
        raise AppError(status.HTTP_413_CONTENT_TOO_LARGE, "image.too_large")
    return r.content


# ---------------------------------------------------------------- tagi

@router.patch("/tracks/{track_id}", response_model=TrackOut)
def edit_track(track_id: int, body: TrackEdit, _: CuratorUser, db: DB, editor: Editor):
    with _errors():
        new_id = editor.edit_track(track_id, body.model_dump(exclude_unset=True))
    track = db.scalar(select(Track).options(joinedload(Track.album).joinedload(Album.artist))
                      .where(Track.id == new_id))
    return track_out(track)


@router.patch("/albums/{album_id}", response_model=AlbumOut)
def edit_album(album_id: int, body: AlbumEdit, _: CuratorUser, db: DB, editor: Editor):
    with _errors():
        new_id = editor.edit_album(album_id, body.title, body.artist, body.year, "year" in body.model_fields_set,
                                   body.genre)
    return _album(db, new_id)


@router.patch("/artists/{artist_id}", response_model=ArtistOut)
def rename_artist(artist_id: int, body: ArtistEdit, _: CuratorUser, db: DB, editor: Editor):
    with _errors():
        new_id = editor.rename_artist(artist_id, body.name)
    artist = db.get(Artist, new_id)
    return _artist_out(artist, len(artist.albums))


# ---------------------------------------------------------------- okładki albumów

@router.put("/albums/{album_id}/cover", response_model=AlbumOut)
async def upload_album_cover(album_id: int, request: Request, _: CuratorUser, db: DB, editor: Editor):
    """Treść zapytania = plik obrazka (JPEG/PNG/WebP)."""
    data = await _body_image(request)
    with _errors():
        await run_in_threadpool(editor.set_album_cover, album_id, data)
    return _album(db, album_id)


@router.post("/albums/{album_id}/cover/url", response_model=AlbumOut)
async def album_cover_from_url(album_id: int, body: ImageUrl, _: CuratorUser, db: DB, editor: Editor):
    data = await _download_image(body.url)
    with _errors():
        await run_in_threadpool(editor.set_album_cover, album_id, data)
    return _album(db, album_id)


@router.get("/albums/{album_id}/cover/candidates", response_model=list[ImageCandidate])
async def album_cover_candidates(album_id: int, _: CuratorUser, db: DB):
    album = db.get(Album, album_id, options=[joinedload(Album.artist)])
    if album is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "album.not_found")
    return await run_in_threadpool(online_search.cover_candidates, album.artist.name, album.title)


# ---------------------------------------------------------------- zdjęcia wykonawców

@router.get("/artists/{artist_id}/image")
def artist_image(artist_id: int, _: StreamUser, db: DB):
    artist = db.get(Artist, artist_id)
    path = get_settings().covers_dir / artist.image if artist and artist.image else None
    if path is None or not path.is_file():
        raise AppError(status.HTTP_404_NOT_FOUND, "photo.not_found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800"})


@router.put("/artists/{artist_id}/image", response_model=ArtistOut)
async def upload_artist_image(artist_id: int, request: Request, _: CuratorUser, db: DB, editor: Editor):
    data = await _body_image(request)
    with _errors():
        await run_in_threadpool(editor.set_artist_image, artist_id, data)
    return _artist_out(db.get(Artist, artist_id), 0)


@router.post("/artists/{artist_id}/image/url", response_model=ArtistOut)
async def artist_image_from_url(artist_id: int, body: ImageUrl, _: CuratorUser, db: DB, editor: Editor):
    data = await _download_image(body.url)
    with _errors():
        await run_in_threadpool(editor.set_artist_image, artist_id, data)
    return _artist_out(db.get(Artist, artist_id), 0)


@router.delete("/artists/{artist_id}/image", status_code=status.HTTP_204_NO_CONTENT)
def remove_artist_image(artist_id: int, _: CuratorUser, editor: Editor):
    with _errors():
        editor.set_artist_image(artist_id, None)


@router.get("/artists/{artist_id}/image/candidates", response_model=list[ImageCandidate])
async def artist_image_candidates(artist_id: int, _: CuratorUser, db: DB):
    artist = db.get(Artist, artist_id)
    if artist is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "artist.not_found")
    return await run_in_threadpool(online_search.artist_image_candidates, artist.name)


# ---------------------------------------------------------------- usuwanie z chmury

def _delete(user, ids: list[int], editor: LibraryEditor) -> dict:
    with _errors():
        n = editor.delete_tracks(user, ids, get_settings().library_roots)
    return {"deleted": n}


@router.delete("/tracks/{track_id}")
def delete_track(track_id: int, user: AdminUser, editor: Editor):
    """Usuwa utwór z chmury (plik trafia do kosza data/trash na NATI_TRASH_DAYS dni)."""
    return _delete(user, [track_id], editor)


@router.delete("/albums/{album_id}")
def delete_album(album_id: int, user: AdminUser, db: DB, editor: Editor):
    album = db.get(Album, album_id)
    if album is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "album.not_found")
    return _delete(user, [t.id for t in album.tracks], editor)


@router.post("/tracks/delete")
def delete_tracks(body: DeleteRequest, user: AdminUser, editor: Editor):
    return _delete(user, body.track_ids, editor)



# ---------------------------------------------------------------- obrazki playlist

def _own_playlist(db, playlist_id: int, user) -> Playlist:
    from .playlists import get_access
    return get_access(db, playlist_id, user, write=True)


def _set_playlist_image(db, editor: LibraryEditor, pl: Playlist, data: bytes | None) -> dict:
    with _errors():
        pl.image = editor.save_image(data) if data is not None else None
    db.commit()
    editor.cleanup_images()
    return {"image_id": cover_id(pl.image)}


@router.get("/playlists/{playlist_id}/image")
def playlist_image(playlist_id: int, _: StreamUser, db: DB):
    pl = db.get(Playlist, playlist_id)
    path = get_settings().covers_dir / pl.image if pl and pl.image else None
    if path is None or not path.is_file():
        raise AppError(status.HTTP_404_NOT_FOUND, "image.not_found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800"})


@router.put("/playlists/{playlist_id}/image")
async def upload_playlist_image(playlist_id: int, request: Request, user: CurrentUser, db: DB, editor: Editor):
    pl = _own_playlist(db, playlist_id, user)
    return _set_playlist_image(db, editor, pl, await _body_image(request))


@router.post("/playlists/{playlist_id}/image/url")
async def playlist_image_from_url(playlist_id: int, body: ImageUrl, user: CurrentUser, db: DB, editor: Editor):
    pl = _own_playlist(db, playlist_id, user)
    return _set_playlist_image(db, editor, pl, await _download_image(body.url))


@router.delete("/playlists/{playlist_id}/image", status_code=status.HTTP_204_NO_CONTENT)
def remove_playlist_image(playlist_id: int, user: CurrentUser, db: DB, editor: Editor):
    _set_playlist_image(db, editor, _own_playlist(db, playlist_id, user), None)


@router.get("/images/search", response_model=list[ImageCandidate])
async def search_free_images(q: Annotated[str, Query(min_length=1, max_length=200)], _: CurrentUser,
                             source: Annotated[str | None, Query(pattern="^(commons|openverse)$")] = None):
    """Darmowe obrazki (wolne licencje) – np. na okładkę playlisty."""
    return await run_in_threadpool(online_search.free_images, q, source)


# ---------------------------------------------------------------- obrazki gatunków i zdjęcia w tle

def _image_file(name: str | None, missing: str) -> FileResponse:
    path = get_settings().covers_dir / name if name else None
    if path is None or not path.is_file():
        raise AppError(status.HTTP_404_NOT_FOUND, missing)
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800"})


def _set_image(db, editor: LibraryEditor, obj, field: str, data: bytes | None) -> dict:
    with _errors():
        setattr(obj, field, editor.save_image(data) if data is not None else None)
    db.commit()
    editor.cleanup_images()
    return {"image_id": cover_id(getattr(obj, field))}


def _genre(db, genre_id: int) -> Genre:
    g = db.get(Genre, genre_id)
    if g is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "genre.not_found")
    return g


@router.get("/genres/{genre_id}/image")
def genre_image(genre_id: int, _: StreamUser, db: DB):
    g = db.get(Genre, genre_id)
    return _image_file(g.image if g else None, "image.not_found")


@router.put("/genres/{genre_id}/image")
async def upload_genre_image(genre_id: int, request: Request, _: CuratorUser, db: DB, editor: Editor):
    return _set_image(db, editor, _genre(db, genre_id), "image", await _body_image(request))


@router.post("/genres/{genre_id}/image/url")
async def genre_image_from_url(genre_id: int, body: ImageUrl, _: CuratorUser, db: DB, editor: Editor):
    return _set_image(db, editor, _genre(db, genre_id), "image", await _download_image(body.url))


@router.delete("/genres/{genre_id}/image", status_code=status.HTTP_204_NO_CONTENT)
def remove_genre_image(genre_id: int, _: CuratorUser, db: DB, editor: Editor):
    _set_image(db, editor, _genre(db, genre_id), "image", None)


# Zdjęcie w tle nagłówka (panorama pod gradientem): albumy i wykonawcy – kurator, playlisty – kto może je zmieniać.
_BANNER_KINDS = {"albums": Album, "artists": Artist, "playlists": Playlist}


def _banner_target(db, kind: str, item_id: int, user, write: bool):
    model = _BANNER_KINDS.get(kind)
    if model is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "edit.unknown_kind")
    if model is Playlist:
        from .playlists import get_access
        return get_access(db, item_id, user, write=write)
    if write and not user.can_curate:
        raise AppError(status.HTTP_403_FORBIDDEN, "auth.forbidden")
    obj = db.get(model, item_id)
    if obj is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "edit.item_not_found")
    return obj


@router.get("/{kind}/{item_id}/banner")
def banner_image(kind: str, item_id: int, _: StreamUser, db: DB):
    model = _BANNER_KINDS.get(kind)
    obj = db.get(model, item_id) if model else None
    return _image_file(obj.banner if obj else None, "background.not_found")


@router.put("/{kind}/{item_id}/banner")
async def upload_banner(kind: str, item_id: int, request: Request, user: CurrentUser, db: DB, editor: Editor):
    obj = _banner_target(db, kind, item_id, user, write=True)
    return _set_image(db, editor, obj, "banner", await _body_image(request))


@router.post("/{kind}/{item_id}/banner/url")
async def banner_from_url(kind: str, item_id: int, body: ImageUrl, user: CurrentUser, db: DB, editor: Editor):
    obj = _banner_target(db, kind, item_id, user, write=True)
    return _set_image(db, editor, obj, "banner", await _download_image(body.url))


@router.delete("/{kind}/{item_id}/banner", status_code=status.HTTP_204_NO_CONTENT)
def remove_banner(kind: str, item_id: int, user: CurrentUser, db: DB, editor: Editor):
    _set_image(db, editor, _banner_target(db, kind, item_id, user, write=True), "banner", None)
