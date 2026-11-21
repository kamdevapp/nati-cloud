def _ids(client, headers):
    return [t["id"] for t in client.get("/api/tracks", headers=headers).json()["items"]]


def test_playlist_crud(app_client, admin, scanned):
    ids = _ids(app_client, admin)
    res = app_client.post("/api/playlists", json={"name": "Do auta", "track_ids": [ids[2], ids[0]]}, headers=admin)
    assert res.status_code == 201
    pl = res.json()
    assert [t["id"] for t in pl["tracks"]] == [ids[2], ids[0]]

    listing = app_client.get("/api/playlists", headers=admin).json()
    assert listing[0]["track_count"] == 2 and listing[0]["duration"] > 3

    upd = app_client.patch(f"/api/playlists/{pl['id']}", json={"name": "Trasa", "track_ids": ids}, headers=admin)
    assert upd.json()["name"] == "Trasa" and len(upd.json()["tracks"]) == 3

    assert app_client.delete(f"/api/playlists/{pl['id']}", headers=admin).status_code == 204
    assert app_client.get("/api/playlists", headers=admin).json() == []


def test_playlist_rejects_unknown_tracks(app_client, admin, scanned):
    res = app_client.post("/api/playlists", json={"name": "X", "track_ids": [9999]}, headers=admin)
    assert res.status_code == 422


def test_playlists_are_private(app_client, admin, scanned):
    pl = app_client.post("/api/playlists", json={"name": "Moja"}, headers=admin).json()
    app_client.post("/api/users", json={"username": "nati", "password": "spiewam1"}, headers=admin)
    tok = app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).json()["access_token"]
    other = {"Authorization": "Bearer " + tok}
    assert app_client.get("/api/playlists", headers=other).json() == []
    assert app_client.get(f"/api/playlists/{pl['id']}", headers=other).status_code == 404


def test_playlist_image(app_client, admin, tmp_path):
    from .conftest import make_image
    from .test_edit import _user
    pl = app_client.post("/api/playlists", json={"name": "Do biegania"}, headers=admin).json()
    pid = pl["id"]
    assert pl["image_id"] is None
    png = make_image(tmp_path / "p.png", color="green").read_bytes()
    res = app_client.put(f"/api/playlists/{pid}/image", content=png, headers=admin)
    assert res.status_code == 200 and res.json()["image_id"]
    assert app_client.get(f"/api/playlists/{pid}/image", headers=admin).content == png
    assert app_client.get("/api/playlists", headers=admin).json()[0]["image_id"] == res.json()["image_id"]
    # Cudza playlista – nie wolno.
    nati = _user(app_client, admin)
    assert app_client.put(f"/api/playlists/{pid}/image", content=png, headers=nati).status_code == 404
    assert app_client.delete(f"/api/playlists/{pid}/image", headers=admin).status_code == 204
    assert app_client.get(f"/api/playlists/{pid}/image", headers=admin).status_code == 404


def test_playlist_cover_icon_and_colors(app_client, admin):
    pid = app_client.post("/api/playlists", json={"name": "Rock"}, headers=admin).json()["id"]
    res = app_client.patch(f"/api/playlists/{pid}", json={"icon": "guitar", "color1": "#e0457b", "color2": "#1c71d8"},
                           headers=admin)
    assert res.status_code == 200, res.text
    assert (res.json()["icon"], res.json()["color1"], res.json()["color2"]) == ("guitar", "#e0457b", "#1c71d8")
    assert app_client.get("/api/playlists", headers=admin).json()[0]["icon"] == "guitar"
    assert app_client.patch(f"/api/playlists/{pid}", json={"color1": "czerwony"}, headers=admin).status_code == 422
    # Pusty napis – domyślna okładka; zmiana samej nazwy nie rusza okładki.
    app_client.patch(f"/api/playlists/{pid}", json={"name": "Rock 2"}, headers=admin)
    assert app_client.get(f"/api/playlists/{pid}", headers=admin).json()["icon"] == "guitar"
    res = app_client.patch(f"/api/playlists/{pid}", json={"icon": "", "color1": "", "color2": ""}, headers=admin)
    assert res.json()["icon"] is None and res.json()["color1"] is None
