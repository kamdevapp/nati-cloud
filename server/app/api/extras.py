"""Dodatki: import playlist ze Spotify i podpowiedzi („Mix dla Ciebie”, dawno niesłuchane, podobne z internetu)."""

from __future__ import annotations

import logging
import random
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from ..errors import AppError
from .. import db as database
from ..db.models import Album, DownloadJob, Favorite, PlayHistory, Playlist, PlaylistItem, Track
from ..schemas import AlbumOut, PlaylistOut, TrackOut
from ..security import DB, CurrentUser
from ..services import server_settings
from ..services import online_search, spotify
from ..services.downloader import ACTIVE
from .convert import track_out
from .online import DiscoverOut, OnlineTrack
from .playlists import playlist_out

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["extras"])


# ================================================================ Spotify

class SpotifyRequest(BaseModel):
    url: str = Field(min_length=10, max_length=512)
    # Brakujące utwory poszukać w YouTube Music i pobrać do chmury (administrator / kurator).
    download_missing: bool = False
    name: str | None = Field(default=None, max_length=256)


class SpotifyTrack(BaseModel):
    title: str
    artist: str
    duration: float = 0
    track_id: int | None = None  # utwór z biblioteki, jeśli jest


class SpotifyPreview(BaseModel):
    kind: str
    name: str
    owner: str | None = None
    image: str | None = None
    tracks: list[SpotifyTrack]
    matched: int


class SpotifyResult(BaseModel):
    playlist: PlaylistOut
    matched: int
    missing: int
    queued: int


def _library_index(db) -> dict[tuple[str, str], int]:
    """(tytuł, wykonawca) → id utworu, dla całej biblioteki."""
    index: dict[tuple[str, str], int] = {}
    for tid, title, artist in db.execute(select(Track.id, Track.title, Track.artist_name)):
        for key in spotify.match_key(title, artist):
            index.setdefault(key, tid)
    return index


def _match(index, title: str, artist: str) -> int | None:
    for key in spotify.match_key(title, artist):
        if key in index:
            return index[key]
    return None


def _load(db, url: str) -> SpotifyPreview:
    try:
        data = spotify.fetch(url)
    except spotify.SpotifyError as e:
        raise AppError.of(e) from e
    index = _library_index(db)
    tracks = [SpotifyTrack(**t, track_id=_match(index, t["title"], t["artist"])) for t in data["tracks"]]
    return SpotifyPreview(kind=data["kind"], name=data["name"], owner=data["owner"], image=data["image"],
                          tracks=tracks, matched=sum(1 for t in tracks if t.track_id))


@router.post("/import/spotify/preview", response_model=SpotifyPreview)
def spotify_preview(body: SpotifyRequest, _: CurrentUser, db: DB):
    """Co jest na playliście / albumie Spotify i które utwory są już w bibliotece."""
    return _load(db, body.url)


@router.post("/import/spotify", response_model=SpotifyResult, status_code=status.HTTP_201_CREATED)
def spotify_import(body: SpotifyRequest, user: CurrentUser, db: DB, request: Request, background: BackgroundTasks):
    """Tworzy playlistę z utworów, które są w bibliotece. Z [download_missing] brakujące są wyszukiwane
    w YouTube Music, pobierane do chmury i dopisywane do playlisty, gdy tylko się pobiorą."""
    data = _load(db, body.url)
    if not data.tracks:
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "spotify.empty_list")
    pl = Playlist(name=(body.name or data.name).strip() or "Ze Spotify", owner_id=user.id)
    seen: set[int] = set()
    for t in data.tracks:
        if t.track_id and t.track_id not in seen:
            seen.add(t.track_id)
            pl.items.append(PlaylistItem(track_id=t.track_id, position=len(pl.items)))
    db.add(pl)
    db.commit()
    missing = [t for t in data.tracks if not t.track_id]
    queued = 0
    if body.download_missing and missing:
        if not user.can_curate or not server_settings.online_enabled():
            raise AppError(status.HTTP_403_FORBIDDEN, "online.curator_only")
        queued = len(missing)
        background.add_task(_queue_missing, request.app.state.downloads, user.id, pl.id,
                            [(t.title, t.artist) for t in missing])
    return SpotifyResult(playlist=playlist_out(db, pl, user), matched=len(seen), missing=len(missing), queued=queued)


