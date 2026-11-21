"""Gatunki, filtry i sortowanie list, strona główna z przypiętymi."""

import mutagen

from .test_edit import _album, _track, _user


def test_genres_read_from_tags(app_client, admin, scanned):
    genres = {g["name"]: g for g in app_client.get("/api/genres", headers=admin).json()}
    assert set(genres) == {"Rock", "Electro", "Pop", "Poezja śpiewana"}
    assert genres["Rock"]["track_count"] == 2 and genres["Rock"]["album_count"] == 1
    t = _track(app_client, admin, "pierwsza")
    assert t["genres"] == ["Electro", "Rock"]
    a = _album(app_client, admin, "Pierwszy")
    assert a["genres"][0] == "Rock"


def test_filter_by_genre(app_client, admin, scanned):
    def titles(path, **params):
        items = app_client.get(path, params=params, headers=admin).json()["items"]
        return [i.get("title") or i.get("name") for i in items]

    assert titles("/api/tracks", genre="rock", sort="album") == ["Pierwsza", "Druga"]
    assert titles("/api/tracks", genre="ELECTRO") == ["Pierwsza"]
    assert titles("/api/albums", genre="poezja spiewana") == ["Koncert"]
    assert titles("/api/artists", genre="rock") == ["Zespół"]
    assert titles("/api/tracks", genre="jazz") == []
    assert titles("/api/tracks", q="pierwsza", genre="rock") == ["Pierwsza"]


def test_sorting(app_client, admin, scanned):
    def titles(path, **params):
        return [i["title"] for i in app_client.get(path, params=params, headers=admin).json()["items"]]

    assert titles("/api/tracks", sort="title") == ["Druga", "Pierwsza", "Śpiewaj"]
    assert titles("/api/tracks", sort="year")[0] == "Śpiewaj"  # 2026
    # Odwrotna kolejność – też przy stronicowaniu.
    assert titles("/api/tracks", sort="title", reverse=True) == ["Śpiewaj", "Pierwsza", "Druga"]
    assert titles("/api/tracks", sort="title", reverse=True, limit=1, offset=1) == ["Pierwsza"]
    assert titles("/api/tracks", sort="year", reverse=True)[-1] == "Śpiewaj"
    for sort in ("artist", "album", "added"):
        forward = titles("/api/tracks", sort=sort)
        assert titles("/api/tracks", sort=sort, reverse=True) == forward[::-1], sort
    assert titles("/api/albums", sort="title") == ["Koncert", "Pierwszy"]
    assert titles("/api/albums", sort="artist") == ["Koncert", "Pierwszy"]  # Nati < Zespół
    assert app_client.get("/api/tracks", params={"sort": "zle"}, headers=admin).status_code == 422


def test_edit_genre(app_client, admin, scanned, music_dir):
    t = _track(app_client, admin, "śpiewaj")
    res = app_client.patch(f"/api/tracks/{t['id']}", json={"genre": "Jazz; Pop"}, headers=admin)
    assert res.status_code == 200 and res.json()["genres"] == ["Jazz", "Pop"]
    assert mutagen.File(music_dir / "Nati" / "Koncert" / "01.flac", easy=True)["genre"] == ["Jazz", "Pop"]
    names = {g["name"] for g in app_client.get("/api/genres", headers=admin).json()}
    assert "Poezja śpiewana" not in names  # nieużywany gatunek znika


def test_old_library_is_rescanned_for_genres(app_client, admin, scanned):
    """Biblioteka zeskanowana starszą wersją (bez gatunków) – pliki bez zmian, a gatunki i tak się pojawią."""
    from app.db import new_session
    from app.db.models import Track, track_genres
    with new_session() as db:
        db.execute(track_genres.delete())
        for t in db.query(Track):
            t.tags_version = None
        db.commit()
    st = app_client.app.state.scanner.scan()
    assert st.updated == 3
    assert len(app_client.get("/api/genres", headers=admin).json()) == 4


