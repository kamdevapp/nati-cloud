"""Teksty utworów: format LRC, pliki .lrc obok muzyki i pobieranie z internetu (LRCLIB).

LRCLIB (https://lrclib.net) to darmowa, otwarta baza tekstów – bez klucza i bez konta.
Wysyłamy do niej tylko wykonawcę, tytuł, album i długość utworu."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from .. import __version__
from ..text import normalize

log = logging.getLogger(__name__)

API = "https://lrclib.net/api"
USER_AGENT = f"Nati/{__version__} ( self-hosted music server )"

# „[01:23.45] linijka” (też „[1:23]”, „[01:23:45]”) – znacznik czasu na początku linii.
_TIMESTAMP = re.compile(r"^\s*\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", re.MULTILINE)
MAX_SIDECAR = 1024 * 1024


@dataclass
class Found:
    text: str
    synced: bool


def clean(text: str) -> str:
    """Ujednolica końce linii i usuwa puste linie na początku i końcu."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def is_synced(text: str) -> bool:
    """Czy tekst ma znaczniki czasu (przynajmniej w dwóch liniach)."""
    return len(_TIMESTAMP.findall(text)) >= 2


def sidecar(audio_path: str) -> str | None:
    """Tekst z pliku .lrc leżącego obok utworu (ta sama nazwa), jeśli jest."""
    lrc = Path(audio_path).with_suffix(".lrc")
    try:
        if not lrc.is_file() or lrc.stat().st_size > MAX_SIDECAR:
            return None
        raw = lrc.read_bytes()
    except OSError:
        return None
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            text = clean(raw.decode(encoding))
            return text or None
        except UnicodeDecodeError:
            continue
    return None


def _from_record(rec: dict) -> Found | None:
    synced = (rec.get("syncedLyrics") or "").strip()
    if synced:
        return Found(clean(synced), True)
    plain = (rec.get("plainLyrics") or "").strip()
    return Found(clean(plain), False) if plain else None


def _similar(a: str, b: str) -> bool:
    na, nb = normalize(a), normalize(b)
    return bool(na and nb) and (na == nb or na in nb or nb in na)


def pick(results: list[dict], artist: str, title: str, duration: float) -> Found | None:
    """Najlepszy wynik wyszukiwania: zgodny wykonawca i tytuł, długość ±3 s; najpierw teksty z czasami."""
    best: tuple[tuple, Found] | None = None
    for rec in results:
        if not _similar(rec.get("artistName") or "", artist) or not _similar(rec.get("trackName") or "", title):
            continue
        found = _from_record(rec)
        if found is None:
            continue
        diff = abs((rec.get("duration") or 0) - duration) if duration else 0
        # Tekst z czasami od nagrania o innej długości rozjechałby się z muzyką.
        if found.synced and diff > 3:
            plain = (rec.get("plainLyrics") or "").strip()
            if not plain:
                continue
            found = Found(clean(plain), False)
        rank = (not found.synced, diff)
        if best is None or rank < best[0]:
            best = (rank, found)
    return best[1] if best else None


async def fetch(artist: str, title: str, album: str | None, duration: float) -> Found | None:
    """Szuka tekstu w LRCLIB. None – nie ma. Błąd sieci → httpx.HTTPError."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        # Dokładne trafienie: wykonawca + tytuł + album + długość (±2 s po stronie LRCLIB).
        params = {"artist_name": artist, "track_name": title, "duration": round(duration)}
        if album:
            params["album_name"] = album
        r = await client.get(f"{API}/get", params=params)
        if r.status_code == 200:
            found = _from_record(r.json())
            if found:
                return found
        elif r.status_code != 404:
            r.raise_for_status()
        # Inne wydanie tego samego utworu (inny album, składanka…).
        r = await client.get(f"{API}/search", params={"artist_name": artist, "track_name": title})
        r.raise_for_status()
        data = r.json()
        return pick(data if isinstance(data, list) else [], artist, title, duration)