def _queue_missing(manager, user_id: int, playlist_id: int, wanted: list[tuple[str, str]]) -> None:
    """W tle: szuka brakujących utworów w YouTube Music i wstawia je do kolejki pobierania."""
    for title, artist in wanted:
        try:
            found = online_search.search(f"{artist} {title}", limit=5)["tracks"]
        except Exception as e:
            log.warning("Spotify: nie znaleziono „%s – %s”: %s", artist, title, e)
            continue
        want = spotify.title_key(title)
        hit = next((f for f in found if spotify.title_key(f["title"]) == want), found[0] if found else None)
        if hit is None:
            continue
        with database.new_session() as db:
            done = db.scalar(select(DownloadJob).where(DownloadJob.source_id == hit["id"], DownloadJob.status == "done",
                                                       DownloadJob.track_id.is_not(None)))
            if done is not None and db.get(Track, done.track_id):
                append_to_playlist(db, playlist_id, done.track_id)
                db.commit()
                continue
            active = db.scalar(select(DownloadJob).where(DownloadJob.source_id == hit["id"],
                                                         DownloadJob.status.in_(ACTIVE)))
            if active is not None:
                active.playlist_id = playlist_id
            else:
                db.add(DownloadJob(user_id=user_id, source="ytmusic", source_id=hit["id"], title=hit["title"],
                                   artist=hit["artist"], album=hit.get("album"), album_artist=hit["artist"],
                                   thumbnail=hit.get("thumbnail"), playlist_id=playlist_id))
            db.commit()
        manager.notify()


def append_to_playlist(db, playlist_id: int, track_id: int) -> None:
    pl = db.get(Playlist, playlist_id)
    if pl is None or any(i.track_id == track_id for i in pl.items):
        return
    last = max((i.position for i in pl.items), default=-1)
    pl.items.append(PlaylistItem(track_id=track_id, position=last + 1))


# ================================================================ podpowiedzi

class Recommendations(BaseModel):
    # „Mix dla Ciebie” – utwory z biblioteki dobrane do tego, czego słuchasz.
    mix: list[TrackOut]
    # Albumy, których dawno nie słuchałeś, a kiedyś lubiłeś.
    forgotten: list[AlbumOut]
    # Czy mix opiera się na historii (inaczej: losowy wybór z biblioteki).
    personal: bool


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def build_mix(tracks: list[Track], plays: list[tuple[int, datetime]], favorites: set[int], *, size: int = 30,
              now: datetime | None = None, rng: random.Random | None = None) -> tuple[list[Track], bool]:
    """Dobiera utwory: ulubieni wykonawcy i gatunki wyżej, to co grało w ostatnich dniach – niżej,
    najwyżej kilka utworów jednego wykonawcy. Bez historii – losowy przekrój biblioteki."""
    rng = rng or random.Random()
    now = now or datetime.now(timezone.utc)
    by_id = {t.id: t for t in tracks}
    artist_score: Counter[str] = Counter()
    genre_score: Counter[str] = Counter()
    recent: set[int] = set()

    def credit(track: Track, weight: float) -> None:
        artist_score[track.album.artist.name] += weight
        for g in track.genres:
            genre_score[g.name] += weight

    for track_id, at in plays:
        t = by_id.get(track_id)
        if t is None:
            continue
        age = (now - _aware(at)).days
        credit(t, 1.0 if age <= 30 else 0.5 if age <= 120 else 0.2)
        if age <= 2:
            recent.add(track_id)
    for track_id in favorites:
        if (t := by_id.get(track_id)) is not None:
            credit(t, 3.0)

    personal = bool(artist_score)
    top_artist = max(artist_score.values(), default=1.0)
    top_genre = max(genre_score.values(), default=1.0)

    def score(t: Track) -> float:
        s = rng.random()  # zawsze trochę przypadku – mix za każdym razem inny
        if personal:
            s += 2.0 * artist_score[t.album.artist.name] / top_artist
            s += 1.0 * max((genre_score[g.name] for g in t.genres), default=0.0) / top_genre
            if t.id in recent:
                s -= 1.5
        return s

    per_artist: defaultdict[str, int] = defaultdict(int)
    mix: list[Track] = []
    for t in sorted(tracks, key=score, reverse=True):
        name = t.album.artist.name
        if per_artist[name] >= 4:
            continue
        per_artist[name] += 1
        mix.append(t)
        if len(mix) >= size:
            break
    rng.shuffle(mix)
    return mix, personal


