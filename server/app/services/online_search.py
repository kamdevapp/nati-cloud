"""Wyszukiwanie w YouTube Music (ytmusicapi). Wyniki są „czyste” – tytuł, wykonawca,
album, okładka – w przeciwieństwie do zwykłego YouTube."""

from __future__ import annotations

import time

import re
from functools import lru_cache

from ytmusicapi import YTMusic
from ..text import UNKNOWN_ARTIST


@lru_cache(maxsize=1)
def _client() -> YTMusic:
    return YTMusic(location="PL")


def big_thumbnail(thumbnails: list[dict] | None, size: int = 1200) -> str | None:
    """Największa okładka; adresy Google'a pozwalają poprosić o dowolny rozmiar."""
    if not thumbnails:
        return None
    url = max(thumbnails, key=lambda t: t.get("width", 0))["url"]
    return re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url)


def _artists(item: dict) -> str:
    return ", ".join(a["name"] for a in item.get("artists") or [] if a.get("name")) or UNKNOWN_ARTIST


def _year(v) -> int | None:
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return None


# Ile wyników na rodzaj: „wszystko” (sekcje) i pojedynczy filtr (dłuższa lista).
LIMITS_ALL = {"songs": 20, "albums": 8, "artists": 6}
LIMITS_ONE = {"songs": 50, "albums": 40, "artists": 30}


def _song(s: dict) -> dict:
    return {
        "id": s["videoId"],
        "title": s.get("title") or "?",
        "artist": _artists(s),
        "album": (s.get("album") or {}).get("name"),
        "album_id": (s.get("album") or {}).get("id"),
        "duration": s.get("duration_seconds") or _seconds(s.get("duration")),
        "thumbnail": big_thumbnail(s.get("thumbnails"), 400),
    }


def _album_item(a: dict, artist: str | None = None) -> dict:
    return {
        "id": a["browseId"],
        "title": a.get("title") or "?",
        "artist": _artists(a) if a.get("artists") else (artist or _artists(a)),
        "year": _year(a.get("year")),
        "kind": a.get("type") or "Album",
        "thumbnail": big_thumbnail(a.get("thumbnails"), 400),
    }


def _artist_item(a: dict) -> dict:
    return {
        "id": a["browseId"],
        "name": a.get("artist") or a.get("title") or "?",
        "thumbnail": big_thumbnail(a.get("thumbnails"), 400),
    }


def search(query: str, limit: int = 20, kind: str = "all") -> dict:
    """[kind]: all | songs | albums | artists. Dla „all” – po kilka wyników każdego rodzaju."""
    yt = _client()
    want = ["songs", "albums", "artists"] if kind == "all" else [kind]
    limits = dict(LIMITS_ALL, songs=limit) if kind == "all" else LIMITS_ONE
    found = {k: yt.search(query, filter=k, limit=limits[k]) for k in want}
    return {
        "tracks": [_song(s) for s in found.get("songs", []) if s.get("videoId")][:limits["songs"]],
        "albums": [_album_item(a) for a in found.get("albums", []) if a.get("browseId")][:limits["albums"]],
        "artists": [_artist_item(a) for a in found.get("artists", []) if a.get("browseId")][:limits["artists"]],
    }


def artist(browse_id: str) -> dict:
    """Strona wykonawcy: najpopularniejsze utwory, albumy i single (pełne listy, jeśli YouTube Music je ma)."""
    yt = _client()
    a = yt.get_artist(browse_id)
    name = a.get("name") or "?"

    def full(section: str) -> list[dict]:
        sec = a.get(section) or {}
        items = sec.get("results") or []
        if sec.get("params") and sec.get("browseId"):
            try:
                items = yt.get_artist_albums(sec["browseId"], sec["params"]) or items
            except Exception:
                pass
        return [_album_item(i, name) for i in items if i.get("browseId")]

    songs = [_song(s) for s in (a.get("songs") or {}).get("results") or [] if s.get("videoId")]
    return {
        "id": browse_id,
        "name": name,
        "thumbnail": big_thumbnail(a.get("thumbnails"), 800),
        "tracks": songs,
        "albums": full("albums"),
        "singles": full("singles"),
    }


def find_artist(name: str) -> str | None:
    """browseId wykonawcy o tej nazwie (pierwszy wynik)."""
    for a in _client().search(name, filter="artists", limit=3):
        if a.get("browseId"):
            return a["browseId"]
    return None


