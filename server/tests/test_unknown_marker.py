"""„Nieznany wykonawca / album” – neutralne znaczniki zamiast tekstu po polsku (i zamiana dawnych baz)."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.models import Album, Artist, Base, Track
from app.db.unknown import migrate_unknown
from app.text import UNKNOWN_ALBUM, UNKNOWN_ARTIST


def _db(tmp_path) -> Session:
    engine = create_engine(f"sqlite:///{tmp_path / 'n.db'}")
    Base.metadata.create_all(engine)
    return Session(engine)


def _track(db, album, title, artist_name, path):
    t = Track(title=title, artist_name=artist_name, album=album, path=path, mtime=0, size=1, duration=1.0,
              format="mp3")
    db.add(t)
    return t


def test_old_polish_names_become_markers(tmp_path):
    db = _db(tmp_path)
    old = Artist(name="Nieznany wykonawca")
    album = Album(title="Nieznany album", artist=old)
    db.add_all([old, album])
    _track(db, album, "Coś", "Nieznany wykonawca", "/m/a.mp3")
    db.commit()

    assert migrate_unknown(db) == 2
    artist = db.scalar(select(Artist))
    assert artist.name == UNKNOWN_ARTIST
    assert db.scalar(select(Album)).title == UNKNOWN_ALBUM
    track = db.scalar(select(Track))
    assert track.artist_name == UNKNOWN_ARTIST
    assert "unknown" in track.search
    assert migrate_unknown(db) == 0, "drugi start – nic do zrobienia"


def test_merges_with_existing_marker(tmp_path):
    """Część biblioteki zeskanowana już nową wersją – oba „nieznane” łączą się w jedno."""
    db = _db(tmp_path)
    new = Artist(name=UNKNOWN_ARTIST)
    old = Artist(name="Nieznany wykonawca")
    a_new = Album(title=UNKNOWN_ALBUM, artist=new)
    a_old = Album(title="Nieznany album", artist=old)
    a_old_named = Album(title="Demo", artist=old)
    db.add_all([new, old, a_new, a_old, a_old_named])
    _track(db, a_new, "Jeden", UNKNOWN_ARTIST, "/m/1.mp3")
    _track(db, a_old, "Dwa", "Nieznany wykonawca", "/m/2.mp3")
    _track(db, a_old_named, "Trzy", "Nieznany wykonawca", "/m/3.mp3")
    db.commit()

    migrate_unknown(db)
    assert [a.name for a in db.scalars(select(Artist))] == [UNKNOWN_ARTIST]
    albums = {a.title: a for a in db.scalars(select(Album))}
    assert set(albums) == {UNKNOWN_ALBUM, "Demo"}
    assert sorted(t.title for t in albums[UNKNOWN_ALBUM].tracks) == ["Dwa", "Jeden"]
    assert all(t.artist_name == UNKNOWN_ARTIST for t in db.scalars(select(Track)))