def test_home_pins(app_client, admin, scanned):
    a = _album(app_client, admin, "Koncert")
    artist = app_client.get("/api/artists", headers=admin).json()["items"][0]
    pl = app_client.post("/api/playlists", json={"name": "Ulubione"}, headers=admin).json()
    for kind, id_ in [("album", a["id"]), ("artist", artist["id"]), ("playlist", pl["id"])]:
        res = app_client.post("/api/pins", json={"kind": kind, "id": id_}, headers=admin)
        assert res.status_code == 201, res.text
    # Drugi raz to samo – bez duplikatu.
    app_client.post("/api/pins", json={"kind": "album", "id": a["id"]}, headers=admin)
    home = app_client.get("/api/home", headers=admin).json()
    assert [p["kind"] for p in home["pins"]] == ["album", "artist", "playlist"]
    assert home["pins"][0]["album"]["title"] == "Koncert"
    assert home["pins"][2]["playlist"]["name"] == "Ulubione"
    assert len(home["recent_albums"]) == 2 and home["genres"]

    # Kolejność, odpinanie, usunięty element znika z Home.
    order = [{"kind": "playlist", "id": pl["id"]}, {"kind": "album", "id": a["id"]}, {"kind": "artist", "id": artist["id"]}]
    assert app_client.put("/api/pins", json=order, headers=admin).status_code == 204
    assert [p["kind"] for p in app_client.get("/api/home", headers=admin).json()["pins"]] == ["playlist", "album", "artist"]
    app_client.delete(f"/api/pins/artist/{artist['id']}", headers=admin)
    app_client.delete(f"/api/playlists/{pl['id']}", headers=admin)
    assert [p["kind"] for p in app_client.get("/api/home", headers=admin).json()["pins"]] == ["album"]
    assert app_client.post("/api/pins", json={"kind": "album", "id": 999}, headers=admin).status_code == 404


def test_pins_are_per_user(app_client, admin, scanned):
    a = _album(app_client, admin, "Koncert")
    app_client.post("/api/pins", json={"kind": "album", "id": a["id"]}, headers=admin)
    nati = _user(app_client, admin)
    assert app_client.get("/api/home", headers=nati).json()["pins"] == []
    # Cudzej playlisty nie da się przypiąć.
    pl = app_client.post("/api/playlists", json={"name": "Moja"}, headers=admin).json()
    assert app_client.post("/api/pins", json={"kind": "playlist", "id": pl["id"]}, headers=nati).status_code == 404


def test_enrich_genres_and_images(app_client, admin, scanned, music_dir, tmp_path, monkeypatch):
    """Uzupełnianie z internetu (atrapy MusicBrainz i YouTube Music): gatunki tylko tam, gdzie ich brak,
    w bazie (plik bez zmian), zdjęcie wykonawcy."""
    from app.services.enrich import Enricher
    from app.db import new_session
    from app.db.models import Track, track_genres
    from .conftest import make_image

    # „Koncert” bez gatunku w bazie (jak plik bez tagu).
    with new_session() as db:
        t = db.query(Track).filter_by(title="Śpiewaj").one()
        db.execute(track_genres.delete().where(track_genres.c.track_id == t.id))
        db.commit()
    jpg = make_image(tmp_path / "artist.jpg").read_bytes()
    app = app_client.app
    enricher = Enricher(new_session, app.state.scanner, app.state.editor)
    monkeypatch.setattr(Enricher, "_mb_genres", lambda self, client, name: ["Poezja śpiewana"] if name == "Nati" else [])
    monkeypatch.setattr(Enricher, "_itunes_genre", staticmethod(lambda client, artist, album: None))
    monkeypatch.setattr(Enricher, "_image_url", staticmethod(lambda name: "https://img/x" if name == "Nati" else None))
    monkeypatch.setattr(Enricher, "_download", staticmethod(lambda client, url: jpg))
    enricher._run()
    assert enricher.status.done == 2 and enricher.status.images_added == 1 and enricher.status.genres_added == 1
    t = _track(app_client, admin, "śpiewaj")
    assert t["genres"] == ["Poezja śpiewana"]
    # Plik nietknięty (gatunek z internetu jest tylko w bazie Nati).
    assert mutagen.File(music_dir / "Nati" / "Koncert" / "01.flac", easy=True)["genre"] == ["Pop; Poezja śpiewana"]
    nati = next(a for a in app_client.get("/api/artists", headers=admin).json()["items"] if a["name"] == "Nati")
    assert nati["image_id"]
    # Drugi przebieg nic nie robi (wykonawcy już sprawdzeni); „ponownie” – sprawdza znowu.
    enricher._run()
    assert enricher.status.done == 2
    enricher.request(again=True)
    enricher._run()
    assert enricher.status.total == 2