def album(browse_id: str) -> dict:
    a = _client().get_album(browse_id)
    artist = _artists(a)
    return {
        "id": browse_id,
        "title": a.get("title") or "?",
        "artist": artist,
        "year": _year(a.get("year")),
        "thumbnail": big_thumbnail(a.get("thumbnails")),
        "tracks": [
            {
                "id": t["videoId"],
                "title": t.get("title") or "?",
                "artist": _artists(t) if t.get("artists") else artist,
                "album": a.get("title"),
                "album_id": browse_id,
                "track_no": t.get("trackNumber") or i + 1,
                "duration": t.get("duration_seconds") or 0,
                "thumbnail": big_thumbnail(a.get("thumbnails"), 400),
            }
            for i, t in enumerate(a.get("tracks") or [])
            if t.get("videoId") and t.get("isAvailable", True)
        ],
    }


def related(video_id: str, limit: int = 25) -> list[dict]:
    """Utwory podobne do danego („radio” YouTube Music)."""
    tracks = _client().get_watch_playlist(video_id, limit=limit).get("tracks") or []
    return [
        {
            "id": t["videoId"],
            "title": t.get("title") or "?",
            "artist": _artists(t),
            "album": (t.get("album") or {}).get("name"),
            "album_id": (t.get("album") or {}).get("id"),
            "duration": _seconds(t.get("length")),
            "thumbnail": big_thumbnail(t.get("thumbnail"), 400),
        }
        for t in tracks
        if t.get("videoId") and t["videoId"] != video_id
    ]


def _seconds(length: str | None) -> int:
    total = 0
    for part in (length or "").split(":"):
        total = total * 60 + (int(part) if part.isdigit() else 0)
    return total


# ---------------------------------------------------------------- okładki i zdjęcia

def cover_candidates(artist: str, album: str, limit: int = 8) -> list[dict]:
    """Propozycje okładki albumu: YouTube Music + iTunes (duże, dobrej jakości)."""
    out: list[dict] = []
    q = f"{artist} {album}".strip()
    try:
        for a in _client().search(q, filter="albums", limit=limit):
            if url := big_thumbnail(a.get("thumbnails")):
                out.append({"url": url, "title": a.get("title") or "", "subtitle": _artists(a), "source": "YouTube Music"})
    except Exception:
        pass
    out += _itunes(q, limit)
    return _dedupe(out)


def artist_image_candidates(name: str, limit: int = 8) -> list[dict]:
    out: list[dict] = []
    try:
        for a in _client().search(name, filter="artists", limit=limit):
            if url := big_thumbnail(a.get("thumbnails")):
                out.append({"url": url, "title": a.get("artist") or "", "subtitle": "", "source": "YouTube Music"})
    except Exception:
        pass
    return _dedupe(out)


def _itunes(term: str, limit: int) -> list[dict]:
    import httpx

    try:
        r = httpx.get("https://itunes.apple.com/search", params={"term": term, "entity": "album", "limit": limit},
                      timeout=10)
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return []
    return [
        {
            "url": re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", a["artworkUrl100"]),
            "title": a.get("collectionName") or "",
            "subtitle": a.get("artistName") or "",
            "source": "iTunes",
        }
        for a in results
        if a.get("artworkUrl100")
    ]


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for i in items:
        if i["url"] not in seen:
            seen.add(i["url"])
            out.append(i)
    return out


# ---------------------------------------------------------------- teledyski

_NOT_CLIP = re.compile(r"cover|live|na żywo|parod|piano|karaoke|tekst|lyric|remix|reakcj|reaction|instrumental|8d",
                       re.IGNORECASE)


