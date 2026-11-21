"""Pobieranie z internetu do chmury – z atrapą yt-dlp i YouTube Music (bez sieci)."""

import asyncio
import subprocess
import time
from pathlib import Path

import mutagen
import pytest

from app.services import downloader, online_search
from app.services.ytdlp import DownloadResult, YtDlp, YtDlpError, _friendly, is_blocked, needs_login

from .conftest import FFMPEG, make_image

ALBUM = {
    "id": "MPREb_test", "title": "Koncert", "artist": "Nati", "year": 2026, "thumbnail": "https://img/cover",
    "tracks": [
        {"id": "vid1", "title": "Śpiewaj", "artist": "Nati", "album": "Koncert", "album_id": "MPREb_test",
         "track_no": 1, "duration": 2, "thumbnail": "https://img/cover"},
        {"id": "vid2", "title": "Do/Re:Mi?", "artist": "Nati", "album": "Koncert", "album_id": "MPREb_test",
         "track_no": 2, "duration": 2, "thumbnail": "https://img/cover"},
    ],
}


class FakeYtDlp(YtDlp):
    """Zamiast YouTube: generuje 1 s dźwięku w Opus."""

    def __init__(self):
        super().__init__(Path("/nonexistent"))
        self.calls: list[str] = []
        self.cookie_calls: list[bool] = []
        self.fail_next: str | None = None
        self.delay = 0.0

    async def version(self):
        return "test"

    async def download(self, url, workdir, on_progress, cookies=False):
        self.calls.append(url)
        self.cookie_calls.append(cookies)
        if self.fail_next:
            msg, self.fail_next = self.fail_next, None
            code, params = _friendly(msg)
            raise YtDlpError(code, blocked=is_blocked(msg), needs_login=needs_login(msg), **params)
        on_progress(0.5)
        await asyncio.sleep(self.delay)
        workdir.mkdir(parents=True, exist_ok=True)
        out = workdir / "audio.opus"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1",
                        "-c:a", "libopus", str(out)], check=True)
        on_progress(1.0)
        return DownloadResult(out, {"title": "Z linku", "uploader": "Kanał", "upload_date": "20240501"})


@pytest.fixture
def online(app_client, admin, monkeypatch, tmp_path):
    fake = FakeYtDlp()
    app_client.app.state.downloads.ytdlp = fake
    cover = make_image(tmp_path / "thumb.jpg").read_bytes()

    async def fetch(url):
        return cover

    monkeypatch.setattr(downloader, "_fetch", fetch)
    monkeypatch.setattr(online_search, "album", lambda browse_id: ALBUM)
    monkeypatch.setattr(online_search, "search", lambda q, limit=20, kind="all": {"tracks": ALBUM["tracks"], "albums": [
        {k: ALBUM[k] for k in ("id", "title", "artist", "year", "thumbnail")}]})
    return fake