def test_enrich_album_genre_from_itunes(app_client, admin, scanned, monkeypatch):
    from app.db import new_session
    from app.db.models import track_genres
    from app.services.enrich import Enricher
    with new_session() as db:
        db.execute(track_genres.delete())
        db.commit()
    enricher = Enricher(new_session, app_client.app.state.scanner, app_client.app.state.editor)
    monkeypatch.setattr(Enricher, "_itunes_genre",
                        staticmethod(lambda client, artist, album: "Metal" if album == "Pierwszy" else None))
    monkeypatch.setattr(Enricher, "_mb_genres", lambda self, client, name: ["Pop"])
    monkeypatch.setattr(Enricher, "_image_url", staticmethod(lambda name: None))
    enricher._run()
    by_title = {t["title"]: t["genres"] for t in app_client.get("/api/tracks", headers=admin).json()["items"]}
    assert by_title["Pierwsza"] == ["Metal"] and by_title["Druga"] == ["Metal"]  # z iTunes – dla albumu
    assert by_title["Śpiewaj"] == ["Pop"]  # album nieznany w iTunes → gatunek wykonawcy z MusicBrainz


def test_genre_catalog(app_client, admin, scanned):
    catalog = {g["name"]: g for g in app_client.get("/api/genres", params={"catalog": True}, headers=admin).json()}
    assert "Disco polo" in catalog and catalog["Disco polo"]["track_count"] == 0  # wbudowany, nieużywany
    assert catalog["Rock"]["track_count"] == 2
    assert "Disco polo" not in {g["name"] for g in app_client.get("/api/genres", headers=admin).json()}
    # Własny gatunek: dodanie (drugi raz – bez duplikatu), użycie, usunięcie z katalogu.
    res = app_client.post("/api/genres", json={"name": "Muzyka  z gier"}, headers=admin)
    assert res.status_code == 201 and res.json()["name"] == "Muzyka z gier" and res.json()["custom"]
    app_client.post("/api/genres", json={"name": "muzyka z gier"}, headers=admin)
    names = [g["name"] for g in app_client.get("/api/genres", params={"catalog": True}, headers=admin).json()]
    assert names.count("Muzyka z gier") == 1
    # Gatunek nie znika z katalogu po zmianie tagów utworu (nieużywany).
    t = _track(app_client, admin, "druga")
    app_client.patch(f"/api/tracks/{t['id']}", json={"genre": "Muzyka z gier"}, headers=admin)
    app_client.patch(f"/api/tracks/{t['id']}", json={"genre": "Rock"}, headers=admin)
    names = [g["name"] for g in app_client.get("/api/genres", params={"catalog": True}, headers=admin).json()]
    assert "Muzyka z gier" in names
    gid = next(g["id"] for g in app_client.get("/api/genres", params={"catalog": True}, headers=admin).json()
               if g["name"] == "Muzyka z gier")
    assert app_client.delete(f"/api/genres/{gid}", headers=admin).status_code == 204
    names = [g["name"] for g in app_client.get("/api/genres", params={"catalog": True}, headers=admin).json()]
    assert "Muzyka z gier" not in names


def _genres(client, headers, catalog=True):
    return {g["name"]: g for g in client.get("/api/genres", params={"catalog": catalog}, headers=headers).json()}


def test_delete_genre_removes_it_from_tracks(app_client, admin, scanned, music_dir):
    import mutagen
    rock = _genres(app_client, admin)["Rock"]
    assert app_client.delete(f"/api/genres/{rock['id']}", headers=admin).status_code == 204
    assert "Rock" not in _genres(app_client, admin)
    for t in app_client.get("/api/tracks", headers=admin).json()["items"]:
        assert "Rock" not in t["genres"]
    assert "genre" not in mutagen.File(music_dir / "Zespół" / "Pierwszy" / "01.mp3", easy=True) or \
        mutagen.File(music_dir / "Zespół" / "Pierwszy" / "01.mp3", easy=True)["genre"] != ["Rock"]
    # Wbudowany gatunek nie wraca przy starcie serwera (seed).
    from app.db import new_session
    from app.services import genres as catalog
    with new_session() as db:
        catalog.seed(db)
    assert "Rock" not in _genres(app_client, admin)
    # Utworzony ponownie – wraca.
    app_client.post("/api/genres", json={"name": "Rock"}, headers=admin)
    assert "Rock" in _genres(app_client, admin)


