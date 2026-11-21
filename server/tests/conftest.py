"""Wspólne fikstury: tymczasowa biblioteka z prawdziwymi plikami audio (ffmpeg)."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FFMPEG = shutil.which("ffmpeg")


def make_audio(path: Path, *, title, artist, album, track, year="2024", album_artist=None,
               seconds=2, cover: Path | None = None, genre: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if cover:
        cmd += ["-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
    meta = {"title": title, "artist": artist, "album": album, "track": track, "date": year}
    if album_artist:
        meta["album_artist"] = album_artist
    if genre:
        meta["genre"] = genre
    for k, v in meta.items():
        cmd += ["-metadata", f"{k}={v}"]
    if path.suffix == ".mp3":
        cmd += ["-id3v2_version", "3"]
    subprocess.run([*cmd, str(path)], check=True)
    return path


def make_image(path: Path, color="red") -> Path:
    subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color={color}:size=64x64",
                    "-frames:v", "1", str(path)], check=True)
    return path


@pytest.fixture(scope="session")
def audio_assets(tmp_path_factory) -> Path:
    if not FFMPEG:
        pytest.skip("ffmpeg nie jest zainstalowany")
    root = tmp_path_factory.mktemp("assets")
    cover = make_image(root / "cover.jpg")
    lib = root / "lib"
    # Album 1: MP3 z osadzoną okładką
    make_audio(lib / "Zespół" / "Pierwszy" / "02.mp3", title="Druga", artist="Zespół", album="Pierwszy",
               track="2/2", cover=cover, genre="Rock")
    make_audio(lib / "Zespół" / "Pierwszy" / "01.mp3", title="Pierwsza", artist="Zespół", album="Pierwszy",
               track="1/2", cover=cover, genre="Rock/Electro")
    # Album 2: FLAC z okładką w folderze i gościnnym wykonawcą
    make_audio(lib / "Nati" / "Koncert" / "01.flac", title="Śpiewaj", artist="Nati feat. Gość",
               album="Koncert", album_artist="Nati", track="1", year="2026", genre="Pop; Poezja śpiewana")
    make_image(lib / "Nati" / "Koncert" / "folder.jpg", color="blue")
    return lib


@pytest.fixture
def music_dir(audio_assets, tmp_path) -> Path:
    d = tmp_path / "music"
    shutil.copytree(audio_assets, d)
    return d


@pytest.fixture
def app_client(music_dir, tmp_path, monkeypatch):
    from app.services.login_guard import guard
    guard._fails.clear()  # liczniki nieudanych logowań z innych testów
    monkeypatch.setenv("NATI_MUSIC_DIRS", str(music_dir))
    monkeypatch.setenv("NATI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("NATI_SCAN_ON_STARTUP", "false")
    monkeypatch.setenv("NATI_YTDLP_AUTO_UPDATE", "false")  # testy nie aktualizują yt-dlp z internetu
    monkeypatch.setenv("NATI_MUSICBRAINZ", "false")
    monkeypatch.setenv("NATI_ENRICH", "false")
    monkeypatch.setenv("NATI_DOWNLOAD_PAUSE", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


@pytest.fixture
def admin(app_client) -> dict:
    res = app_client.post("/api/setup", json={"username": "ala", "password": "tajne123"})
    assert res.status_code == 201, res.text
    return {"Authorization": "Bearer " + res.json()["access_token"]}


@pytest.fixture
def scanned(app_client, admin):
    status = app_client.app.state.scanner.scan()
    assert not status.errors, status.errors
    return status
