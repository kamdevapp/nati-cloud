"""Edycja tagów, okładek, zdjęć wykonawców i usuwanie z chmury."""

import os
import sqlite3
import stat
import sys

import mutagen
import pytest

from app.services import musicbrainz

from .conftest import make_image


def _album(client, headers, title):
    return next(a for a in client.get("/api/albums", headers=headers).json()["items"] if a["title"] == title)


def _track(client, headers, q):
    [t] = client.get("/api/tracks", params={"q": q}, headers=headers).json()["items"]
    return t


def _user(client, admin, name="nati", password="spiewam1"):
    client.post("/api/users", json={"username": name, "password": password}, headers=admin)
    tok = client.post("/api/auth/login", json={"username": name, "password": password}).json()["access_token"]
    return {"Authorization": "Bearer " + tok}


# ---------------------------------------------------------------- tagi

def test_edit_track_writes_file(app_client, admin, scanned, music_dir):
    t = _track(app_client, admin, "pierwsza")
    res = app_client.patch(f"/api/tracks/{t['id']}", json={"title": "Pierwsza (wersja 2)", "track_no": 5},
                           headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["id"] == t["id"] and res.json()["title"] == "Pierwsza (wersja 2)"
    tags = mutagen.File(music_dir / "Zespół" / "Pierwszy" / "01.mp3", easy=True)
    assert tags["title"] == ["Pierwsza (wersja 2)"] and tags["tracknumber"] == ["5"]
    # Po ponownym skanie zmiana zostaje (jest w pliku).
    app_client.app.state.scanner.scan()
    assert _track(app_client, admin, "wersja 2")["track_no"] == 5


def test_edit_read_only_file_is_kept_in_database(app_client, admin, scanned, music_dir):
    f = music_dir / "Nati" / "Koncert" / "01.flac"
    f.chmod(stat.S_IRUSR)
    try:
        t = _track(app_client, admin, "śpiewaj")
        res = app_client.patch(f"/api/tracks/{t['id']}", json={"title": "Śpiewaj głośno"}, headers=admin)
        assert res.status_code == 200, res.text
        assert mutagen.File(f, easy=True)["title"] == ["Śpiewaj"]  # plik nietknięty
        # Pełny skan (np. po zmianie pliku) nadal pokazuje tytuł z Nati.
        os.utime(f, (1, 1))
        app_client.app.state.scanner.scan()
        assert _track(app_client, admin, "głośno")["title"] == "Śpiewaj głośno"
    finally:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_edit_album_keeps_id(app_client, admin, scanned, music_dir):
    a = _album(app_client, admin, "Pierwszy")
    res = app_client.patch(f"/api/albums/{a['id']}", json={"title": "Debiut", "artist": "Zespół Nati", "year": 2020},
                           headers=admin)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["id"] == a["id"] and out["title"] == "Debiut" and out["artist_name"] == "Zespół Nati"
    assert out["year"] == 2020 and out["track_count"] == 2
    detail = app_client.get(f"/api/albums/{a['id']}", headers=admin).json()
    assert {t["artist_name"] for t in detail["tracks"]} == {"Zespół Nati"}  # wykonawca utworów też
    tags = mutagen.File(music_dir / "Zespół" / "Pierwszy" / "02.mp3", easy=True)
    assert tags["album"] == ["Debiut"] and tags["albumartist"] == ["Zespół Nati"] and tags["date"] == ["2020"]
    # Wykonawca przemianowany w miejscu (miał tylko ten album) – to samo id.
    assert out["artist_id"] == a["artist_id"]
    names = [x["name"] for x in app_client.get("/api/artists", headers=admin).json()["items"]]
    assert "Zespół" not in names


def test_album_rename_to_existing_merges(app_client, admin, scanned):
    a = _album(app_client, admin, "Koncert")
    res = app_client.patch(f"/api/albums/{a['id']}", json={"title": "Pierwszy", "artist": "Zespół"}, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["track_count"] == 3
    assert len(app_client.get("/api/albums", headers=admin).json()["items"]) == 1


def test_rename_artist(app_client, admin, scanned):
    artist = next(x for x in app_client.get("/api/artists", headers=admin).json()["items"] if x["name"] == "Nati")
    res = app_client.patch(f"/api/artists/{artist['id']}", json={"name": "Nati i Przyjaciele"}, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["id"] == artist["id"]
    t = _track(app_client, admin, "śpiewaj")
    assert t["album_artist"] == "Nati i Przyjaciele"
    assert t["artist_name"] == "Nati feat. Gość"  # inny wykonawca utworu zostaje


# ---------------------------------------------------------------- okładki i zdjęcia

def test_change_album_cover(app_client, admin, scanned, music_dir, tmp_path):
    a = _album(app_client, admin, "Pierwszy")
    png = make_image(tmp_path / "new.png", color="green").read_bytes()
    res = app_client.put(f"/api/albums/{a['id']}/cover", content=png, headers={**admin, "Content-Type": "image/png"})
    assert res.status_code == 200, res.text
    assert res.json()["cover_id"] != a["cover_id"]
    assert app_client.get(f"/api/albums/{a['id']}/cover", headers=admin).content == png
    # Osadzona w plikach (stara okładka usunięta, nie dopisana obok).
    apics = mutagen.File(music_dir / "Zespół" / "Pierwszy" / "01.mp3").tags.getall("APIC")
    assert len(apics) == 1 and apics[0].data == png
    # Po pełnym skanie okładka zostaje.
    app_client.app.state.scanner.scan()
    assert app_client.get(f"/api/albums/{a['id']}/cover", headers=admin).content == png


def test_cover_rejects_non_image_and_foreign_urls(app_client, admin, scanned):
    a = _album(app_client, admin, "Pierwszy")
    assert app_client.put(f"/api/albums/{a['id']}/cover", content=b"hello", headers=admin).status_code == 415
    res = app_client.post(f"/api/albums/{a['id']}/cover/url", json={"url": "http://192.168.1.1/router.png"},
                          headers=admin)
    assert res.status_code == 422


def test_artist_image(app_client, admin, scanned, tmp_path):
    artist = app_client.get("/api/artists", headers=admin).json()["items"][0]
    assert artist["image_id"] is None
    assert app_client.get(f"/api/artists/{artist['id']}/image", headers=admin).status_code == 404
    jpg = make_image(tmp_path / "a.jpg").read_bytes()
    res = app_client.put(f"/api/artists/{artist['id']}/image", content=jpg, headers=admin)
    assert res.status_code == 200 and res.json()["image_id"]
    assert app_client.get(f"/api/artists/{artist['id']}/image", headers=admin).content == jpg
    assert app_client.delete(f"/api/artists/{artist['id']}/image", headers=admin).status_code == 204
    assert app_client.get(f"/api/artists/{artist['id']}/image", headers=admin).status_code == 404


# ---------------------------------------------------------------- usuwanie

def test_delete_album_moves_files_to_trash(app_client, admin, scanned, music_dir, tmp_path):
    a = _album(app_client, admin, "Pierwszy")
    res = app_client.delete(f"/api/albums/{a['id']}", headers=admin)
    assert res.status_code == 200 and res.json() == {"deleted": 2}
    assert app_client.get(f"/api/albums/{a['id']}", headers=admin).status_code == 404
    assert not (music_dir / "Zespół").exists()  # puste foldery posprzątane
    trashed = list((tmp_path / "data" / "trash").rglob("*.mp3"))
    assert sorted(p.name for p in trashed) == ["01.mp3", "02.mp3"]
    names = [x["name"] for x in app_client.get("/api/artists", headers=admin).json()["items"]]
    assert names == ["Nati"]


def test_trash_is_purged_after_days(app_client, admin, scanned, tmp_path):
    t = _track(app_client, admin, "pierwsza")
    app_client.delete(f"/api/tracks/{t['id']}", headers=admin)
    editor = app_client.app.state.editor
    editor.purge_trash(30)
    assert list((tmp_path / "data" / "trash").iterdir())
    for d in (tmp_path / "data" / "trash").iterdir():
        os.utime(d, (1, 1))
    editor.purge_trash(30)
    assert not list((tmp_path / "data" / "trash").iterdir())


def test_only_admin_changes_library(app_client, admin, scanned):
    """Zwykły użytkownik tylko słucha i tworzy playlisty – biblioteki nie zmienia."""
    nati = _user(app_client, admin)
    t = _track(app_client, admin, "pierwsza")
    album = _album(app_client, admin, "Pierwszy")
    assert app_client.delete(f"/api/tracks/{t['id']}", headers=nati).status_code == 403
    assert app_client.patch(f"/api/tracks/{t['id']}", json={"title": "x"}, headers=nati).status_code == 403
    assert app_client.patch(f"/api/albums/{album['id']}", json={"title": "x"}, headers=nati).status_code == 403
    assert app_client.put(f"/api/tracks/{t['id']}/lyrics", json={"text": "x"}, headers=nati).status_code == 403
    assert app_client.post("/api/upload", content=b"abc", headers={**nati, "X-Filename": "a.mp3"}).status_code == 403
    # Słuchać i czytać może.
    assert app_client.get(f"/api/tracks/{t['id']}", headers=nati).status_code == 200


@pytest.mark.skipif(sys.platform == "win32", reason="Windows: chmod nie blokuje zapisu w folderze")
def test_delete_from_read_only_folder(app_client, admin, scanned, music_dir):
    folder = music_dir / "Nati" / "Koncert"
    folder.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        t = _track(app_client, admin, "śpiewaj")
        res = app_client.delete(f"/api/tracks/{t['id']}", headers=admin)
        assert res.status_code == 409 and res.json()["code"] == "delete.read_only_music"
        assert app_client.get(f"/api/tracks/{t['id']}", headers=admin).status_code == 200
    finally:
        folder.chmod(stat.S_IRWXU)


# ---------------------------------------------------------------- migracja i MusicBrainz

def test_old_database_gets_new_columns(tmp_path):
    from app import db
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE artists (id INTEGER PRIMARY KEY, name VARCHAR(512) UNIQUE, search VARCHAR)")
    con.execute("INSERT INTO artists (name, search) VALUES ('Stary', 'stary')")
    con.commit()
    con.close()
    db.init_db(f"sqlite:///{path}")
    cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(artists)")]
    assert "image" in cols


@pytest.mark.parametrize(("title", "uploader", "expected"), [
    ("Dawid Podsiadło - Małomiasteczkowy (Official Video)", "x", ("Dawid Podsiadło", "Małomiasteczkowy")),
    ("Queen – Bohemian Rhapsody [Official Video Remastered]", None, ("Queen", "Bohemian Rhapsody")),
    ("Sanah - Szampan (Lyric Video)", None, ("Sanah", "Szampan")),
])
def test_title_split(title, uploader, expected):
    assert musicbrainz.guess(title, uploader)[0] == expected


def test_musicbrainz_prefers_studio_album():
    rec = {"score": 100, "title": "Utwór", "artist-credit": [{"name": "Nati"}], "first-release-date": "2020-01-01"}
    data = {"recordings": [
        {**rec, "disambiguation": "live", "releases": [
            {"title": "Na żywo", "status": "Official", "date": "2019",
             "release-group": {"id": "live", "primary-type": "Album", "secondary-types": ["Live"]}}]},
        {**rec, "releases": [
            {"title": "Składanka", "status": "Official", "date": "2021",
             "release-group": {"id": "comp", "primary-type": "Album", "secondary-types": ["Compilation"]}},
            {"title": "Debiut", "status": "Official", "date": "2020-01-01",
             "release-group": {"id": "rg1", "primary-type": "Album"},
             "media": [{"track": [{"number": "4"}]}]}]},
        {"score": 100, "title": "Inny", "artist-credit": [{"name": "Ktoś"}]},
    ]}
    m = musicbrainz._parse(data, "Nati", "Utwór")
    assert (m.album, m.track_no, m.year) == ("Debiut", 4, 2020)
    assert m.cover_url.endswith("/release-group/rg1/front-500")
    assert musicbrainz._parse(data, "Zupełnie ktoś inny", "Utwór") is None


# ---------------------------------------------------------------- wysyłanie z urządzenia

def test_upload_from_device(app_client, admin, scanned, tmp_path):
    from .conftest import make_audio
    f = make_audio(tmp_path / "x" / "moja piosenka.mp3", title="Nowa", artist="Nati", album="Demo domowe", track="1")
    data = f.read_bytes()
    nati = admin
    res = app_client.post("/api/upload", content=data,
                          headers={**nati, "X-Filename": "moja%20piosenka.mp3"})
    assert res.status_code == 201, res.text
    t = res.json()
    assert (t["title"], t["album_title"], t["album_artist"]) == ("Nowa", "Demo domowe", "Nati")
    # Drugi raz to samo – odrzucone.
    again = app_client.post("/api/upload", content=data, headers={**nati, "X-Filename": "kopia.mp3"})
    assert again.status_code == 409 and again.headers["x-nati-track-id"] == str(t["id"])
    # Nie-muzyka – odrzucona.
    assert app_client.post("/api/upload", content=b"abc", headers={**nati, "X-Filename": "plik.txt"}).status_code == 415
    # Administrator może utwór usunąć.
    assert app_client.delete(f"/api/tracks/{t['id']}", headers=nati).status_code == 200
