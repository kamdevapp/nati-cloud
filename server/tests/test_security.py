"""Bezpieczeństwo logowania: blokada po złych hasłach, sesje urządzeń, weryfikacja dwuetapowa, tokeny strumieni."""

import jwt

from app.config import get_settings
from app.services import totp


def _login(client, password="tajne123", **extra):
    return client.post("/api/auth/login", json={"username": "ala", "password": password, **extra})


def _h(token):
    return {"Authorization": "Bearer " + token}


def test_lockout_after_failed_logins(app_client, admin):
    for _ in range(5):
        assert _login(app_client, "zle-haslo").status_code == 401
    res = _login(app_client)  # nawet dobre hasło – konto chwilowo zablokowane
    body = res.json()
    assert res.status_code == 429 and body["code"] == "auth.locked" and body["params"]["minutes"] >= 1
    assert "try again" in body["detail"]  # tekst zapasowy po angielsku


def test_sessions_list_and_logout(app_client, admin):
    laptop = _login(app_client, device="Laptop").json()["access_token"]
    phone = _login(app_client, device="Telefon").json()["access_token"]
    sessions = app_client.get("/api/auth/sessions", headers=_h(laptop)).json()
    assert {"Laptop", "Telefon"} <= {s["device"] for s in sessions}
    assert [s["device"] for s in sessions if s["current"]] == ["Laptop"]
    phone_id = next(s["id"] for s in sessions if s["device"] == "Telefon")
    assert app_client.delete(f"/api/auth/sessions/{phone_id}", headers=_h(laptop)).status_code == 204
    assert app_client.get("/api/auth/me", headers=_h(phone)).status_code == 401  # telefon wylogowany
    # Wyloguj pozostałe i wyloguj się.
    other = _login(app_client, device="TV").json()["access_token"]
    app_client.delete("/api/auth/sessions", headers=_h(laptop))
    assert app_client.get("/api/auth/me", headers=_h(other)).status_code == 401
    assert app_client.post("/api/auth/logout", headers=_h(laptop)).status_code == 204
    assert app_client.get("/api/auth/me", headers=_h(laptop)).status_code == 401


def test_password_change_logs_out_other_devices(app_client, admin):
    other = _login(app_client, device="Telefon").json()["access_token"]
    res = app_client.post("/api/auth/me/password", json={"current_password": "tajne123", "new_password": "nowe-haslo"},
                          headers=admin)
    assert res.status_code == 204
    assert app_client.get("/api/auth/me", headers=admin).status_code == 200  # to urządzenie zostaje
    assert app_client.get("/api/auth/me", headers=_h(other)).status_code == 401


def test_token_without_session_is_rejected(app_client, admin):
    old = jwt.encode({"sub": "1", "exp": 9999999999}, get_settings().secret_key, algorithm="HS256")
    assert app_client.get("/api/auth/me", headers=_h(old)).status_code == 401


def test_two_factor(app_client, admin):
    setup = app_client.post("/api/auth/2fa/setup", headers=admin).json()
    assert setup["uri"].startswith("otpauth://totp/Nati:ala?secret=")
    assert app_client.post("/api/auth/2fa/enable", json={"code": "000000"}, headers=admin).status_code == 422
    codes = app_client.post("/api/auth/2fa/enable", json={"code": totp.now_code(setup["secret"])},
                            headers=admin).json()["codes"]
    assert len(codes) == 10
    assert app_client.get("/api/auth/me", headers=admin).json()["two_factor"] is True
    # Logowanie: bez kodu – prośba o kod; zły kod – odmowa; dobry – token.
    res = _login(app_client)
    assert res.status_code == 401 and res.json()["mfa_required"] is True
    assert _login(app_client, code="123456").status_code == 401
    assert _login(app_client, code=totp.now_code(setup["secret"])).status_code == 200
    # Kod zapasowy działa raz.
    assert _login(app_client, code=codes[0]).status_code == 200
    assert _login(app_client, code=codes[0]).status_code == 401
    # Wyłączenie – hasło i kod.
    res = app_client.post("/api/auth/2fa/disable", json={"password": "tajne123", "code": codes[1]}, headers=admin)
    assert res.status_code == 204
    assert _login(app_client).status_code == 200


def test_admin_can_disable_users_two_factor(app_client, admin):
    app_client.post("/api/users", json={"username": "nati", "password": "spiewam1"}, headers=admin)
    tok = app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).json()["access_token"]
    secret = app_client.post("/api/auth/2fa/setup", headers=_h(tok)).json()["secret"]
    app_client.post("/api/auth/2fa/enable", json={"code": totp.now_code(secret)}, headers=_h(tok))
    nati = next(u for u in app_client.get("/api/users", headers=admin).json() if u["username"] == "nati")
    app_client.patch(f"/api/users/{nati['id']}", json={"disable_two_factor": True}, headers=admin)
    assert app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).status_code == 200


def test_media_token_only_for_streams(app_client, admin, scanned):
    media = app_client.get("/api/auth/media-token", headers=admin).json()["token"]
    track = app_client.get("/api/tracks", headers=admin).json()["items"][0]
    assert app_client.get(f"/api/stream/{track['id']}", params={"token": media}).status_code == 200
    assert app_client.get("/api/auth/me", headers=_h(media)).status_code == 401  # nie zastąpi logowania


def test_totp_known_vector():
    # RFC 6238 (SHA1, sekret „12345678901234567890”), czas 59 s → 94287082 (8 cyfr) – ostatnie 6: 287082.
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.now_code(secret, at=59) == "287082"
