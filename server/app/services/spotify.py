"""Import ze Spotify bez konta i kluczy: publiczna strona „embed” playlisty albo albumu
zawiera listę utworów (tytuł, wykonawcy, czas). Spotify podaje tak najwyżej ok. 100 pozycji."""

from __future__ import annotations

import json
import re

import httpx

from ..errors import CodedError
from ..text import normalize

_LINK = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?(?:embed/)?|spotify:)(playlist|album)[/:]([A-Za-z0-9]{10,40})")
_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
_UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


class SpotifyError(CodedError):
    def __init__(self, code: str, **params):
        super().__init__(code, 422, **params)


def parse_link(url: str) -> tuple[str, str]:
    m = _LINK.search(url.strip())
    if not m:
        raise SpotifyError("spotify.not_link")
    return m.group(1), m.group(2)


def parse_page(html: str) -> dict:
    m = _DATA.search(html)
    try:
        entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        raise SpotifyError("spotify.no_tracks") from e
    images = ((entity.get("coverArt") or {}).get("sources")) or []
    return {
        "kind": entity.get("type") or "playlist",
        "name": entity.get("name") or entity.get("title") or "Ze Spotify",
        "owner": entity.get("subtitle"),
        "image": images[0]["url"] if images else None,
        "tracks": [
            {"title": t["title"], "artist": t.get("subtitle") or "", "duration": (t.get("duration") or 0) / 1000}
            for t in entity.get("trackList") or []
            if t.get("title") and (t.get("entityType") or "track") == "track"
        ],
    }


def fetch(url: str) -> dict:
    kind, ident = parse_link(url)
    try:
        r = httpx.get(f"https://open.spotify.com/embed/{kind}/{ident}", headers=_UA, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise SpotifyError("spotify.unavailable", error=str(e)) from e
    return parse_page(r.text)


# ---------------------------------------------------------------- dopasowanie do biblioteki

_EXTRA = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]|\s+-\s+.*$|\s+feat\.?\s.*$", re.IGNORECASE)
_WORDS = re.compile(r"[^\w]+", re.UNICODE)


def title_key(title: str) -> str:
    """Tytuł bez dopisków („- Remastered 2011”, „(feat. X)”, „[Live]”) i znaków przestankowych."""
    return _WORDS.sub(" ", normalize(_EXTRA.sub("", title) or title)).strip()


def artist_keys(artists: str) -> set[str]:
    parts = re.split(r",|&| feat\.? | ft\.? | x | oraz | i ", normalize(artists))
    return {k for p in parts if (k := _WORDS.sub(" ", p).strip())}


def match_key(title: str, artists: str) -> list[tuple[str, str]]:
    t = title_key(title)
    return [(t, a) for a in artist_keys(artists)]