def wait_jobs(client, headers, until=lambda jobs: all(j["status"] not in ("queued", "downloading", "processing")
                                                    for j in jobs), timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        jobs = client.get("/api/online/downloads", headers=headers).json()
        if jobs and until(jobs):
            return jobs
        time.sleep(0.1)
    raise AssertionError(f"Zadania nie skończyły się: {jobs}")


def test_search(app_client, admin, online):
    res = app_client.get("/api/online/search", params={"q": "nati"}, headers=admin).json()
    assert res["tracks"][0]["title"] == "Śpiewaj"
    assert res["albums"][0]["id"] == "MPREb_test"
    assert app_client.get("/api/online/search", params={"q": "nati"}).status_code == 401


def test_download_album_to_cloud(app_client, admin, online, tmp_path):
    res = app_client.post("/api/online/downloads", json={"kind": "album", "album_id": "MPREb_test"}, headers=admin)
    assert res.status_code == 202
    assert [j["status"] for j in res.json()] == ["queued", "queued"]

    jobs = wait_jobs(app_client, admin)
    assert {j["status"] for j in jobs} == {"done"}, jobs
    assert all(j["track_id"] for j in jobs)
    assert online.calls == ["https://music.youtube.com/watch?v=vid1", "https://music.youtube.com/watch?v=vid2"] \
        or sorted(online.calls) == sorted(["https://music.youtube.com/watch?v=vid1",
                                           "https://music.youtube.com/watch?v=vid2"])

    # W bibliotece – jak każdy inny album, z okładką.
    albums = app_client.get("/api/albums", params={"q": "koncert"}, headers=admin).json()["items"]
    assert len(albums) == 1 and albums[0]["artist_name"] == "Nati" and albums[0]["year"] == 2026
    assert albums[0]["has_cover"]
    detail = app_client.get(f"/api/albums/{albums[0]['id']}", headers=admin).json()
    assert [t["title"] for t in detail["tracks"]] == ["Śpiewaj", "Do/Re:Mi?"]
    assert detail["tracks"][0]["format"] == "opus"

    # Plik w osobnym folderze, z bezpieczną nazwą i tagami.
    online_dir = tmp_path / "data" / "downloads"
    files = sorted(p.relative_to(online_dir).as_posix() for p in online_dir.rglob("*.opus"))
    assert files == ["Nati/Koncert/01 - Śpiewaj.opus", "Nati/Koncert/02 - Do_Re_Mi_.opus"]
    tags = mutagen.File(online_dir / files[0])
    assert tags["album"] == ["Koncert"] and tags["tracknumber"] == ["1"]
    assert tags["metadata_block_picture"], "okładka osadzona w pliku"

    # Utwór gra jak każdy inny.
    assert app_client.get(f"/api/stream/{jobs[0]['track_id']}", headers=admin).status_code == 200


def test_same_track_is_not_downloaded_twice(app_client, admin, online):
    body = {"kind": "track", "track": ALBUM["tracks"][0]}
    first = app_client.post("/api/online/downloads", json=body, headers=admin).json()[0]
    wait_jobs(app_client, admin)
    again = app_client.post("/api/online/downloads", json=body, headers=admin).json()[0]
    assert again["id"] == first["id"] and again["status"] == "done"
    assert len(online.calls) == 1


def test_link_from_other_site(app_client, admin, online):
    res = app_client.post("/api/online/downloads", json={"kind": "url", "url": "https://soundcloud.com/x/y"},
                          headers=admin)
    assert res.status_code == 202
    job = wait_jobs(app_client, admin)[0]
    assert job["status"] == "done", job
    assert job["title"] == "Z linku" and job["artist"] == "Kanał"
    assert app_client.post("/api/online/downloads", json={"kind": "url", "url": "ftp://x"},
                           headers=admin).status_code == 422


def test_error_and_retry(app_client, admin, online):
    online.fail_next = "ERROR: [youtube] abc: Video unavailable"
    app_client.post("/api/online/downloads", json={"kind": "track", "track": ALBUM["tracks"][0]}, headers=admin)
    job = wait_jobs(app_client, admin)[0]
    assert job["status"] == "error" and job["error_code"] == "ytdlp.unavailable" and "unavailable" in job["error"]

    assert app_client.post(f"/api/online/downloads/{job['id']}/retry", headers=admin).json()["status"] == "queued"
    assert wait_jobs(app_client, admin)[0]["status"] == "done"


def test_cancel(app_client, admin, online):
    online.delay = 30
    job = app_client.post("/api/online/downloads", json={"kind": "track", "track": ALBUM["tracks"][0]},
                          headers=admin).json()[0]
    wait_jobs(app_client, admin, until=lambda jobs: jobs[0]["status"] == "downloading")
    assert app_client.delete(f"/api/online/downloads/{job['id']}", headers=admin).status_code == 204
    assert app_client.get("/api/online/downloads", headers=admin).json() == []
    assert app_client.get("/api/tracks", headers=admin).json()["total"] == 0


def test_only_admin_adds_music_to_cloud(app_client, admin, online):
    app_client.post("/api/users", json={"username": "nati", "password": "spiewam1"}, headers=admin)
    tok = app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).json()["access_token"]
    nati = {"Authorization": "Bearer " + tok}
    assert app_client.post("/api/online/downloads", json={"kind": "track", "track": ALBUM["tracks"][1]},
                           headers=nati).status_code == 403
    # Szukać i słuchać z internetu może.
    assert app_client.get("/api/online/search", params={"q": "x"}, headers=nati).status_code == 200
    assert app_client.get("/api/online/downloads", headers=nati).json() == []


