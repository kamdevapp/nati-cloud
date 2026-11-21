"""Konta: profil, zarządzanie użytkownikami, role i udostępnianie playlist."""

from .conftest import make_image


def _login(client, username, password):
    res = client.post("/api/auth/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": "Bearer " + res.json()["access_token"]}


def _add_user(client, admin, username="ola", password="haslo123", **extra):
    res = client.post("/api/users", json={"username": username, "password": password, **extra}, headers=admin)
    assert res.status_code == 201, res.text
    return res.json(), _login(client, username, password)


def _tracks(client, headers):
    return [t["id"] for t in client.get("/api/tracks", headers=headers).json()["items"]]


def test_profile_and_avatar(app_client, admin, tmp_path):
    res = app_client.patch("/api/auth/me", json={"first_name": " Ala ", "last_name": "Nowak"}, headers=admin)
    assert res.json()["first_name"] == "Ala" and res.json()["last_name"] == "Nowak"
    img = make_image(tmp_path / "a.png", color="green").read_bytes()
    me = app_client.put("/api/auth/me/avatar", content=img, headers=admin).json()
    assert me["avatar_id"]
    got = app_client.get(f"/api/users/{me['id']}/avatar", headers=admin)
    assert got.status_code == 200 and got.content == img
    assert app_client.delete("/api/auth/me/avatar", headers=admin).json()["avatar_id"] is None
    assert app_client.get(f"/api/users/{me['id']}/avatar", headers=admin).status_code == 404


def test_change_password(app_client, admin):
    assert app_client.post("/api/auth/me/password", json={"current_password": "złe", "new_password": "nowe123"},
                           headers=admin).status_code == 403
    assert app_client.post("/api/auth/me/password", json={"current_password": "tajne123", "new_password": "nowe123"},
                           headers=admin).status_code == 204
    _login(app_client, "ala", "nowe123")


def test_manage_users(app_client, admin):
    ola, ola_h = _add_user(app_client, admin, first_name="Ola")
    assert ola["is_admin"] is False and ola["first_name"] == "Ola"
    assert app_client.get("/api/users", headers=ola_h).status_code == 403
    assert app_client.post("/api/users", json={"username": "x", "password": "123456"}, headers=ola_h).status_code == 403
    # Nadanie roli administratora, reset hasła.
    res = app_client.patch(f"/api/users/{ola['id']}", json={"is_admin": True, "password": "inne123"}, headers=admin)
    assert res.json()["is_admin"] is True
    _login(app_client, "ola", "inne123")
    # Ostatniego administratora nie da się odebrać ani usunąć siebie.
    me = app_client.get("/api/auth/me", headers=admin).json()
    app_client.patch(f"/api/users/{ola['id']}", json={"is_admin": False}, headers=admin)
    assert app_client.patch(f"/api/users/{me['id']}", json={"is_admin": False}, headers=admin).status_code == 409
    assert app_client.delete(f"/api/users/{me['id']}", headers=admin).status_code == 409
    # Usunięcie konta razem z jego playlistami.
    app_client.post("/api/playlists", json={"name": "Oli"}, headers=_login(app_client, "ola", "inne123"))
    assert app_client.delete(f"/api/users/{ola['id']}", headers=admin).status_code == 204
    assert [u["username"] for u in app_client.get("/api/users", headers=admin).json()] == ["ala"]


def test_each_user_has_own_home_and_playlists(app_client, admin, scanned):
    _, ola = _add_user(app_client, admin)
    album = app_client.get("/api/albums", headers=admin).json()["items"][0]
    app_client.post("/api/pins", json={"kind": "album", "id": album["id"]}, headers=admin)
    app_client.post("/api/playlists", json={"name": "Moja"}, headers=admin)
    assert app_client.get("/api/home", headers=ola).json()["pins"] == []
    assert app_client.get("/api/playlists", headers=ola).json() == []
    # Bibliotekę widzą wszyscy.
    assert _tracks(app_client, ola) == _tracks(app_client, admin)