def find_clip(artist: str, title: str, video_id: str | None = None) -> dict | None:
    """Oficjalny teledysk do utworu (do tła na ekranie odtwarzania). None – nie znaleziono.

    Utwory z YouTube Music to zwykle „art track” (statyczna okładka) – szukamy do nich
    oficjalnego klipu (OMV); w ostateczności filmu o pasującym tytule bez „cover”, „live” itp."""
    from ..text import normalize

    yt = _client()
    if video_id:
        try:
            t = yt.get_watch_playlist(video_id, limit=1)["tracks"][0]
            if t.get("videoType") == "MUSIC_VIDEO_TYPE_OMV":
                return {"video_id": video_id, "title": t.get("title") or title}
            cp = t.get("counterpart") or {}
            if cp.get("videoId"):
                return {"video_id": cp["videoId"], "title": cp.get("title") or title}
        except Exception:
            pass
    want = normalize(title)
    who = normalize(artist.split(",")[0].split(" feat")[0])
    results = yt.search(f"{artist} {title}", filter="videos", limit=10)

    def matches(v: dict) -> bool:
        name = v.get("title") or ""
        channel = normalize(" ".join(a.get("name") or "" for a in v.get("artists") or []))
        by_artist = bool(who) and (who in normalize(name) or who in channel)
        return (by_artist and want in normalize(name)
                and not (_NOT_CLIP.search(name) and not _NOT_CLIP.search(title)))

    candidates = [v for v in results if v.get("videoId") and matches(v)]
    best = next((v for v in candidates if v.get("videoType") == "MUSIC_VIDEO_TYPE_OMV"), None)
    best = best or next(iter(candidates), None)
    return {"video_id": best["videoId"], "title": best.get("title") or title} if best else None



# ---------------------------------------------------------------- darmowe obrazki (playlisty)

_UA = {"User-Agent": "Nati/0.6 (self-hosted music server)"}


# Standardowe szerokości miniatur Wikimedia – są gotowe w ich pamięci podręcznej, więc przychodzą od razu;
# inne rozmiary serwer Wikimedia generuje na żądanie i przy kilku naraz odpowiada 429 („za dużo zapytań”).
_COMMONS_WIDTHS = (1920, 1280, 960, 500, 330)
_PREVIEW_WIDTH = 330
FREE_IMAGE_SOURCES = ("commons", "openverse")

# Wyniki wyszukiwania na godzinę – te same hasła (np. gotowe podpowiedzi) otwierają się od razu.
_free_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_FREE_TTL = 3600


def _commons(query: str, limit: int) -> list[dict]:
    import httpx

    r = httpx.get("https://commons.wikimedia.org/w/api.php", headers=_UA, timeout=15, params={
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
        "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limit, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": _PREVIEW_WIDTH,
        "iiextmetadatafilter": "LicenseShortName|Artist"})
    r.raise_for_status()
    out = []
    pages = sorted((r.json().get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        thumb = ii.get("thumburl")
        if not thumb:
            continue
        # Pełny obraz po wyborze: największa standardowa szerokość, nie większa niż oryginał.
        width = ii.get("width") or 0
        full = next((w for w in _COMMONS_WIDTHS if w <= width), None)
        full_url = thumb.replace(f"/{_PREVIEW_WIDTH}px-", f"/{full}px-") if full and full != _PREVIEW_WIDTH else thumb
        meta = ii.get("extmetadata") or {}
        lic = (meta.get("LicenseShortName") or {}).get("value", "")
        out.append({"url": thumb, "full_url": full_url,
                    "title": p.get("title", "").removeprefix("File:").rsplit(".", 1)[0],
                    "subtitle": lic, "source": "Wikimedia Commons"})
    return out


def _openverse(query: str) -> list[dict]:
    import httpx

    r = httpx.get("https://api.openverse.org/v1/images/", headers=_UA, timeout=12,
                  params={"q": query, "page_size": 12})
    r.raise_for_status()
    return [{"url": i["thumbnail"], "full_url": i["thumbnail"], "title": i.get("title") or "",
             "subtitle": (i.get("license") or "").upper(), "source": "Openverse"}
            for i in r.json().get("results", []) if i.get("thumbnail")]


def free_images(query: str, source: str | None = None, limit: int = 24) -> list[dict]:
    """Obrazki na wolnych licencjach z jednego źródła (commons / openverse) albo z obu naraz.

    Aplikacja pyta o każde źródło osobno – szybka Wikimedia nie czeka na wolniejsze Openverse."""
    if source is None:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(2) as pool:
            parts = list(pool.map(lambda s: free_images(query, s, limit), FREE_IMAGE_SOURCES))
        return _dedupe([i for part in parts for i in part])
    key = (source, " ".join(query.lower().split()))
    hit = _free_cache.get(key)
    if hit and time.time() - hit[0] < _FREE_TTL:
        return hit[1]
    try:
        items = _commons(query, limit) if source == "commons" else _openverse(query)
    except Exception:
        return []  # błąd nie trafia do pamięci – następnym razem spróbujemy znowu
    if len(_free_cache) > 200:
        _free_cache.clear()
    _free_cache[key] = (time.time(), items)
    return _dedupe(items)
