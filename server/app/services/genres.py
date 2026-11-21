"""Katalog gatunków Nati: wbudowana lista + gatunki dodane przez użytkowników.

Gatunki z tagów plików też trafiają do bazy (skaner), ale z katalogu nie znikają, gdy żaden
utwór ich nie używa – dzięki temu da się je wybierać z listy."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Genre
from ..text import normalize

BUILTIN = [
    # rock i ostrzejsze
    "Rock", "Hard Rock", "Alternative", "Indie", "Grunge", "Punk", "Post-punk", "Metal", "Heavy Metal",
    "Hardcore", "Emo", "Progressive Rock", "Psychedelic Rock",
    # pop i taneczne
    "Pop", "Synthpop", "K-Pop", "Dance", "Disco", "Disco polo", "Electronic", "House", "Techno", "Trance",
    "Drum and Bass", "Dubstep", "EDM", "Ambient", "Chillout", "Downtempo", "Lo-fi", "New Wave",
    # hip-hop, r&b
    "Hip-Hop", "Rap", "Trap", "R&B", "Soul", "Funk", "Gospel",
    # akustyczne i tradycyjne
    "Folk", "Country", "Blues", "Jazz", "Swing", "Reggae", "Ska", "Latin", "World", "Singer-Songwriter",
    "Acoustic", "Poezja śpiewana", "Piosenka aktorska", "Szanty", "Muzyka ludowa",
    # klasyka i inne
    "Classical", "Opera", "Soundtrack", "Musical", "Muzyka dziecięca", "Kolędy", "Experimental", "Instrumental",
]


def seed(db: Session) -> None:
    """Dopisuje wbudowane gatunki do katalogu (bez duplikatów; idempotentne)."""
    existing = {g.search: g for g in db.scalars(select(Genre))}
    for name in BUILTIN:
        g = existing.get(normalize(name))
        if g is None:
            db.add(Genre(name=name, search=normalize(name), in_catalog=True, custom=False))
        elif not g.in_catalog and not g.hidden:  # ukryty = usunięty przez użytkownika
            g.in_catalog = True
    db.commit()


def add(db: Session, name: str) -> Genre:
    """Nowy gatunek w katalogu (albo istniejący o tej samej nazwie, bez względu na wielkość liter)."""
    name = " ".join(name.split())
    key = normalize(name)
    g = db.scalar(select(Genre).where(Genre.search == key))
    if g is None:
        g = Genre(name=name, search=key, in_catalog=True, custom=True)
        db.add(g)
    else:
        g.in_catalog, g.hidden = True, False
    db.commit()
    return g
