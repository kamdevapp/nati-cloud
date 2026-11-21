"""Import ze Spotify i podpowiedzi – bez sieci (atrapy Spotify, YouTube Music i yt-dlp)."""

import json
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.api import extras
from app.services import online_search, spotify

from .test_online import online, wait_jobs  # noqa: F401  (fikstura)


def _page(tracks, kind="playlist", name="Moja lista"):
    entity = {"type": kind, "name": name, "subtitle": "ala",
              "coverArt": {"sources": [{"url": "https://img/spotify"}]},
              "trackList": [{"title": t, "subtitle": a, "duration": 2000, "entityType": "track"} for t, a in tracks]}
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


def test_spotify_links_and_page():
    assert spotify.parse_link("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc") == (
        "playlist", "37i9dQZF1DXcBWIGoYBM5M")
    assert spotify.parse_link("https://open.spotify.com/intl-pl/album/4LH4d3cOWNNsVw41Gqt2kv")[0] == "album"
    assert spotify.parse_link("spotify:playlist:37i9dQZF1DXcBWIGoYBM5M")[0] == "playlist"
    try:
        spotify.parse_link("https://example.com/x")
        raise AssertionError("powinien być błąd")
    except spotify.SpotifyError:
        pass
    page = spotify.parse_page(_page([("Śpiewaj", "Nati, Gość")]))
    assert page["name"] == "Moja lista" and page["image"] == "https://img/spotify"
    assert page["tracks"] == [{"title": "Śpiewaj", "artist": "Nati, Gość", "duration": 2.0}]


def test_title_matching_ignores_extras():
    assert spotify.title_key("Śpiewaj - Remastered 2011") == "spiewaj"
    assert spotify.title_key("Śpiewaj (feat. Gość) [Live]") == "spiewaj"
    assert spotify.artist_keys("Nati, Gość & Ktoś") == {"nati", "gosc", "ktos"}
    assert ("spiewaj", "nati") in spotify.match_key("Śpiewaj", "Nati feat. Gość")


