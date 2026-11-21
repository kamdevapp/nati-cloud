"""„Zaloguj telefonem”: TV dostaje kod, zalogowana aplikacja go potwierdza, TV dostaje token."""

from app.api import pairing


def test_pair_flow(app_client, admin):
    start = app_client.post("/api/auth/pair/start", json={"device": "Telewizor w salonie"}).json()
    assert len(start["code"]) == 6
    pid = start["pair_id"]
    # Przed potwierdzeniem – czeka.
    assert app_client.post("/api/auth/pair/poll", json={"pair_id": pid}).status_code == 202
    # Potwierdzić może tylko zalogowany.
    assert app_client.post("/api/auth/pair/approve", json={"code": start["code"]}).status_code == 401
    res = app_client.post("/api/auth/pair/approve", json={"code": start["code"].lower()}, headers=admin)
    assert res.status_code == 200 and res.json()["device"] == "Telewizor w salonie"
    res = app_client.post("/api/auth/pair/poll", json={"pair_id": pid})
    assert res.status_code == 200
    token = res.json()["access_token"]
    me = app_client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
    assert me.json()["username"] == "ala"
    # TV na liście urządzeń, a token wydaje się tylko raz.
    sessions = app_client.get("/api/auth/sessions", headers=admin).json()
    assert any(s["device"] == "Telewizor w salonie" for s in sessions)
    assert app_client.post("/api/auth/pair/poll", json={"pair_id": pid}).status_code == 410


def test_pair_wrong_and_expired_code(app_client, admin, monkeypatch):
    assert app_client.post("/api/auth/pair/approve", json={"code": "ZZZZZZ"}, headers=admin).status_code == 404
    start = app_client.post("/api/auth/pair/start", json={}).json()
    monkeypatch.setattr(pairing, "TTL", -1)
    assert app_client.post("/api/auth/pair/approve", json={"code": start["code"]}, headers=admin).status_code == 404
    assert app_client.post("/api/auth/pair/poll", json={"pair_id": start["pair_id"]}).status_code == 410