def test_disabled(app_client, admin, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "online_enabled", False)
    assert app_client.get("/api/online/search", params={"q": "x"}, headers=admin).status_code == 403


@pytest.mark.skipif(not __import__("os").environ.get("NATI_NETWORK_TESTS"), reason="wymaga internetu (NATI_NETWORK_TESTS=1)")
def test_real_ytdlp_update(tmp_path):
    """Prawdziwa aktualizacja yt-dlp: nowa wersja w osobnym folderze, przełączenie dowiązania."""
    y = YtDlp(tmp_path / "tools")
    version = asyncio.run(y.update())
    assert version[:4].isdigit()
    assert (tmp_path / "tools" / "current").is_symlink()
    assert "current" in y._env()["PYTHONPATH"] or "releases" in y._env()["PYTHONPATH"]
    assert not y.update_due()
    asyncio.run(y.update())  # druga aktualizacja usuwa poprzednią wersję
    assert len(list((tmp_path / "tools" / "releases").iterdir())) == 1


class FakePreviewYtDlp(YtDlp):
    """Plik do odsłuchu „pobierany” po kawałku – sprawdza czytanie w trakcie pobierania."""

    def __init__(self, data: bytes):
        super().__init__(Path("/nonexistent"))
        self.data = data
        self.calls = 0

    async def fetch_audio(self, url, workdir, on_info, fmt=None):
        self.calls += 1
        workdir.mkdir(parents=True, exist_ok=True)
        on_info("webm", len(self.data))
        with open(workdir / "audio.webm", "wb") as f:
            for i in range(0, len(self.data), 1000):
                f.write(self.data[i:i + 1000])
                f.flush()
                await asyncio.sleep(0.01)


def test_preview_supports_seeking_and_cache(app_client, admin):
    data = bytes(range(256)) * 40  # 10 240 B
    fake = FakePreviewYtDlp(data)
    app_client.app.state.preview.ytdlp = fake
    res = app_client.get("/api/online/preview/abcdefghijk", headers={**admin, "Range": "bytes=5000-5099"})
    assert res.status_code == 206, res.text
    assert res.headers["content-range"] == "bytes 5000-5099/10240"
    assert res.content == data[5000:5100]
    full = app_client.get("/api/online/preview/abcdefghijk", headers=admin)
    assert full.status_code == 200 and full.content == data
    # Drugi raz – z pamięci podręcznej, bez yt-dlp.
    again = app_client.get("/api/online/preview/abcdefghijk", headers={**admin, "Range": "bytes=10000-"})
    assert again.status_code == 206 and again.content == data[10000:]
    assert fake.calls == 1
    assert app_client.get("/api/online/preview/..%2Fetc", headers=admin).status_code == 404


def test_link_tags_from_musicbrainz(app_client, admin, online, monkeypatch):
    from app.services import musicbrainz

    manager = app_client.app.state.downloads
    manager.use_musicbrainz = True
    looked_up = []

    async def lookup(title, uploader):
        looked_up.append((title, uploader))
        return musicbrainz.Match("Śpiewaj", "Nati", "Debiut", "Nati", 2024, 3, None)

    monkeypatch.setattr(musicbrainz, "lookup", lookup)
    app_client.post("/api/online/downloads", json={"kind": "url", "url": "https://youtu.be/abc"}, headers=admin)
    job = wait_jobs(app_client, admin)[0]
    assert job["status"] == "done", job
    assert (job["title"], job["artist"], job["album"]) == ("Śpiewaj", "Nati", "Debiut")
    assert looked_up == [("Z linku", "Kanał")]
    t = app_client.get(f"/api/tracks/{job['track_id']}", headers=admin).json()
    assert (t["track_no"], t["year"], t["album_artist"]) == (3, 2024, "Nati")