@router.get("/recommendations", response_model=Recommendations)
def recommendations(user: CurrentUser, db: DB):
    from .library import albums_with_counts

    tracks = list(db.scalars(select(Track).options(joinedload(Track.album).joinedload(Album.artist))).unique())
    plays = [(r.track_id, r.played_at) for r in db.execute(
        select(PlayHistory.track_id, PlayHistory.played_at).where(PlayHistory.user_id == user.id)
        .order_by(PlayHistory.played_at.desc()).limit(3000))]
    favorites = set(db.scalars(select(Favorite.track_id).where(Favorite.user_id == user.id)))
    mix, personal = build_mix(tracks, plays, favorites)

    # Dawno niesłuchane: albumy grane co najmniej 2 razy, ostatnio ponad 3 tygodnie temu.
    cutoff = datetime.now(timezone.utc) - timedelta(days=21)
    rows = db.execute(
        select(Track.album_id, func.count(PlayHistory.id).label("n"), func.max(PlayHistory.played_at).label("last"))
        .join(Track, Track.id == PlayHistory.track_id).where(PlayHistory.user_id == user.id)
        .group_by(Track.album_id).having(func.count(PlayHistory.id) >= 2)
        .order_by(func.count(PlayHistory.id).desc())
    ).all()
    order = [r.album_id for r in rows if _aware(r.last) < cutoff][:12]
    found = {a.id: a for a in albums_with_counts(db, select(Album).where(Album.id.in_(order)))} if order else {}
    return Recommendations(mix=[track_out(t) for t in mix], forgotten=[found[i] for i in order if i in found],
                           personal=personal)


# ---------------------------------------------------------------- podobne z internetu

_similar_cache: dict[int, tuple[float, list[dict]]] = {}
SIMILAR_TTL = 6 * 3600


def _seeds(db, user_id: int, limit: int = 3) -> list[Track]:
    """Utwory, od których zaczynamy szukanie podobnych: ostatnio ulubione i najczęściej grane."""
    ids = list(db.scalars(select(Favorite.track_id).where(Favorite.user_id == user_id)
                          .order_by(Favorite.created_at.desc()).limit(limit)))
    since = datetime.now(timezone.utc) - timedelta(days=60)
    ids += [r[0] for r in db.execute(
        select(PlayHistory.track_id, func.count(PlayHistory.id)).where(
            PlayHistory.user_id == user_id, PlayHistory.played_at >= since)
        .group_by(PlayHistory.track_id).order_by(func.count(PlayHistory.id).desc()).limit(limit))]
    unique = list(dict.fromkeys(ids))
    random.shuffle(unique)
    return [t for i in unique[:limit] if (t := db.get(Track, i)) is not None]


def _similar_online(seeds: list[tuple[str, str]], have: set[tuple[str, str]], limit: int = 24) -> list[dict]:
    out: dict[str, dict] = {}
    for artist, title in seeds:
        try:
            found = online_search.search(f"{artist} {title}", limit=3)["tracks"]
            if not found:
                continue
            for t in online_search.related(found[0]["id"]):
                keys = spotify.match_key(t["title"], t["artist"])
                if t["id"] not in out and not any(k in have for k in keys):
                    out[t["id"]] = t
        except Exception as e:  # jedna nieudana próba nie psuje reszty
            log.warning("Podobne z internetu: %s", e)
    items = list(out.values())
    random.shuffle(items)
    return items[:limit]


