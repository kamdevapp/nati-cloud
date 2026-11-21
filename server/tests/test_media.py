def _first_track(client, headers):
    return client.get("/api/tracks", headers=headers).json()["items"][0]


def test_stream_full_and_range(app_client, admin, scanned):
    t = _first_track(app_client, admin)
    full = app_client.get(f"/api/stream/{t['id']}", headers=admin)
    assert full.status_code == 200
    assert len(full.content) == t["size"]
    assert full.headers["accept-ranges"] == "bytes"

    part = app_client.get(f"/api/stream/{t['id']}", headers={**admin, "Range": "bytes=100-199"})
    assert part.status_code == 206
    assert part.content == full.content[100:200]


def test_stream_with_query_token(app_client, admin, scanned):
    t = _first_track(app_client, admin)
    token = admin["Authorization"].split()[1]
    assert app_client.get(f"/api/stream/{t['id']}", params={"token": token}).status_code == 200
    assert app_client.get(f"/api/stream/{t['id']}").status_code == 401


def test_stream_gone_file(app_client, admin, scanned, music_dir):
    t = _first_track(app_client, admin)
    for f in music_dir.rglob("*.*"):
        f.unlink()
    assert app_client.get(f"/api/stream/{t['id']}", headers=admin).status_code == 410


def test_cover(app_client, admin, scanned):
    album = app_client.get("/api/albums", headers=admin).json()["items"][0]
    res = app_client.get(f"/api/albums/{album['id']}/cover", headers=admin)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/")
    assert res.content[:2] == b"\xff\xd8"  # JPEG


def test_transcode_to_opus(app_client, admin, scanned):
    t = _first_track(app_client, admin)
    assert t["bitrate"] > 40
    res = app_client.get(f"/api/stream/{t['id']}", params={"bitrate": 32}, headers=admin)
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/ogg"
    assert res.headers["x-nati-transcoded"] == "opus/32"
    assert res.content[:4] == b"OggS"
    assert 0 < len(res.content)


def test_no_transcode_when_original_is_small_enough(app_client, admin, scanned):
    t = _first_track(app_client, admin)
    res = app_client.get(f"/api/stream/{t['id']}", params={"bitrate": 320}, headers=admin)
    assert "x-nati-transcoded" not in res.headers
    assert len(res.content) == t["size"]


def test_bitrate_validation(app_client, admin, scanned):
    t = _first_track(app_client, admin)
    assert app_client.get(f"/api/stream/{t['id']}", params={"bitrate": 5}, headers=admin).status_code == 422


def test_token_is_redacted_in_access_log():
    import logging

    from app.main import _RedactTokens

    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', (
        "127.0.0.1:1", "GET", "/api/stream/1?token=eyJsekret.abc&x=1", "1.1", 200), None)
    _RedactTokens().filter(rec)
    assert "eyJsekret" not in rec.getMessage()
    assert "token=***&x=1" in rec.getMessage()
