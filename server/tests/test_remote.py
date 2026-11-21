"""„Odtwarzaj na…”: urządzenia konta online, stan i polecenia między nimi."""


def _hello(ws, token, device, name, kind="desktop"):
    ws.send_json({"type": "hello", "token": token, "device_id": device, "name": name, "kind": kind})
    assert ws.receive_json()["type"] == "welcome"


def _devices(ws):
    while True:
        msg = ws.receive_json()
        if msg["type"] == "devices":
            return {d["id"]: d for d in msg["devices"]}


def test_devices_state_and_commands(app_client, admin):
    token = admin["Authorization"].split()[1]
    with app_client.websocket_connect("/api/remote/ws") as phone:
        _hello(phone, token, "tel-1", "Telefon Ali", "phone")
        assert set(_devices(phone)) == {"tel-1"}
        with app_client.websocket_connect("/api/remote/ws") as tv:
            _hello(tv, token, "tv-1", "Telewizor", "tv")
            assert set(_devices(tv)) == {"tel-1", "tv-1"}
            devices = _devices(phone)  # telefon dowiaduje się o TV
            assert devices["tv-1"]["kind"] == "tv" and devices["tv-1"]["name"] == "Telewizor"

            # Telefon: „zagraj na TV” – TV dostaje polecenie.
            phone.send_json({"type": "command", "to": "tv-1", "command": {"action": "pause"}})
            msg = tv.receive_json()
            assert msg == {"type": "command", "from": "tel-1", "from_name": "Telefon Ali",
                           "command": {"action": "pause"}}

            # TV: co gra – trafia do telefonu.
            tv.send_json({"type": "state", "state": {"playing": True, "title": "Utwór 1"}})
            msg = phone.receive_json()
            assert msg == {"type": "state", "device": "tv-1", "state": {"playing": True, "title": "Utwór 1"}}

            # Nieznane urządzenie – błąd.
            phone.send_json({"type": "command", "to": "nie-ma", "command": {"action": "pause"}})
            assert phone.receive_json()["type"] == "error"
        # TV się rozłączył – telefon dostaje nową listę.
        assert set(_devices(phone)) == {"tel-1"}


def test_other_accounts_do_not_see_each_other(app_client, admin):
    app_client.post("/api/users", json={"username": "nati", "password": "spiewam1"}, headers=admin)
    other = app_client.post("/api/auth/login", json={"username": "nati", "password": "spiewam1"}).json()["access_token"]
    with app_client.websocket_connect("/api/remote/ws") as a:
        _hello(a, admin["Authorization"].split()[1], "dev-a", "A")
        _devices(a)
        with app_client.websocket_connect("/api/remote/ws") as b:
            _hello(b, other, "dev-b", "B")
            assert set(_devices(b)) == {"dev-b"}
            b.send_json({"type": "command", "to": "dev-a", "command": {"action": "pause"}})
            assert b.receive_json()["type"] == "error", "cudze urządzenie jest niewidoczne"


def test_bad_token_rejected(app_client, admin):
    with app_client.websocket_connect("/api/remote/ws") as ws:
        ws.send_json({"type": "hello", "token": "smieci", "device_id": "x"})
        msg = ws.receive_json()
        assert msg["type"] == "error"
