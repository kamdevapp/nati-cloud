"""Przeglądanie biblioteki: wykonawcy, albumy, utwory, gatunki (z filtrami i sortowaniem), skanowanie."""

import json
from collections import defaultdict
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ..errors import AppError, CodedError
from ..db.models import Album, Artist, Genre, Track, UserOrder, track_genres
from ..schemas import AlbumDetail, AlbumOut, ArtistDetail, ArtistOut, GenreOut, Page, TrackOut
from ..security import DB, AdminUser, CuratorUser, CurrentUser
from ..config import get_settings
from ..services import genres as genre_catalog
from ..services.musicbrainz import USER_AGENT
from ..services.library_edit import EditError
from ..text import normalize
from .convert import album_out, cover_id, track_out

router = APIRouter(prefix="/api", tags=["library"])

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]
GenreQ = Annotated[str | None, Query(description="Tylko z tym gatunkiem (bez względu na wielkość liter)")]


def _like(q: str) -> str:
    escaped = normalize(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _tracks_with_genre(genre: str):
    """Podzapytanie: id utworów z danym gatunkiem."""
    return (select(track_genres.c.track_id).join(Genre, Genre.id == track_genres.c.genre_id)
            .where(Genre.search == normalize(genre)))


def _top(counts: dict[int, dict[str, int]], key: int, n: int = 6) -> list[str]:
    return [g for g, _ in sorted(counts.get(key, {}).items(), key=lambda x: (-x[1], x[0]))[:n]]


def _genre_counts(db, group_col, ids: list[int]) -> dict[int, dict[str, int]]:
    """Ile utworów danego gatunku ma każdy album / wykonawca (do „tagów” na liście)."""
    if not ids:
        return {}
    stmt = (select(group_col, Genre.name, func.count())
            .select_from(Track)
            .join(track_genres, track_genres.c.track_id == Track.id)
            .join(Genre, Genre.id == track_genres.c.genre_id)
            .join(Album, Album.id == Track.album_id)
            .where(group_col.in_(ids))
            .group_by(group_col, Genre.name))
    out: dict[int, dict[str, int]] = defaultdict(dict)
    for key, name, n in db.execute(stmt):
        out[key][name] = n
    return out


def artist_out(a: Artist, album_count: int, genres: list[str] | None = None, track_count: int = 0) -> ArtistOut:
    return ArtistOut(id=a.id, name=a.name, album_count=album_count, image_id=cover_id(a.image), genres=genres or [],
                     banner_id=cover_id(a.banner), track_count=track_count)




# ---------------------------------------------------------------- wykonawcy

@router.get("/artists", response_model=Page[ArtistOut])
def list_artists(_: CurrentUser, db: DB, q: str | None = None, genre: GenreQ = None,
                 offset: Offset = 0, limit: Limit = 100):
    album_count = (
        select(func.count(Album.id)).where(Album.artist_id == Artist.id).correlate(Artist).scalar_subquery()
    )
    track_count = (
        select(func.count(Track.id)).join(Album, Album.id == Track.album_id)
        .where(Album.artist_id == Artist.id).correlate(Artist).scalar_subquery()
    )
    stmt = select(Artist, album_count, track_count)
    if q:
        stmt = stmt.where(Artist.search.like(_like(q), escape="\\"))
    if genre:
        with_genre = select(Album.artist_id).join(Track, Track.album_id == Album.id).where(
            Track.id.in_(_tracks_with_genre(genre)))
        stmt = stmt.where(Artist.id.in_(with_genre))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(func.lower(Artist.name)).offset(offset).limit(limit)).all()
    genres = _genre_counts(db, Album.artist_id, [a.id for a, _, _ in rows])
    items = [artist_out(a, c, _top(genres, a.id), t) for a, c, t in rows]
    return Page(items=items, total=total, offset=offset, limit=limit)


@router.get("/artists/{artist_id}", response_model=ArtistDetail)
def get_artist(artist_id: int, _: CurrentUser, db: DB):
    artist = db.get(Artist, artist_id)
    if artist is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "artist.not_found")
    albums = albums_with_counts(db, select(Album).where(Album.artist_id == artist_id))
    genres = _top(_genre_counts(db, Album.artist_id, [artist.id]), artist.id)
    tracks = sum(a.track_count for a in albums)
    return ArtistDetail(**artist_out(artist, len(albums), genres, tracks).model_dump(), albums=albums)


# ---------------------------------------------------------------- albumy