def test_link_without_musicbrainz_match(app_client, admin, online, monkeypatch):
    from app.services import musicbrainz

    app_client.app.state.downloads.use_musicbrainz = True

    async def no_match(title, uploader):
        return None

    monkeypatch.setattr(musicbrainz, "lookup", no_match)
    infos = [
        {"title": "Nati - Śpiewaj (Official Video)", "uploader": "Wytwórnia", "categories": ["Music"]},
        {"title": "Film - Official Short Film (HD)", "uploader": "Studio", "categories": ["Film & Animation"]},
    ]
    original = online.download

    async def download(url, workdir, on_progress):
        result = await original(url, workdir, on_progress)
        result.info.clear()
        result.info.update(infos.pop(0))
        return result

    online.download = download
    for url in ("https://youtu.be/m1", "https://youtu.be/f1"):
        app_client.post("/api/online/downloads", json={"kind": "url", "url": url}, headers=admin)
        wait_jobs(app_client, admin)
    jobs = {j["source_id"]: j for j in app_client.get("/api/online/downloads", headers=admin).json()}
    assert (jobs["https://youtu.be/m1"]["artist"], jobs["https://youtu.be/m1"]["title"]) == ("Nati", "Śpiewaj")
    # Nie-muzyka: tytuł zostaje (bez dopisków w nawiasach), wykonawca = kanał.
    assert (jobs["https://youtu.be/f1"]["artist"], jobs["https://youtu.be/f1"]["title"]) == (
        "Studio", "Film - Official Short Film")


def test_video_and_clip(app_client, admin, monkeypatch):
    data = b"\x1aE\xdf\xa3" + bytes(3000)
    fake = FakePreviewYtDlp(data)
    app_client.app.state.video.ytdlp = fake
    res = app_client.get("/api/online/video/abcdefghijk", headers=admin)
    assert res.status_code == 200 and res.headers["content-type"] == "video/webm" and res.content == data
    from app.api import online as online_api
    online_api._find_clip.cache_clear()
    monkeypatch.setattr(online_search, "find_clip", lambda a, t, v: {"video_id": "X2XWBcd5jn0", "title": t}
                        if t == "Utwór" else None)
    assert app_client.get("/api/online/clip", params={"artist": "Nati", "title": "Utwór"},
                          headers=admin).json()["video_id"] == "X2XWBcd5jn0"
    assert app_client.get("/api/online/clip", params={"artist": "Nati", "title": "Brak"},
                          headers=admin).status_code == 404


def test_album_tracks_share_genre(app_client, admin, online):
    """Utwory albumu pobierane po kolei dostają gatunek, który ma już reszta albumu w chmurze."""
    from sqlalchemy import select

    from app import db as database
    from app.db.models import Artist

    first = {"kind": "track", "track": ALBUM["tracks"][0] | {"album": "Koncert"}}
    app_client.post("/api/online/downloads", json=first, headers=admin)
    jobs = wait_jobs(app_client, admin)
    track_id = jobs[0]["track_id"]
    # Utwór bez gatunku → wykonawca do ponownego uzupełnienia z internetu.
    with database.new_session() as db:
        assert db.scalar(select(Artist.enriched_at).where(Artist.name == "Nati")) is None
    res = app_client.patch(f"/api/tracks/{track_id}", json={"genre": "Pop; Poezja śpiewana"}, headers=admin)
    assert res.status_code == 200, res.text

    second = {"kind": "track", "track": ALBUM["tracks"][1] | {"album": "Koncert"}}
    app_client.post("/api/online/downloads", json=second, headers=admin)
    jobs = wait_jobs(app_client, admin)
    tracks = app_client.get(f"/api/tracks/{jobs[0]['track_id']}", headers=admin).json()
    assert tracks["genres"] == ["Poezja śpiewana", "Pop"]