def test_spotify_import_creates_playlist(app_client, admin, scanned, monkeypatch):
    monkeypatch.setattr(spotify, "fetch", lambda url: spotify.parse_page(_page(
        [("Pierwsza - Remastered", "Zespół"), ("Nieznana", "Ktoś"), ("ŚPIEWAJ", "Nati")])))
    body = {"url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}
    preview = app_client.post("/api/import/spotify/preview", json=body, headers=admin).json()
    assert preview["name"] == "Moja lista" and preview["matched"] == 2
    assert [bool(t["track_id"]) for t in preview["tracks"]] == [True, False, True]

    res = app_client.post("/api/import/spotify", json=body, headers=admin)
    assert res.status_code == 201, res.text
    out = res.json()
    assert (out["matched"], out["missing"], out["queued"]) == (2, 1, 0)
    detail = app_client.get(f"/api/playlists/{out['playlist']['id']}", headers=admin).json()
    assert detail["name"] == "Moja lista"
    assert [t["title"] for t in detail["tracks"]] == ["Pierwsza", "Śpiewaj"]


def test_spotify_import_downloads_missing(app_client, admin, scanned, online, monkeypatch):  # noqa: F811
    monkeypatch.setattr(spotify, "fetch", lambda url: spotify.parse_page(_page(
        [("Pierwsza", "Zespół"), ("Nowa piosenka", "Nowy Zespół")])))
    monkeypatch.setattr(online_search, "search", lambda q, limit=20: {"albums": [], "tracks": [
        {"id": "vidNew", "title": "Nowa piosenka", "artist": "Nowy Zespół", "album": "Nowy album",
         "thumbnail": "https://img/cover"}]})
    res = app_client.post("/api/import/spotify", headers=admin, json={
        "url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M", "download_missing": True, "name": "Import"})
    out = res.json()
    assert (out["matched"], out["missing"], out["queued"]) == (1, 1, 1), out
    jobs = wait_jobs(app_client, admin)
    assert [j["status"] for j in jobs] == ["done"], jobs
    detail = app_client.get(f"/api/playlists/{out['playlist']['id']}", headers=admin).json()
    assert detail["name"] == "Import"
    assert [t["title"] for t in detail["tracks"]] == ["Pierwsza", "Nowa piosenka"]


def test_spotify_bad_link(app_client, admin):
    res = app_client.post("/api/import/spotify/preview", json={"url": "https://example.com/abcdef"}, headers=admin)
    assert res.status_code == 422


def _track(i, artist, genres=()):
    return SimpleNamespace(id=i, album=SimpleNamespace(artist=SimpleNamespace(name=artist)),
                           genres=[SimpleNamespace(name=g) for g in genres])


def test_mix_prefers_what_you_listen_to():
    now = datetime(2026, 11, 16, tzinfo=timezone.utc)
    tracks = [_track(i, "Lubiany", ["Rock"]) for i in range(1, 11)]
    tracks += [_track(100 + i, f"Inny {i}", ["Jazz"]) for i in range(40)]
    plays = [(1, now - timedelta(days=10)), (2, now - timedelta(days=12)), (3, now - timedelta(hours=3))]
    mix, personal = extras.build_mix(tracks, plays, {4}, size=10, now=now, rng=random.Random(1))
    assert personal and len(mix) == 10
    liked = [t for t in mix if t.album.artist.name == "Lubiany"]
    assert len(liked) == 4  # najwyżej 4 utwory jednego wykonawcy
    assert 3 not in {t.id for t in mix}  # grany przed chwilą – odpoczywa

    mix, personal = extras.build_mix(tracks, [], set(), size=10, now=now, rng=random.Random(1))
    assert not personal and len(mix) == 10


def test_recommendations_endpoint(app_client, admin, scanned):
    rec = app_client.get("/api/recommendations", headers=admin).json()
    assert not rec["personal"] and len(rec["mix"]) == 3 and rec["forgotten"] == []
    track = rec["mix"][0]
    app_client.put(f"/api/favorites/{track['id']}", headers=admin)
    rec = app_client.get("/api/recommendations", headers=admin).json()
    assert rec["personal"] and len(rec["mix"]) == 3


def test_online_recommendations(app_client, admin, scanned, monkeypatch):
    assert app_client.get("/api/online/recommendations", headers=admin).json() == []  # nic jeszcze nie słuchano
    track = app_client.get("/api/tracks", params={"q": "pierwsza"}, headers=admin).json()["items"][0]
    app_client.put(f"/api/favorites/{track['id']}", headers=admin)
    monkeypatch.setattr(online_search, "search", lambda q, limit=20: {"albums": [], "tracks": [
        {"id": "seed", "title": "Pierwsza", "artist": "Zespół"}]})
    monkeypatch.setattr(online_search, "related", lambda vid: [
        {"id": "a", "title": "Druga", "artist": "Zespół", "duration": 100},  # już w bibliotece
        {"id": "b", "title": "Coś nowego", "artist": "Ktoś inny", "duration": 200}])
    out = app_client.get("/api/online/recommendations", headers=admin).json()
    assert [t["id"] for t in out] == ["b"]


def test_favorites_order_and_look(app_client, admin, scanned, tmp_path):
    from .conftest import make_image

    ids = [t["id"] for t in app_client.get("/api/tracks", params={"sort": "album"}, headers=admin).json()["items"]]
    a, b, c = ids[:3]
    for t in (a, b):
        assert app_client.put(f"/api/favorites/{t}", headers=admin).status_code == 204

    def order():
        return [t["id"] for t in app_client.get("/api/favorites", headers=admin).json()]

    assert order() == [b, a]  # bez ręcznej kolejności – ostatnio dodane pierwsze
    assert app_client.put("/api/favorites/order", json=[a, b], headers=admin).status_code == 204
    assert order() == [a, b]
    app_client.put(f"/api/favorites/{c}", headers=admin)
    assert order() == [c, a, b]  # nowy – na początek
    app_client.delete(f"/api/favorites/{a}", headers=admin)
    assert order() == [c, b]

    # Okładka „Ulubionych”: ikona i kolory, obrazek.
    look = app_client.put("/api/favorites/look", json={"icon": "star", "color1": "#112233", "color2": "#445566"},
                          headers=admin).json()
    assert (look["icon"], look["color1"], look["image_id"]) == ("star", "#112233", None)
    assert app_client.put("/api/favorites/look", json={"color1": "zielony"}, headers=admin).status_code == 422
    img = make_image(tmp_path / "fav.png").read_bytes()
    look = app_client.put("/api/favorites/image", content=img, headers=admin).json()
    assert look["image_id"] and look["icon"] == "star"
    res = app_client.get("/api/favorites/image", headers=admin)
    assert res.status_code == 200 and res.content == img
    look = app_client.delete("/api/favorites/image", headers=admin).json()
    assert look["image_id"] is None
    assert app_client.get("/api/favorites/image", headers=admin).status_code == 404