AlbumSort = Literal["year", "title", "artist", "added"]


def albums_with_counts(db, stmt, offset: int = 0, limit: int | None = None, sort: AlbumSort = "year") -> list[AlbumOut]:
    track_count = (
        select(func.count(Track.id)).where(Track.album_id == Album.id).correlate(Album).scalar_subquery()
    )
    added = select(func.max(Track.added_at)).where(Track.album_id == Album.id).correlate(Album).scalar_subquery()
    order = {
        "year": (Album.year.desc().nulls_last(), func.lower(Album.title)),
        "title": (func.lower(Album.title),),
        "artist": (func.lower(Artist.name), Album.year.nulls_last(), func.lower(Album.title)),
        "added": (added.desc(), func.lower(Album.title)),
    }[sort]
    stmt = (
        stmt.add_columns(track_count)
        .join(Artist, Artist.id == Album.artist_id)
        .options(joinedload(Album.artist))
        .order_by(*order)
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).all()
    genres = _genre_counts(db, Track.album_id, [a.id for a, _ in rows])
    return [album_out(a, c, _top(genres, a.id)) for a, c in rows]




@router.get("/albums", response_model=Page[AlbumOut])
def list_albums(_: CurrentUser, db: DB, q: str | None = None, genre: GenreQ = None, sort: AlbumSort = "year",
                offset: Offset = 0, limit: Limit = 100):
    stmt = select(Album)
    if q:
        stmt = stmt.where(Album.search.like(_like(q), escape="\\"))
    if genre:
        stmt = stmt.where(Album.id.in_(select(Track.album_id).where(Track.id.in_(_tracks_with_genre(genre)))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    return Page(items=albums_with_counts(db, stmt, offset, limit, sort), total=total, offset=offset, limit=limit)


@router.get("/albums/{album_id}", response_model=AlbumDetail)
def get_album(album_id: int, _: CurrentUser, db: DB):
    album = db.get(Album, album_id, options=[joinedload(Album.artist)])
    if album is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "album.not_found")
    tracks = db.scalars(
        select(Track)
        .where(Track.album_id == album_id)
        .order_by(Track.disc_no.nulls_first(), Track.track_no.nulls_last(), Track.title)
    ).all()
    genres = _top(_genre_counts(db, Track.album_id, [album.id]), album.id)
    base = album_out(album, len(tracks), genres)
    return AlbumDetail(**base.model_dump(), tracks=[track_out(t) for t in tracks])


# ---------------------------------------------------------------- utwory

TrackSort = Literal["title", "artist", "album", "added", "year", "custom"]


def _track_query():
    return select(Track).options(joinedload(Track.album).joinedload(Album.artist))


def _order(spec: list[tuple], reverse: bool) -> list:
    """Kolumny sortowania z kierunkiem; [reverse] odwraca całą kolejność. Puste wartości zawsze na końcu."""
    return [(e.desc() if desc != reverse else e.asc()).nulls_last() for e, desc in spec]


@router.get("/tracks", response_model=Page[TrackOut])
def list_tracks(user: CurrentUser, db: DB, q: str | None = None, genre: GenreQ = None, sort: TrackSort = "title",
                offset: Offset = 0, limit: Limit = 100,
                artist_id: Annotated[int | None, Query(description="Tylko z albumów tego wykonawcy")] = None,
                reverse: Annotated[bool, Query(description="Odwrotna kolejność (Z→A, najstarsze…)")] = False):
    stmt = select(Track)
    if artist_id is not None:
        stmt = stmt.where(Track.album_id.in_(select(Album.id).where(Album.artist_id == artist_id)))
    if q:
        stmt = stmt.where(Track.search.like(_like(q), escape="\\"))
    if genre:
        stmt = stmt.where(Track.id.in_(_tracks_with_genre(genre)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    ids = select(stmt.subquery().c.id)
    if sort == "custom":
        return Page(items=_custom_tracks(db, user.id, ids, offset, limit, reverse), total=total, offset=offset,
                    limit=limit)
    title = func.lower(Track.title)
    spec = {
        "title": [(title, False), (Track.id, False)],
        "artist": [(func.lower(Track.artist_name), False), (func.lower(Album.title), False), (Track.disc_no, False),
                   (Track.track_no, False), (Track.id, False)],
        "album": [(func.lower(Album.title), False), (Track.disc_no, False), (Track.track_no, False), (Track.id, False)],
        "added": [(Track.added_at, True), (Track.id, True)],
        "year": [(Album.year, True), (func.lower(Album.title), False), (Track.track_no, False), (Track.id, False)],
    }[sort]
    tracks = db.scalars(
        _track_query().join(Album, Album.id == Track.album_id).where(Track.id.in_(ids))
        .order_by(*_order(spec, reverse)).offset(offset).limit(limit)
    ).unique().all()
    return Page(items=[track_out(t) for t in tracks], total=total, offset=offset, limit=limit)


def _custom_tracks(db, user_id: int, ids, offset: int, limit: int, reverse: bool) -> list[TrackOut]:
    """Własna kolejność użytkownika: najpierw utwory z jego listy (w jej kolejności), potem reszta po tytule."""
    row = db.scalar(select(UserOrder).where(UserOrder.user_id == user_id, UserOrder.kind == "track"))
    pos = {tid: i for i, tid in enumerate(json.loads(row.ids))} if row else {}
    all_ids = list(db.scalars(select(Track.id).where(Track.id.in_(ids)).order_by(func.lower(Track.title), Track.id)))
    big = len(pos)
    all_ids.sort(key=lambda tid: pos.get(tid, big))  # stabilne – reszta zostaje po tytule
    if reverse:
        all_ids.reverse()
    page = all_ids[offset:offset + limit]
    found = {t.id: t for t in db.scalars(_track_query().where(Track.id.in_(page))).unique()}
    return [track_out(found[i]) for i in page if i in found]


@router.get("/tracks/{track_id}", response_model=TrackOut)
def get_track(track_id: int, _: CurrentUser, db: DB):
    track = db.scalar(_track_query().where(Track.id == track_id))
    if track is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "track.not_found")
    return track_out(track)


# ---------------------------------------------------------------- gatunki

def genre_list(db, limit: int | None = None, catalog: bool = False) -> list[GenreOut]:
    """Gatunki z liczbą utworów i albumów. [catalog] – także nieużywane gatunki z katalogu Nati."""
    tracks = func.count(Track.id.distinct())
    stmt = (select(Genre, tracks, func.count(Track.album_id.distinct()))
            .outerjoin(track_genres, track_genres.c.genre_id == Genre.id)
            .outerjoin(Track, Track.id == track_genres.c.track_id)
            .group_by(Genre.id)
            .order_by(tracks.desc(), func.lower(Genre.name)))
    if catalog:
        stmt = stmt.having((tracks > 0) | Genre.hidden.is_not(True))
    else:
        stmt = stmt.having(tracks > 0)
    if limit:
        stmt = stmt.limit(limit)
    return [genre_out(g, t, a) for g, t, a in db.execute(stmt)]


def genre_out(g: Genre, track_count: int = 0, album_count: int = 0) -> GenreOut:
    return GenreOut(id=g.id, name=g.name, track_count=track_count, album_count=album_count, custom=bool(g.custom),
                    icon=g.icon, color1=g.color1, color2=g.color2, image_id=cover_id(g.image))


def _one_genre(db, genre_id: int) -> GenreOut:
    found = next((x for x in genre_list(db, catalog=True) if x.id == genre_id), None)
    if found is None:
        g = db.get(Genre, genre_id)
        if g is None:
            raise AppError(status.HTTP_404_NOT_FOUND, "genre.not_found")
        return genre_out(g)
    return found


@router.get("/genres", response_model=list[GenreOut])
def list_genres(_: CurrentUser, db: DB, catalog: bool = False):
    """Gatunki z biblioteki (najpopularniejsze najpierw) – do filtrów.
    `catalog=true` – cały katalog Nati (też nieużywane), do wyboru gatunku utworu."""
    return genre_list(db, catalog=catalog)


class GenreCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.post("/genres", response_model=GenreOut, status_code=status.HTTP_201_CREATED)
def create_genre(body: GenreCreate, _: CuratorUser, db: DB):
    """Nowy gatunek w katalogu (np. „Muzyka filmowa PL”). Ten sam drugi raz – zwraca istniejący."""
    g = genre_catalog.add(db, body.name)
    return _one_genre(db, g.id)


class GenreEdit(BaseModel):
    """Pominięte pola zostają bez zmian; pusty tekst przywraca domyślny wygląd."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=64)
    color1: str | None = Field(default=None, pattern=r"^(#[0-9a-fA-F]{6})?$")
    color2: str | None = Field(default=None, pattern=r"^(#[0-9a-fA-F]{6})?$")


@router.patch("/genres/{genre_id}", response_model=GenreOut)
async def edit_genre(genre_id: int, body: GenreEdit, request: Request, _: CuratorUser, db: DB):
    """Wygląd kafla gatunku i zmiana nazwy (nowa nazwa trafia do tagów wszystkich jego utworów;
    nazwa innego istniejącego gatunku – łączy je w jeden)."""
    g = db.get(Genre, genre_id)
    if g is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "genre.not_found")
    for f in ("icon", "color1", "color2"):
        if f in body.model_fields_set:
            setattr(g, f, getattr(body, f) or None)
    db.commit()
    if body.name is not None and body.name.strip() != g.name:
        editor = request.app.state.editor
        try:
            genre_id = await run_in_threadpool(editor.replace_genre, genre_id, body.name)
        except CodedError as e:  # EditError, biblioteka zajęta (skan)
            raise AppError.of(e) from e
    db.expire_all()
    return _one_genre(db, genre_id)


@router.delete("/genres/{genre_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_genre(genre_id: int, request: Request, _: CuratorUser):
    """Usuwa gatunek z bazy i z tagów wszystkich utworów, które go mają (utwory zostają)."""
    try:
        await run_in_threadpool(request.app.state.editor.replace_genre, genre_id, None)
    except CodedError as e:  # EditError, biblioteka zajęta (skan)
        raise AppError.of(e) from e


# ---------------------------------------------------------------- podpowiedzi gatunków

class GenreSuggestions(BaseModel):
    library: list[str]  # z tej samej biblioteki (album, wykonawca)
    online: list[str]  # z internetu (iTunes – album, MusicBrainz – wykonawca)


def _suggest(request: Request, db, album: Album) -> GenreSuggestions:
    library = _top(_genre_counts(db, Track.album_id, [album.id]), album.id, 5)
    library += [g for g in _top(_genre_counts(db, Album.artist_id, [album.artist_id]), album.artist_id, 5)
                if g not in library]
    online: list[str] = []
    enricher = getattr(request.app.state, "enricher", None)
    if enricher is not None and enricher.enabled:
        import httpx
        try:
            with httpx.Client(timeout=10, headers={"User-Agent": USER_AGENT}) as client:
                if g := enricher._itunes_genre(client, album.artist.name, album.title):
                    online.append(g)
                for g in enricher._mb_genres(client, album.artist.name):
                    if g not in online:
                        online.append(g)
        except Exception:
            pass  # bez internetu – same podpowiedzi z biblioteki
    return GenreSuggestions(library=library, online=[g for g in online if g not in library])


@router.get("/tracks/{track_id}/genre-suggestions", response_model=GenreSuggestions)
def track_genre_suggestions(track_id: int, request: Request, _: CurrentUser, db: DB):
    track = db.get(Track, track_id)
    if track is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "track.not_found")
    return _suggest(request, db, track.album)


@router.get("/albums/{album_id}/genre-suggestions", response_model=GenreSuggestions)
def album_genre_suggestions(album_id: int, request: Request, _: CurrentUser, db: DB):
    album = db.get(Album, album_id)
    if album is None:
        raise AppError(status.HTTP_404_NOT_FOUND, "album.not_found")
    return _suggest(request, db, album)


# ---------------------------------------------------------------- skan

@router.get("/library/scan")
def scan_status(request: Request, _: CurrentUser):
    return request.app.state.scanner.status.as_dict()


@router.post("/library/scan", status_code=status.HTTP_202_ACCEPTED)
def start_scan(request: Request, _: AdminUser):
    scanner = request.app.state.scanner
    started = scanner.start_background()
    return {"started": started, **scanner.status.as_dict()}


# ---------------------------------------------------------------- uzupełnianie z internetu

@router.get("/library/enrich")
def enrich_status(request: Request, _: CurrentUser):
    """Stan uzupełniania gatunków i zdjęć wykonawców z internetu."""
    return request.app.state.enricher.as_dict()


@router.post("/library/enrich", status_code=status.HTTP_202_ACCEPTED)
def enrich_again(request: Request, _: AdminUser):
    """Sprawdź jeszcze raz wszystkich wykonawców (np. po dodaniu dużo muzyki)."""
    enricher = request.app.state.enricher
    if not enricher.enabled:
        raise AppError(status.HTTP_409_CONFLICT, "enrich.disabled")
    enricher.request(again=True)
    return enricher.as_dict()
