"""Wyszukiwanie poza YouTube Music: zwykły YouTube i SoundCloud (przez yt-dlp) oraz Bandcamp
(jego publiczna wyszukiwarka). Wyniki mają ten sam kształt co z YouTube Music, plus pole „source”.

Identyfikator utworu: dla YouTube – videoId (odsłuch jak z YouTube Music), dla pozostałych – adres strony.
Pobieranie takich utworów idzie jak „link” (tagi z serwisu + MusicBrainz)."""

from __future__ import annotations

import html
import json
import re

import httpx

from .ytdlp import YtDlp
from ..errors import CodedError
from ..text import UNKNOWN_ARTIST

SOURCES = ("ytmusic", "youtube", "soundcloud", "bandcamp")
# Czego da się szukać w danym serwisie.
KINDS = {
    "ytmusic": ("all", "songs", "albums", "artists"),
    "youtube": ("all", "songs"),
    "soundcloud": ("all", "songs"),
    "bandcamp": ("all", "songs", "albums"),
}

_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
_SPLIT = re.compile(r"\s+[-–—]\s+")
_NOISE = re.compile(r"\s*[\(\[][^)\]]*(official|video|audio|lyric|teledysk|hd|4k|mv|visuali[sz]er)[^)\]]*[\)\]]",
                    re.IGNORECASE)


def split_title(title: str, uploader: str | None) -> tuple[str, str]:
    """„Wykonawca - Tytuł (Official Video)” → (wykonawca, tytuł)."""
    clean = _NOISE.sub("", title).strip() or title
    parts = _SPLIT.split(clean, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    return (uploader or UNKNOWN_ARTIST).removesuffix(" - Topic").strip(), clean


def _best_thumb(entry: dict) -> str | None:
    thumbs = [t for t in entry.get("thumbnails") or [] if t.get("url")]
    if not thumbs:
        return entry.get("thumbnail")
    # SoundCloud: rozmiar „t500x500” zamiast miniaturki 16×16.
    url = max(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))["url"]
    return re.sub(r"-(mini|tiny|small|badge|t67x67|large|t300x300|crop)\.(jpg|png)$", r"-t500x500.\2", url)


async def search_youtube(ytdlp: YtDlp, query: str, limit: int = 20) -> list[dict]:
    out = []
    for e in await ytdlp.search(f"ytsearch{limit}:{query}"):
        if not e.get("id") or e.get("live_status") == "is_live":
            continue
        artist, title = split_title(e.get("title") or "?", e.get("channel") or e.get("uploader"))
        out.append({"id": e["id"], "title": title, "artist": artist, "duration": e.get("duration") or 0,
                    "thumbnail": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg", "source": "youtube",
                    "url": f"https://www.youtube.com/watch?v={e['id']}"})
    return out


async def search_soundcloud(ytdlp: YtDlp, query: str, limit: int = 20) -> list[dict]:
    out = []
    for e in await ytdlp.search(f"scsearch{limit}:{query}"):
        url = e.get("webpage_url") or e.get("url")
        if not url or not url.startswith("https://soundcloud.com/"):
            continue
        artist, title = split_title(e.get("title") or "?", e.get("uploader"))
        out.append({"id": url, "title": title, "artist": artist, "duration": e.get("duration") or 0,
                    "thumbnail": _best_thumb(e), "source": "soundcloud", "url": url})
    return out


def _bc_image(url: str | None, size: int = 16) -> str | None:
    # Bandcamp: _3 = 100 px, _16 = 700 px, _10 = 1200 px.
    return re.sub(r"_\d+\.jpg$", f"_{size}.jpg", url) if url else None


def search_bandcamp(query: str, kind: str = "all", limit: int = 20) -> dict:
    def ask(flt: str) -> list[dict]:
        r = httpx.post("https://bandcamp.com/api/bcsearch_public_api/1/autocomplete_elastic", headers=_UA,
                       timeout=15, json={"search_text": query, "search_filter": flt, "full_page": False,
                                         "fan_id": None})
        r.raise_for_status()
        return (r.json().get("auto") or {}).get("results") or []

    tracks, albums = [], []
    if kind in ("all", "songs"):
        for t in ask("t")[: limit if kind == "songs" else 10]:
            if url := t.get("item_url_path"):
                tracks.append({"id": url, "title": t.get("name") or "?", "artist": t.get("band_name") or "?",
                               "album": t.get("album_name"), "duration": 0, "thumbnail": _bc_image(t.get("img")),
                               "source": "bandcamp", "url": url})
    if kind in ("all", "albums"):
        for a in ask("a")[: limit if kind == "albums" else 8]:
            if url := a.get("item_url_path"):
                albums.append({"id": url, "title": a.get("name") or "?", "artist": a.get("band_name") or "?",
                               "kind": "Album", "thumbnail": _bc_image(a.get("img")), "source": "bandcamp"})
    return {"tracks": tracks, "albums": albums, "artists": []}


def bandcamp_album(url: str) -> dict:
    """Album z Bandcampa: lista utworów z danych na stronie albumu (szybko, bez yt-dlp)."""
    r = httpx.get(url, headers=_UA, timeout=20, follow_redirects=True)
    r.raise_for_status()
    m = re.search(r'data-tralbum="([^"]+)"', r.text)
    if not m:
        raise CodedError("bandcamp.not_album")
    data = json.loads(html.unescape(m[1]))
    root = re.match(r"https?://[^/]+", str(r.url))[0]
    artist = data.get("artist") or "?"
    title = (data.get("current") or {}).get("title") or "?"
    art = data.get("art_id")
    cover = f"https://f4.bcbits.com/img/a{art}_10.jpg" if art else None
    year = re.search(r"\b(19|20)\d\d\b", data.get("album_release_date") or "")
    tracks = []
    for i, t in enumerate(data.get("trackinfo") or []):
        if not t.get("title_link") or not t.get("streaming", 1):
            continue
        link = root + t["title_link"]
        tracks.append({"id": link, "title": t.get("title") or "?", "artist": t.get("artist") or artist,
                       "album": title, "album_id": url, "track_no": t.get("track_num") or i + 1,
                       "duration": t.get("duration") or 0,
                       "thumbnail": _bc_image(cover, 16), "source": "bandcamp", "url": link})
    return {"id": url, "title": title, "artist": artist, "year": int(year[0]) if year else None, "kind": "Album",
            "thumbnail": cover, "source": "bandcamp", "tracks": tracks}


def is_url(item_id: str) -> bool:
    return item_id.startswith(("http://", "https://"))
