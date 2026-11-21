def test_scan_builds_library(app_client, admin, scanned):
    assert scanned.added == 3
    artists = app_client.get("/api/artists", headers=admin).json()
    assert [a["name"] for a in artists["items"]] == ["Nati", "Zespół"]

    albums = app_client.get("/api/albums", headers=admin).json()["items"]
    by_title = {a["title"]: a for a in albums}
    assert by_title["Koncert"]["artist_name"] == "Nati"
    assert by_title["Koncert"]["year"] == 2026
    assert all(a["has_cover"] for a in albums)  # osadzona (MP3) i z folderu (FLAC)


def test_album_detail_sorted_by_track_number(app_client, admin, scanned):
    album = next(a for a in app_client.get("/api/albums", headers=admin).json()["items"] if a["title"] == "Pierwszy")
    detail = app_client.get(f"/api/albums/{album['id']}", headers=admin).json()
    assert [t["title"] for t in detail["tracks"]] == ["Pierwsza", "Druga"]
    assert detail["tracks"][0]["format"] == "mp3"
    assert 1.5 < detail["tracks"][0]["duration"] < 2.5


def test_featuring_artist_kept_on_track(app_client, admin, scanned):
    tracks = app_client.get("/api/tracks", params={"q": "śpiewaj"}, headers=admin).json()
    assert tracks["total"] == 1
    t = tracks["items"][0]
    assert t["artist_name"] == "Nati feat. Gość" and t["album_artist"] == "Nati"


def test_search(app_client, admin, scanned):
    assert app_client.get("/api/tracks", params={"q": "zesp"}, headers=admin).json()["total"] == 2
    assert app_client.get("/api/albums", params={"q": "konc"}, headers=admin).json()["total"] == 1
    assert app_client.get("/api/tracks", params={"q": "100%"}, headers=admin).json()["total"] == 0
    # Bez polskich znaków i wielkich liter też znajduje.
    assert app_client.get("/api/tracks", params={"q": "SPIEWAJ"}, headers=admin).json()["total"] == 1
    assert app_client.get("/api/tracks", params={"q": "gosc"}, headers=admin).json()["total"] == 1
    assert app_client.get("/api/artists", params={"q": "zespol"}, headers=admin).json()["total"] == 1


def test_rescan_is_incremental_and_removes_deleted(app_client, admin, scanned, music_dir):
    scanner = app_client.app.state.scanner
    again = scanner.scan()
    assert (again.added, again.updated, again.removed) == (0, 0, 0)

    (music_dir / "Nati" / "Koncert" / "01.flac").unlink()
    after = scanner.scan()
    assert after.removed == 1
    titles = [a["title"] for a in app_client.get("/api/albums", headers=admin).json()["items"]]
    assert titles == ["Pierwszy"]  # pusty album i wykonawca zostali posprzątani
    assert [a["name"] for a in app_client.get("/api/artists", headers=admin).json()["items"]] == ["Zespół"]


def test_missing_music_dir_does_not_wipe_library(app_client, admin, scanned, music_dir):
    music_dir.rename(music_dir.with_name("odmontowany"))
    st = app_client.app.state.scanner.scan()
    assert st.removed == 0 and st.errors
    assert app_client.get("/api/tracks", headers=admin).json()["total"] == 3


def test_tracks_from_removed_music_dir_are_dropped(app_client, admin, scanned, tmp_path):
    from app.services.scanner import LibraryScanner

    other = tmp_path / "inna_muzyka"
    other.mkdir()
    old = app_client.app.state.scanner
    # Użytkownik zmienił folder z muzyką na inny (pusty).
    new = LibraryScanner(old._session_factory, [other], old.covers_dir)
    st = new.scan()
    assert st.removed == 3
    assert app_client.get("/api/tracks", headers=admin).json()["total"] == 0


def test_tracks_of_artist(app_client, admin, scanned):
    artists = {a["name"]: a["id"] for a in app_client.get("/api/artists", headers=admin).json()["items"]}
    res = app_client.get("/api/tracks", params={"artist_id": artists["Zespół"], "sort": "album"}, headers=admin).json()
    assert [t["title"] for t in res["items"]] == ["Pierwsza", "Druga"] and res["total"] == 2
