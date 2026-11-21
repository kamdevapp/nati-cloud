"""Uzupełnianie tagów z MusicBrainz – dla utworów pobranych z linków (zwykły YouTube,
SoundCloud…), gdzie strona podaje tylko tytuł filmu i nazwę kanału.

Bierzemy wynik tylko, gdy wyraźnie pasuje (wysoki wynik, zgodny wykonawca i tytuł) –
lepiej zostawić tagi ze strony niż wpisać zły album."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

import httpx

from .. import __version__
from ..text import normalize

log = logging.getLogger(__name__)

API = "https://musicbrainz.org/ws/2/recording"
# MusicBrainz wymaga opisowego User-Agenta i najwyżej 1 zapytania na sekundę.
USER_AGENT = f"Nati/{__version__} ( self-hosted music server )"

_JUNK = re.compile(
    r"[\(\[](?:[^\)\]]*?)(official|oficjalny|teledysk|video|audio|lyric|lyrics|tekst|visualizer|hd|4k|"
    r"remaster(?:ed)?|music video|clip)(?:[^\)\]]*?)[\)\]]",
    re.IGNORECASE,
)
_SEPARATORS = (" - ", " – ", " — ", " | ")

_lock = asyncio.Lock()
_last_call = 0.0


@dataclass
class Match:
    title: str
    artist: str
    album: str | None
    album_artist: str | None
    year: int | None
    track_no: int | None
    cover_url: str | None
    genres: list[str] = field(default_factory=list)


def clean_title(raw: str) -> str:
    t = _JUNK.sub("", raw)
    t = re.sub(r"\s+", " ", t).strip(" -–—|")
    return t or raw


def split_artist_title(video_title: str) -> tuple[str, str] | None:
    """„Wykonawca - Tytuł (Official Video)” → ("Wykonawca", "Tytuł")."""
    title = clean_title(video_title)
    for sep in _SEPARATORS:
        if sep in title:
            artist, _, song = title.partition(sep)
            if artist.strip() and song.strip():
                return artist.strip(), song.strip()
    return None


def guess(video_title: str, uploader: str | None) -> list[tuple[str, str]]:
    """Możliwe pary (wykonawca, tytuł) z tytułu filmu."""
    title = clean_title(video_title)
    out = []
    if pair := split_artist_title(video_title):
        out.append(pair)
    if uploader:
        channel = re.sub(r"\s*(-\s*Topic|VEVO|Official)$", "", uploader, flags=re.IGNORECASE).strip()
        out.append((channel, title))
    return [(a, t) for a, t in out if a and t]


def _similar(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na and nb) and (na == nb or na in nb or nb in na)


def _year(date: str | None) -> int | None:
    return int(date[:4]) if date and date[:4].isdigit() else None


def _credit(credits: list[dict] | None) -> str:
    return "".join(c.get("name", "") + c.get("joinphrase", "") for c in credits or [])


def _rank(rec: dict, rel: dict | None):
    """Mniejsze = lepsze: oficjalny album studyjny, bez „live”/kompilacji, najwcześniejszy."""
    if rel is None:
        return (True, 9, True, True, rec.get("first-release-date") or "9999")
    rg = rel.get("release-group") or {}
    return (
        rel.get("status") != "Official",
        {"Album": 0, "EP": 1, "Single": 2}.get(rg.get("primary-type"), 3),
        bool(rg.get("secondary-types")) or bool(rec.get("disambiguation")),  # koncertowe, kompilacje, wersje
        False,
        rel.get("date") or "9999",
    )


def _track_no(rel: dict) -> int | None:
    for medium in rel.get("media") or []:
        for t in medium.get("track") or []:
            if str(t.get("number", "")).isdigit():
                return int(t["number"])
    return None


def _parse(data: dict, artist: str, title: str) -> Match | None:
    candidates = []
    for rec in data.get("recordings") or []:
        if (rec.get("score") or 0) < 90:
            continue
        credit = _credit(rec.get("artist-credit"))
        if not _similar(credit, artist) or not _similar(rec.get("title", ""), title):
            continue
        for rel in rec.get("releases") or [None]:
            candidates.append((_rank(rec, rel), rec, rel))
    if not candidates:
        return None
    _, rec, rel = min(candidates, key=lambda c: c[0])
    credit = _credit(rec.get("artist-credit"))
    year = _year(rec.get("first-release-date"))
    genres = _genres(rec)
    if rel is None:
        return Match(rec["title"], credit, None, None, year, None, None, genres)
    rg = (rel.get("release-group") or {}).get("id")
    return Match(
        title=rec["title"],
        artist=credit,
        album=rel.get("title"),
        album_artist=_credit(rel.get("artist-credit")) or credit,
        year=year or _year(rel.get("date")),
        track_no=_track_no(rel),
        cover_url=f"https://coverartarchive.org/release-group/{rg}/front-500" if rg else None,
        genres=genres,
    )


# Tagi MusicBrainz to też np. „polish”, „seen live” – bierzemy tylko znane gatunki.
_GENRE_WORDS = {
    "rock", "pop", "metal", "jazz", "blues", "hip hop", "rap", "electronic", "electro", "house", "techno", "trance",
    "dance", "disco", "funk", "soul", "r&b", "reggae", "punk", "folk", "country", "classical", "ambient", "indie",
    "alternative", "alternative rock", "indie rock", "indie pop", "synthpop", "synth-pop", "hard rock", "heavy metal",
    "punk rock", "pop rock", "edm", "drum and bass", "dubstep", "trap", "lo-fi", "soundtrack", "singer-songwriter",
    "acoustic", "chillout", "downtempo", "grunge", "progressive rock", "psychedelic rock", "new wave", "post-punk",
    "k-pop", "latin", "gospel", "opera", "swing", "ska", "emo", "hardcore", "garage", "experimental", "world",
    "poezja śpiewana", "disco polo", "shanty",
}


def _genres(rec: dict) -> list[str]:
    tags = sorted(rec.get("tags") or [], key=lambda t: -(t.get("count") or 0))
    out = []
    for t in tags:
        name = (t.get("name") or "").strip().lower()
        if name in _GENRE_WORDS and len(out) < 2:
            out.append(name.upper() if name in ("edm", "r&b") else name.title().replace("Hip Hop", "Hip-Hop"))
    return out


async def _query(client: httpx.AsyncClient, artist: str, title: str) -> dict:
    global _last_call
    q = f'recording:"{_esc(title)}" AND artist:"{_esc(artist)}"'
    async with _lock:
        wait = 1.1 - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        for attempt in range(3):
            _last_call = time.monotonic()
            r = await client.get(API, params={"query": q, "fmt": "json", "limit": 10})
            if r.status_code != 503:  # 503 = „za szybko” – czekamy i ponawiamy
                break
            await asyncio.sleep(2 * (attempt + 1))
    r.raise_for_status()
    return r.json()


def _esc(s: str) -> str:
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", s)


async def lookup(video_title: str, uploader: str | None) -> Match | None:
    """Szuka utworu w MusicBrainz. None – brak pewnego dopasowania albo brak sieci."""
    return await lookup_pairs(guess(video_title, uploader))


async def lookup_pairs(pairs: list[tuple[str, str]]) -> Match | None:
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
            for artist, title in pairs:
                match = _parse(await _query(client, artist, title), artist, title)
                if match:
                    return match
    except (httpx.HTTPError, ValueError) as e:
        log.info("MusicBrainz: %s", e)
    return None