def test_rename_and_merge_genre(app_client, admin, scanned):
    rock = _genres(app_client, admin)["Rock"]
    res = app_client.patch(f"/api/genres/{rock['id']}", json={"name": "Rock polski"}, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "Rock polski" and res.json()["track_count"] == 2
    by_title = {t["title"]: t["genres"] for t in app_client.get("/api/tracks", headers=admin).json()["items"]}
    assert "Rock polski" in by_title["Pierwsza"]
    # Połączenie z istniejącym gatunkiem.
    jazz = _genres(app_client, admin)["Jazz"]
    res = app_client.patch(f"/api/genres/{res.json()['id']}", json={"name": "jazz"}, headers=admin)
    assert res.status_code == 200 and res.json()["id"] == jazz["id"] and res.json()["track_count"] == 2
    assert "Rock polski" not in _genres(app_client, admin)


def test_genre_look_and_image(app_client, admin, scanned, tmp_path):
    from .conftest import make_image
    rock = _genres(app_client, admin)["Rock"]
    res = app_client.patch(f"/api/genres/{rock['id']}", json={"icon": "bolt", "color1": "#112233", "color2": "#445566"},
                           headers=admin)
    assert res.status_code == 200 and res.json()["icon"] == "bolt" and res.json()["color1"] == "#112233"
    assert app_client.put(f"/api/genres/{rock['id']}/image", content=make_image(tmp_path / "x.png").read_bytes(), headers=admin).status_code == 200
    g = _genres(app_client, admin)["Rock"]
    assert g["image_id"] and app_client.get(f"/api/genres/{rock['id']}/image", headers=admin).status_code == 200
    app_client.delete(f"/api/genres/{rock['id']}/image", headers=admin)
    assert _genres(app_client, admin)["Rock"]["image_id"] is None
    app_client.patch(f"/api/genres/{rock['id']}", json={"icon": ""}, headers=admin)
    assert _genres(app_client, admin)["Rock"]["icon"] is None


def test_banners(app_client, admin, scanned, tmp_path):
    from .conftest import make_image
    album = app_client.get("/api/albums", headers=admin).json()["items"][0]
    artist = app_client.get("/api/artists", headers=admin).json()["items"][0]
    pl = app_client.post("/api/playlists", json={"name": "Lato"}, headers=admin).json()
    for kind, item in (("albums", album), ("artists", artist), ("playlists", pl)):
        res = app_client.put(f"/api/{kind}/{item['id']}/banner", content=make_image(tmp_path / "x.png").read_bytes(), headers=admin)
        assert res.status_code == 200, res.text
        assert app_client.get(f"/api/{kind}/{item['id']}/banner", headers=admin).status_code == 200
    assert app_client.get(f"/api/albums/{album['id']}", headers=admin).json()["banner_id"]
    assert app_client.get(f"/api/artists/{artist['id']}", headers=admin).json()["banner_id"]
    assert app_client.get(f"/api/playlists/{pl['id']}", headers=admin).json()["banner_id"]
    assert app_client.delete(f"/api/albums/{album['id']}/banner", headers=admin).status_code == 204
    assert app_client.get(f"/api/albums/{album['id']}/banner", headers=admin).status_code == 404
    assert app_client.put("/api/users/1/banner", content=make_image(tmp_path / "x.png").read_bytes(), headers=admin).status_code == 404


def test_genre_suggestions_from_library(app_client, admin, scanned):
    t = _track(app_client, admin, "pierwsza")
    s = app_client.get(f"/api/tracks/{t['id']}/genre-suggestions", headers=admin).json()
    assert set(s["library"]) == {"Rock", "Electro"} and s["online"] == []  # bez internetu w testach


def test_favorites_banner_and_artist_counts(app_client, admin, scanned, tmp_path):
    from .conftest import make_image
    img = make_image(tmp_path / "x.png").read_bytes()
    assert app_client.put("/api/favorites/banner", content=img, headers=admin).status_code == 200
    look = app_client.get("/api/favorites/look", headers=admin).json()
    assert look["banner_id"] and look["image_id"] is None
    assert app_client.get("/api/favorites/banner", headers=admin).status_code == 200
    assert app_client.delete("/api/favorites/banner", headers=admin).json()["banner_id"] is None
    assert app_client.get("/api/favorites/ids", headers=admin).status_code == 200
    artists = {a["name"]: a for a in app_client.get("/api/artists", headers=admin).json()["items"]}
    assert artists["Zespół"]["track_count"] == 2 and artists["Zespół"]["album_count"] == 1
    assert app_client.get(f"/api/artists/{artists['Zespół']['id']}", headers=admin).json()["track_count"] == 2