def test_share_playlist(app_client, admin, scanned):
    ola, ola_h = _add_user(app_client, admin)
    _, ewa_h = _add_user(app_client, admin, "ewa")
    ids = _tracks(app_client, admin)
    pl = app_client.post("/api/playlists", json={"name": "Wspólna", "track_ids": ids[:2]}, headers=admin).json()
    url = f"/api/playlists/{pl['id']}"
    assert app_client.get(url, headers=ola_h).status_code == 404

    directory = app_client.get("/api/users/directory", headers=admin).json()
    assert {u["username"] for u in directory} == {"ola", "ewa"}

    # Tylko do słuchania.
    res = app_client.put(url + "/shares", json=[{"user_id": ola["id"]}], headers=admin)
    assert res.status_code == 200 and res.json()[0]["user"]["username"] == "ola"
    shared = app_client.get("/api/playlists/shared", headers=ola_h).json()
    assert [p["name"] for p in shared] == ["Wspólna"]
    assert shared[0]["mine"] is False and shared[0]["can_edit"] is False and shared[0]["owner_name"] == "ala"
    assert app_client.get(url, headers=ola_h).json()["track_count"] == 2
    assert app_client.patch(url, json={"track_ids": ids}, headers=ola_h).status_code == 403
    assert app_client.get(url, headers=ewa_h).status_code == 404
    assert app_client.get(url + "/shares", headers=ola_h).status_code == 403
    mine = app_client.get("/api/playlists", headers=admin).json()[0]
    assert mine["share_count"] == 1 and mine["mine"] is True

    # Z prawem zmieniania.
    app_client.put(url + "/shares", json=[{"user_id": ola["id"], "can_edit": True}], headers=admin)
    assert app_client.patch(url, json={"track_ids": ids}, headers=ola_h).json()["track_count"] == len(ids)
    # Udostępniona może być przypięta.
    assert app_client.post("/api/pins", json={"kind": "playlist", "id": pl["id"]}, headers=ola_h).status_code == 201
    assert app_client.get("/api/home", headers=ola_h).json()["pins"][0]["playlist"]["name"] == "Wspólna"

    # „Usunięcie” przez Olę = rezygnacja; u właściciela zostaje.
    assert app_client.delete(url, headers=ola_h).status_code == 204
    assert app_client.get("/api/playlists/shared", headers=ola_h).json() == []
    assert app_client.get(url, headers=admin).status_code == 200
    assert app_client.get("/api/home", headers=ola_h).json()["pins"] == []


def test_curator_role(app_client, admin, scanned):
    cur, cur_h = _add_user(app_client, admin, "kurator", role="curator")
    assert cur["role"] == "curator" and cur["is_admin"] is False
    t = client_track = app_client.get("/api/tracks", headers=admin).json()["items"][0]
    # Poprawia bibliotekę i dodaje muzykę…
    assert app_client.patch(f"/api/tracks/{t['id']}", json={"title": "Poprawiony"}, headers=cur_h).status_code == 200
    assert app_client.put(f"/api/tracks/{t['id']}/lyrics", json={"text": "la la"}, headers=cur_h).status_code == 200
    assert app_client.post("/api/upload", content=b"abc", headers={**cur_h, "X-Filename": "a.txt"}).status_code == 415
    # …ale nie usuwa z chmury i nie zarządza kontami.
    assert app_client.delete(f"/api/tracks/{t['id']}", headers=cur_h).status_code == 403
    assert app_client.get("/api/users", headers=cur_h).status_code == 403
    # Zmiana roli przez administratora.
    res = app_client.patch(f"/api/users/{cur['id']}", json={"role": "user"}, headers=admin)
    assert res.json()["role"] == "user"
    assert app_client.patch(f"/api/tracks/{client_track['id']}", json={"title": "x"}, headers=cur_h).status_code == 403


def test_favorites(app_client, admin, scanned):
    _, ola = _add_user(app_client, admin)
    ids = _tracks(app_client, admin)
    assert app_client.put(f"/api/favorites/{ids[0]}", headers=admin).status_code == 204
    assert app_client.put(f"/api/favorites/{ids[1]}", headers=admin).status_code == 204
    app_client.put(f"/api/favorites/{ids[1]}", headers=admin)  # drugi raz – bez zmian
    assert [t["id"] for t in app_client.get("/api/favorites", headers=admin).json()] == [ids[1], ids[0]]
    assert sorted(app_client.get("/api/favorites/ids", headers=admin).json()) == sorted(ids[:2])
    assert app_client.get("/api/favorites", headers=ola).json() == [], "ulubione są osobne dla każdego"
    assert app_client.get("/api/home", headers=admin).json()["favorite_count"] == 2
    assert app_client.delete(f"/api/favorites/{ids[0]}", headers=admin).status_code == 204
    assert [t["id"] for t in app_client.get("/api/favorites", headers=admin).json()] == [ids[1]]
    assert app_client.put("/api/favorites/999999", headers=admin).status_code == 404


def test_history(app_client, admin, scanned):
    _, ola = _add_user(app_client, admin)
    ids = _tracks(app_client, admin)
    for tid in (ids[0], ids[0], ids[1]):  # ten sam utwór od razu drugi raz = jeden odsłuch
        assert app_client.post("/api/history", json={"track_id": tid}, headers=admin).status_code == 204
    hist = app_client.get("/api/history", headers=admin).json()
    assert [h["track"]["id"] for h in hist] == [ids[1], ids[0]]
    assert app_client.get("/api/history", headers=ola).json() == []
    home = app_client.get("/api/home", headers=admin).json()
    played = [a["id"] for a in home["recently_played"]]
    assert played and played[0] == hist[0]["track"]["album_id"]
    assert app_client.delete("/api/history", headers=admin).status_code == 204
    assert app_client.get("/api/history", headers=admin).json() == []