def test_enricher_copies_album_genre(app_client, admin, scanned):
    """Uzupełnianie: utwory bez gatunku dostają gatunek reszty albumu – bez pytania internetu."""
    from sqlalchemy import select

    from app import db as database
    from app.db.models import Album
    from app.services.enrich import album_genre

    album = app_client.get("/api/albums", params={"q": "pierwszy"}, headers=admin).json()["items"][0]
    with database.new_session() as db:
        assert album_genre(db, album["id"]) in ("Rock", "Electro; Rock")  # remis 1:1
        assert album_genre(db, db.scalar(select(Album.id).where(Album.title == "Koncert"))) == "Poezja śpiewana; Pop"


# ---------------------------------------------------------------- filtry, inne serwisy, wykonawcy

def test_search_kinds_and_sources(app_client, admin, online, monkeypatch):
    from app.services import other_sources

    asked = []
    monkeypatch.setattr(online_search, "search", lambda q, limit=20, kind="all": asked.append(kind) or {
        "tracks": [], "albums": [], "artists": [{"id": "UC1", "name": "Nati", "thumbnail": None}]})
    res = app_client.get("/api/online/search", params={"q": "nati", "kind": "artists"}, headers=admin).json()
    assert asked == ["artists"] and res["artists"][0]["name"] == "Nati"

    async def sc(ytdlp, q, limit):
        return [{"id": "https://soundcloud.com/a/b", "title": "B", "artist": "A", "source": "soundcloud"}]

    monkeypatch.setattr(other_sources, "search_soundcloud", sc)
    res = app_client.get("/api/online/search", params={"q": "x", "source": "soundcloud"}, headers=admin).json()
    assert res["tracks"][0]["source"] == "soundcloud" and res["albums"] == []
    # SoundCloud nie ma albumów – pusto, bez pytania serwisu.
    res = app_client.get("/api/online/search", params={"q": "x", "source": "soundcloud", "kind": "albums"},
                         headers=admin).json()
    assert res == {"tracks": [], "albums": [], "artists": []}
    assert app_client.get("/api/online/search", params={"q": "x", "source": "myspace"},
                          headers=admin).status_code == 422


def test_split_title():
    from app.services.other_sources import split_title

    assert split_title("KULT - Arahja [OFFICIAL VIDEO]", "S.P. RECORDS") == ("KULT", "Arahja")
    assert split_title("Arahja (Official Audio)", "Kult - Topic") == ("Kult", "Arahja")
    assert split_title("Coś", None) == ("Unknown artist", "Coś")


