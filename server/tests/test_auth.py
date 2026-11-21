def test_setup_flow(app_client):
    assert app_client.get("/api/info").json()["setup_required"] is True
    res = app_client.post("/api/setup", json={"username": "ala", "password": "tajne123"})
    assert res.status_code == 201
    assert res.json()["user"]["is_admin"] is True
    assert app_client.get("/api/info").json()["setup_required"] is False
    # Drugi raz się nie da – nikt nie przejmie skonfigurowanego serwera.
    assert app_client.post("/api/setup", json={"username": "x", "password": "123456"}).status_code == 409


def test_login(app_client, admin):
    assert app_client.post("/api/auth/login", json={"username": "ala", "password": "zle-haslo"}).status_code == 401
    res = app_client.post("/api/auth/login", json={"username": "ala", "password": "tajne123"})
    assert res.status_code == 200
    me = app_client.get("/api/auth/me", headers={"Authorization": "Bearer " + res.json()["access_token"]})
    assert me.json()["username"] == "ala"


def test_requires_auth(app_client, admin):
    assert app_client.get("/api/albums").status_code == 401
    assert app_client.get("/api/albums", headers={"Authorization": "Bearer smieci"}).status_code == 401


def test_only_admin_creates_users_and_scans(app_client, admin):
    res = app_client.post("/api/users", json={"username": "nati", "password": "spiewam1"}, headers=admin)
    assert res.status_code == 201 and res.json()["is_admin"] is False
    tok = app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).json()["access_token"]
    user = {"Authorization": "Bearer " + tok}
    assert app_client.post("/api/users", json={"username": "x", "password": "123456"}, headers=user).status_code == 403
    assert app_client.post("/api/library/scan", headers=user).status_code == 403
