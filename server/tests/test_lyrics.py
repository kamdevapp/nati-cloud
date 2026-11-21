"""Teksty utworów: chmura, plik .lrc obok utworu, pobieranie z internetu."""

import httpx
import pytest

from app.services import lyrics

LRC = "[00:01.00] Pierwsza linijka\n[00:05.50] Druga linijka\n"


def _track(client, headers, q):
    [t] = client.get("/api/tracks", params={"q": q}, headers=headers).json()["items"]
    return t


@pytest.fixture
def online(monkeypatch):
    """Podmienia LRCLIB: lista wywołań i odpowiedź do ustawienia w teście."""
    state = {"calls": [], "result": None, "error": None}

    async def fake(artist, title, album, duration):
        state["calls"].append((artist, title, album))
        if state["error"]:
            raise state["error"]
        return state["result"]

    monkeypatch.setattr(lyrics, "fetch", fake)
    return state


def test_format():
    assert lyrics.is_synced(LRC)
    assert not lyrics.is_synced("Zwykły tekst\n[refren]\nbez czasów")
    assert lyrics.clean("\r\n\r\nA  \r\nB\r\n\r\n") == "A\nB"


def test_pick_prefers_synced_with_matching_duration():
    results = [
        {"artistName": "Zespół", "trackName": "Pierwsza", "duration": 200, "plainLyrics": "zwykły", "syncedLyrics": None},
        {"artistName": "Zespół", "trackName": "Pierwsza", "duration": 181, "plainLyrics": "p", "syncedLyrics": LRC},
        {"artistName": "Ktoś inny", "trackName": "Pierwsza", "duration": 180, "syncedLyrics": "[00:01.00] nie to\n[00:02.00] x"},
    ]
    found = lyrics.pick(results, "Zespół", "Pierwsza", 180)
    assert found.synced and "Pierwsza linijka" in found.text
    # Inna długość nagrania: czasy by się rozjechały – bierzemy zwykły tekst.
    found = lyrics.pick(results[1:2], "Zespół", "Pierwsza", 240)
    assert found is not None and not found.synced and found.text == "p"
    assert lyrics.pick(results[2:], "Zespół", "Pierwsza", 180) is None


def test_save_edit_delete(app_client, admin, scanned, online):
    t = _track(app_client, admin, "pierwsza")
    url = f"/api/tracks/{t['id']}/lyrics"
    assert app_client.get(url, headers=admin).status_code == 404
    assert online["calls"], "brak tekstu → szukamy w internecie"

    res = app_client.put(url, json={"text": LRC}, headers=admin)
    assert res.status_code == 200, res.text
    assert res.json()["synced"] is True and res.json()["source"] == "user"
    got = app_client.get(url, headers=admin).json()
    assert got["text"] == LRC.strip() and got["synced"] is True

    res = app_client.put(url, json={"text": "Tylko słowa\nbez czasów"}, headers=admin)
    assert res.json()["synced"] is False
    assert app_client.put(url, json={"text": "  \n "}, headers=admin).status_code == 422

    # Tekst przetrwa ponowny skan biblioteki (jest przypisany do pliku).
    app_client.app.state.scanner.scan()
    assert app_client.get(url, headers=admin).json()["text"].startswith("Tylko słowa")

    assert app_client.delete(url, headers=admin).status_code == 204
    online["calls"].clear()
    online["result"] = lyrics.Found("z internetu", False)
    assert app_client.get(url, headers=admin).status_code == 404
    assert not online["calls"], "usunięty tekst nie wraca sam z internetu"
    assert app_client.get("/api/tracks/999999/lyrics", headers=admin).status_code == 404
    assert app_client.get(url).status_code == 401


def test_auto_fetch_and_cache(app_client, admin, scanned, online):
    t = _track(app_client, admin, "druga")
    url = f"/api/tracks/{t['id']}/lyrics"
    online["result"] = lyrics.Found(LRC.strip(), True)
    got = app_client.get(url, headers=admin).json()
    assert got["source"] == "lrclib" and got["synced"] is True
    assert online["calls"] == [("Zespół", "Druga", "Pierwszy")]
    app_client.get(url, headers=admin)
    assert len(online["calls"]) == 1, "zapisany tekst nie jest pobierany ponownie"

    # Nie znaleziono: zapamiętujemy i nie pytamy przy każdym otwarciu; ?auto=false nie pyta wcale.
    other = _track(app_client, admin, "śpiewaj")
    other_url = f"/api/tracks/{other['id']}/lyrics"
    online["result"] = None
    assert app_client.get(other_url, params={"auto": "false"}, headers=admin).status_code == 404
    assert len(online["calls"]) == 1
    assert app_client.get(other_url, headers=admin).status_code == 404
    assert app_client.get(other_url, headers=admin).status_code == 404
    assert len(online["calls"]) == 2


def test_manual_fetch(app_client, admin, scanned, online):
    t = _track(app_client, admin, "pierwsza")
    url = f"/api/tracks/{t['id']}/lyrics"
    app_client.put(url, json={"text": "moje"}, headers=admin)
    # Nic w internecie – własny tekst zostaje.
    assert app_client.post(url + "/fetch", headers=admin).status_code == 404
    assert app_client.get(url, headers=admin).json()["text"] == "moje"
    online["error"] = httpx.ConnectError("brak sieci")
    assert app_client.post(url + "/fetch", headers=admin).status_code == 502
    online["error"] = None
    online["result"] = lyrics.Found(LRC.strip(), True)
    res = app_client.post(url + "/fetch", headers=admin)
    assert res.status_code == 200 and res.json()["source"] == "lrclib"
    assert app_client.get(url, headers=admin).json()["synced"] is True


def test_sidecar_file(app_client, admin, scanned, music_dir, online):
    (music_dir / "Zespół" / "Pierwszy" / "01.lrc").write_text(LRC, encoding="utf-8")
    t = _track(app_client, admin, "pierwsza")
    url = f"/api/tracks/{t['id']}/lyrics"
    got = app_client.get(url, headers=admin).json()
    assert got["source"] == "file" and got["synced"] is True
    assert not online["calls"], "plik obok utworu ma pierwszeństwo przed internetem"
    # Tekst zapisany w chmurze ma pierwszeństwo przed plikiem.
    app_client.put(url, json={"text": "z chmury"}, headers=admin)
    assert app_client.get(url, headers=admin).json()["text"] == "z chmury"
