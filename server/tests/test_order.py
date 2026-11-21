def test_custom_order_per_user(app_client, admin):
    assert app_client.get("/api/order/album", headers=admin).json() == []
    res = app_client.put("/api/order/album", json=[3, 1, 2, 1], headers=admin)
    assert res.status_code == 200 and res.json() == [3, 1, 2]  # bez powtórzeń
    assert app_client.get("/api/order/album", headers=admin).json() == [3, 1, 2]
    app_client.put("/api/order/album", json=[2, 3], headers=admin)
    assert app_client.get("/api/order/album", headers=admin).json() == [2, 3]
    assert app_client.get("/api/order/artist", headers=admin).json() == []
    assert app_client.get("/api/order/song", headers=admin).status_code == 422
    assert app_client.get("/api/order/album").status_code == 401


def test_genre_order_names(app_client, admin):
    assert app_client.put("/api/order/genre", json=["Rock", "Pop", "Rock"], headers=admin).json() == ["Rock", "Pop"]
    assert app_client.put("/api/order/album", json=["Rock"], headers=admin).status_code == 422


def test_tracks_custom_sort(app_client, admin, scanned):
    items = app_client.get("/api/tracks", params={"sort": "title"}, headers=admin).json()["items"]
    ids = [t["id"] for t in items]
    assert len(ids) >= 3
    # Ostatni na początek, reszta po tytule.
    app_client.put("/api/order/track", json=[ids[-1]], headers=admin)
    got = app_client.get("/api/tracks", params={"sort": "custom"}, headers=admin).json()
    assert [t["id"] for t in got["items"]] == [ids[-1], *ids[:-1]] and got["total"] == len(ids)
    page = app_client.get("/api/tracks", params={"sort": "custom", "offset": 1, "limit": 1}, headers=admin).json()
    assert [t["id"] for t in page["items"]] == [ids[0]]


def test_home_layout_in_cloud(app_client, admin):
    """Układ strony głównej – ten sam na każdym urządzeniu (komputer, TV)."""
    assert app_client.get("/api/order/home", headers=admin).json() == []
    layout = ["albums:grid:1", "playlists:compact:1", "mix:row:0"]
    assert app_client.put("/api/order/home", json=layout, headers=admin).json() == layout
    assert app_client.get("/api/order/home", headers=admin).json() == layout