@router.get("/online/recommendations", response_model=list[OnlineTrack])
async def online_recommendations(user: CurrentUser, db: DB, refresh: bool = False):
    """Utwory z YouTube Music podobne do tego, czego słuchasz – takie, których nie ma w bibliotece."""
    if not server_settings.online_enabled():
        return []
    hit = _similar_cache.get(user.id)
    if hit and not refresh and time.time() - hit[0] < SIMILAR_TTL:
        return hit[1]
    seeds = [(t.artist_name.split(",")[0].strip(), t.title) for t in _seeds(db, user.id)]
    if not seeds:
        return []
    have = set(_library_index(db))
    items = await run_in_threadpool(_similar_online, seeds, have)
    if items:
        _similar_cache[user.id] = (time.time(), items)
    return items


# ---------------------------------------------------------------- polecane w „Dodaj do chmury”

_discover_cache: dict[int, tuple[float, dict]] = {}


def _top_artists(db, user_id: int, limit: int = 2) -> list[str]:
    """Najczęściej słuchani wykonawcy (ostatnie 90 dni) i wykonawcy ulubionych utworów."""
    since = datetime.now(timezone.utc) - timedelta(days=90)
    names = [r[0] for r in db.execute(
        select(Track.artist_name, func.count(PlayHistory.id)).join(PlayHistory, PlayHistory.track_id == Track.id)
        .where(PlayHistory.user_id == user_id, PlayHistory.played_at >= since)
        .group_by(Track.artist_name).order_by(func.count(PlayHistory.id).desc()).limit(6))]
    names += list(db.scalars(select(Track.artist_name).join(Favorite, Favorite.track_id == Track.id)
                             .where(Favorite.user_id == user_id).order_by(Favorite.created_at.desc()).limit(6)))
    unique = list(dict.fromkeys(n.split(",")[0].strip() for n in names if n and n.strip()))
    random.shuffle(unique)
    return unique[:limit]


def _album_index(db) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for album in db.scalars(select(Album).options(joinedload(Album.artist))):
        keys.update(spotify.match_key(album.title, album.artist.name))
    return keys


def _discover(seeds, artists: list[str], have: set, have_albums: set, limit: int = 18) -> dict:
    tracks = _similar_online(seeds, have) if seeds else []
    albums: dict[str, dict] = {}

    def add(a: dict):
        if a["id"] not in albums and not any(k in have_albums for k in spotify.match_key(a["title"], a["artist"])):
            albums[a["id"]] = a

    for t in tracks:
        if t.get("album_id") and t.get("album"):
            add({"id": t["album_id"], "title": t["album"], "artist": t["artist"], "kind": "Album",
                 "thumbnail": t.get("thumbnail")})
    for name in artists:
        try:
            if browse_id := online_search.find_artist(name):
                page = online_search.artist(browse_id)
                for a in page["albums"][:8]:
                    add(a)
        except Exception as e:
            log.warning("Polecane albumy (%s): %s", name, e)
    items = list(albums.values())
    random.shuffle(items)
    return {"tracks": tracks, "albums": items[:limit]}


@router.get("/online/discover", response_model=DiscoverOut)
async def online_discover(user: CurrentUser, db: DB, refresh: bool = False):
    """Polecane z internetu (ekran „Dodaj do chmury”): utwory podobne do tego, czego słuchasz,
    i albumy – z tych utworów oraz od najczęściej słuchanych wykonawców. Bez tego, co już jest w chmurze."""
    empty = {"tracks": [], "albums": []}
    if not server_settings.online_enabled() or not user.can_curate:
        return empty
    hit = _discover_cache.get(user.id)
    if hit and not refresh and time.time() - hit[0] < SIMILAR_TTL:
        return hit[1]
    seeds = [(t.artist_name.split(",")[0].strip(), t.title) for t in _seeds(db, user.id)]
    artists = _top_artists(db, user.id)
    if not seeds and not artists:
        return empty
    out = await run_in_threadpool(_discover, seeds, artists, set(_library_index(db)), _album_index(db))
    if out["tracks"] or out["albums"]:
        _discover_cache[user.id] = (time.time(), out)
    return out