def test_other_source_track_downloads_as_link(app_client, admin, online):
    t = {"id": "https://soundcloud.com/a/b", "title": "B", "artist": "A", "source": "soundcloud"}
    job = app_client.post("/api/online/downloads", json={"kind": "track", "track": t}, headers=admin).json()[0]
    assert job["source"] == "url" and job["source_id"] == t["id"] and job["title"] == "A – B"
    yt = {"id": "dQw4w9WgXcQ", "title": "X", "artist": "Y", "source": "youtube"}
    job = app_client.post("/api/online/downloads", json={"kind": "track", "track": yt}, headers=admin).json()[0]
    assert job["source_id"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    wait_jobs(app_client, admin)
    assert sorted(online.calls) == sorted([t["id"], yt and "https://www.youtube.com/watch?v=dQw4w9WgXcQ"])


def test_bandcamp_album_from_page(monkeypatch):
    import html as html_lib
    import json as json_lib

    from app.services import other_sources

    data = {"artist": "Zespół", "art_id": 123, "album_release_date": "05 Jan 2023 00:00:00 GMT",
            "current": {"title": "Płyta"},
            "trackinfo": [{"title": "Jeden", "track_num": 1, "duration": 61.5, "title_link": "/track/jeden"},
                          {"title": "Ukryty", "track_num": 2, "title_link": None}]}
    page = f'<div data-tralbum="{html_lib.escape(json_lib.dumps(data))}"></div>'

    class R:
        text = page
        url = "https://zespol.bandcamp.com/album/plyta"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(other_sources.httpx, "get", lambda *a, **k: R())
    album = other_sources.bandcamp_album("https://zespol.bandcamp.com/album/plyta")
    assert (album["title"], album["artist"], album["year"]) == ("Płyta", "Zespół", 2023)
    assert album["thumbnail"] == "https://f4.bcbits.com/img/a123_10.jpg"
    assert [(t["id"], t["track_no"], t["duration"]) for t in album["tracks"]] == [
        ("https://zespol.bandcamp.com/track/jeden", 1, 61.5)]


def test_online_artist(app_client, admin, online, monkeypatch):
    page = {"id": "UC1", "name": "Nati", "thumbnail": None, "tracks": ALBUM["tracks"],
            "albums": [{k: ALBUM[k] for k in ("id", "title", "artist", "year", "thumbnail")}], "singles": []}
    monkeypatch.setattr(online_search, "artist", lambda browse_id: page)
    res = app_client.get("/api/online/artists/UC1", headers=admin).json()
    assert res["name"] == "Nati" and res["albums"][0]["id"] == "MPREb_test" and len(res["tracks"]) == 2


def test_preview_url_validation(app_client, admin):
    assert app_client.get("/api/online/preview-url", params={"url": "file:///etc/passwd"},
                          headers=admin).status_code == 422


# ---------------------------------------------------------------- ochrona przed blokadą YouTube

def test_blocked_youtube_pauses_queue(app_client, admin, online):
    manager = app_client.app.state.downloads
    online.fail_next = "ERROR: [youtube] abc: Sign in to confirm you're not a bot"
    app_client.post("/api/online/downloads", json={"kind": "track", "track": ALBUM["tracks"][0]}, headers=admin)
    end = time.time() + 10
    while not manager.blocked and time.time() < end:
        time.sleep(0.05)
    assert manager.blocked
    job = app_client.get("/api/online/downloads", headers=admin).json()[0]
    assert job["status"] == "queued" and job["error_code"] == "ytdlp.blocked"  # wraca do kolejki, nie jest błędem
    status_ = app_client.get("/api/online/status", headers=admin).json()
    assert status_["blocked_until"] and not status_["has_cookies"]

    assert app_client.post("/api/online/downloads/resume", headers=admin).status_code == 204
    job = wait_jobs(app_client, admin)[0]
    assert job["status"] == "done" and job["error"] is None


def test_cookies_used_after_block(app_client, admin, online, tmp_path):
    cookies = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    bad = app_client.put("/api/online/cookies", files={"file": ("c.txt", b"hello", "text/plain")}, headers=admin)
    assert bad.status_code == 422
    res = app_client.put("/api/online/cookies", files={"file": ("cookies.txt", cookies.encode(), "text/plain")},
                         headers=admin)
    assert res.status_code == 204
    online.cookies_file = app_client.app.state.downloads.ytdlp.cookies_file
    app_client.app.state.downloads.ytdlp = online
    assert app_client.get("/api/online/status", headers=admin).json()["has_cookies"]

    online.fail_next = "ERROR: HTTP Error 429: Too Many Requests"
    app_client.post("/api/online/downloads", json={"kind": "track", "track": ALBUM["tracks"][0]}, headers=admin)
    job = wait_jobs(app_client, admin)[0]
    assert job["status"] == "done", job
    assert online.cookie_calls == [False, True]  # druga próba – z ciasteczkami
    assert not app_client.app.state.downloads.blocked

    assert app_client.delete("/api/online/cookies", headers=admin).status_code == 204
    assert not app_client.get("/api/online/status", headers=admin).json()["has_cookies"]
