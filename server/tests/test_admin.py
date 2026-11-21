"""Administracja chmurą: miejsce, kopia zapasowa (utworzenie, pobranie, przywrócenie z przeprowadzką),
całkowite wyczyszczenie."""

import shutil
import time

import pytest

from app.config import get_settings
from app.services import maintenance


@pytest.fixture
def no_restart(monkeypatch):
    calls = []
    monkeypatch.setattr(maintenance, "restart_later", lambda *a, **k: calls.append(1))
    return calls


def _restart(client):
    """Jak ponowne uruchomienie serwera: zamknięcie i start (zlecenia wykonują się przy starcie)."""
    client.__exit__(None, None, None)
    get_settings.cache_clear()
    client.__enter__()
    return client


def _wait_backup(client, headers):
    for _ in range(100):
        st = client.get("/api/admin/backups", headers=headers).json()
        if not st["status"]["running"]:
            return st
        time.sleep(0.05)
    raise AssertionError("kopia się nie skończyła")


def test_storage(app_client, admin, scanned):
    res = app_client.get("/api/admin/storage", headers=admin).json()
    items = {i["key"]: i for i in res["items"]}
    assert items["music"]["files"] == 3 and items["music"]["bytes"] > 0
    assert items["database"]["bytes"] > 0 and res["disk_free"] > 0
    assert {"covers", "trash", "cache", "backups", "downloads"} <= items.keys()
    assert app_client.delete("/api/admin/storage/cache", headers=admin).status_code == 204
    assert app_client.delete("/api/admin/storage/covers", headers=admin).status_code == 422  # tylko kosz i cache


def test_storage_is_admin_only(app_client, admin):
    app_client.post("/api/users", json={"username": "nati", "password": "spiewam1"}, headers=admin)
    tok = app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).json()["access_token"]
    assert app_client.get("/api/admin/storage", headers={"Authorization": "Bearer " + tok}).status_code == 403


def test_backup_and_restore_after_moving_music(app_client, admin, scanned, music_dir, tmp_path, monkeypatch,
                                               no_restart):
    pl = app_client.post("/api/playlists", json={"name": "Na lato"}, headers=admin).json()
    tracks = app_client.get("/api/tracks", headers=admin).json()["items"]
    app_client.patch(f"/api/playlists/{pl['id']}", json={"track_ids": [t["id"] for t in tracks]}, headers=admin)

    assert app_client.post("/api/admin/backups", json={}, headers=admin).status_code == 202
    st = _wait_backup(app_client, admin)
    assert st["status"]["error"] is None
    [backup] = st["backups"]
    assert backup["counts"]["tracks"] == 3 and backup["counts"]["playlists"] == 1
    data = app_client.get(f"/api/admin/backups/{backup['name']}", headers=admin)
    assert data.status_code == 200 and len(data.content) == backup["bytes"]

    # Po kopii: zmiany, które przywrócenie ma cofnąć.
    app_client.delete(f"/api/playlists/{pl['id']}", headers=admin)
    assert app_client.post(f"/api/admin/backups/{backup['name']}/restore", json={"password": "zle"},
                           headers=admin).status_code == 403
    res = app_client.post(f"/api/admin/backups/{backup['name']}/restore", json={"password": "tajne123"},
                          headers=admin)
    assert res.status_code == 200 and no_restart == [1]

    # Przeprowadzka: muzyka w innym folderze, serwer startuje od nowa (zlecenie wykonuje się przy starcie).
    moved = tmp_path / "nowa-muzyka"
    shutil.move(str(music_dir), moved)
    monkeypatch.setenv("NATI_MUSIC_DIRS", str(moved))
    client = _restart(app_client)
    login = client.post("/api/auth/login", json={"username": "ala", "password": "tajne123"})
    assert login.status_code == 200
    h = {"Authorization": "Bearer " + login.json()["access_token"]}
    assert client.get("/api/auth/me", headers=admin).status_code == 200  # stara sesja też działa (klucz z kopii)
    client.app.state.scanner.scan()
    [restored] = client.get("/api/playlists", headers=h).json()
    assert restored["name"] == "Na lato" and restored["track_count"] == 3  # te same utwory, nowe ścieżki
    detail = client.get(f"/api/playlists/{restored['id']}", headers=h).json()
    assert {t["id"] for t in detail["tracks"]} == {t["id"] for t in tracks}


def test_upload_rejects_non_backup(app_client, admin):
    res = app_client.put("/api/admin/backups/upload", content=b"to nie jest tar", headers=admin)
    assert res.status_code == 422


def test_wipe(app_client, admin, scanned, music_dir, no_restart):
    bad = app_client.post("/api/admin/wipe", json={"password": "tajne123", "confirm": "tak"}, headers=admin)
    assert bad.status_code == 422  # trzeba wpisać WYCZYŚĆ
    res = app_client.post("/api/admin/wipe", json={"password": "tajne123", "confirm": "WYCZYŚĆ"}, headers=admin)
    assert res.status_code == 200 and no_restart == [1]
    client = _restart(app_client)
    assert client.get("/api/info").json()["setup_required"] is True
    assert client.get("/api/auth/me", headers=admin).status_code == 401  # nowy klucz – stare tokeny nieważne
    assert any(music_dir.rglob("*.mp3"))  # muzyka użytkownika nietknięta


def test_online_switch_in_app(app_client, admin):
    """Pobieranie z internetu – przełącznik administratora, działa od razu (bez restartu)."""
    assert app_client.get("/api/info").json()["online_enabled"] is True
    assert app_client.put("/api/admin/settings", json={"online_enabled": False}, headers=admin).json() == {
        "online_enabled": False}
    assert app_client.get("/api/info").json()["online_enabled"] is False
    res = app_client.get("/api/online/search", params={"q": "nati"}, headers=admin)
    assert res.status_code == 403
    assert app_client.get("/api/admin/settings", headers=admin).json()["online_enabled"] is False
    app_client.put("/api/admin/settings", json={"online_enabled": True}, headers=admin)
    assert app_client.get("/api/info").json()["online_enabled"] is True


def test_online_switch_only_admin(app_client, admin):
    app_client.post("/api/users", json={"username": "nati", "password": "spiewam1"}, headers=admin)
    tok = app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).json()["access_token"]
    res = app_client.put("/api/admin/settings", json={"online_enabled": False}, headers={"Authorization": "Bearer " + tok})
    assert res.status_code == 403
