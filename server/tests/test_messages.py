"""Komunikaty serwera: kod + tekst angielski; każdy kod użyty w kodzie ma tekst w rejestrze."""

import pathlib
import re

import pytest

from app.messages import MESSAGES, STORAGE_LABELS, render

APP = pathlib.Path(__file__).parent.parent / "app"
# AppError(status, "kod"…), CodedError("kod"…), EditError("kod"…), YtDlpError("kod"…), SpotifyError("kod"…),
# ScanBusyError("kod"…), _err("kod"…), error_body("kod"…), _get(db, Model, id, "kod"), _image_file(…, "kod")
_USES = re.compile(r'(?:AppError\(\s*status\.\w+,\s*|(?:Coded|Edit|YtDlp|Spotify|ScanBusy)Error\(\s*|_err\(\s*|'
                   r'error_body\(\s*|_get\(db, \w+, [\w.]+, |_image_file\([^,]+, |NOT_FOUND = )"([a-z_]+\.[a-z_.]+)"')


def test_every_used_code_has_a_message():
    used = set()
    for f in APP.rglob("*.py"):
        used |= set(_USES.findall(f.read_text(encoding="utf-8")))
    assert len(used) > 60
    assert not used - MESSAGES.keys(), f"kody bez tekstu: {sorted(used - MESSAGES.keys())}"


def test_messages_are_english_with_params():
    polish = re.compile(r"[ąćęłńóśźż]")
    assert not [c for c, t in MESSAGES.items() if polish.search(t.lower())]
    assert not [k for k, t in STORAGE_LABELS.items() if polish.search(t.lower())]
    assert render("auth.locked", {"minutes": 5}) == "Too many failed login attempts – try again in 5 min"
    assert render("nieznany.kod") == "nieznany.kod"


def test_error_response_has_code_params_and_english_detail(app_client, admin):
    res = app_client.get("/api/albums/999999", headers=admin)
    assert res.status_code == 404
    assert res.json() == {"detail": "No such album", "code": "album.not_found", "params": {}}
    res = app_client.post("/api/auth/login", json={"username": "ala", "password": "zle-haslo"})
    assert res.json()["code"] == "auth.bad_credentials"


def test_app_has_translations_for_every_code():
    """Aplikacja (app/assets/i18n) ma polskie i angielskie tłumaczenie każdego kodu serwera."""
    import json

    i18n = APP.parent.parent / "app" / "assets" / "i18n"
    if not i18n.is_dir():
        pytest.skip("bez kodu aplikacji (repozytorium nati-cloud) – tłumaczenia są w nati-translations")
    for lang in ("en", "pl"):
        texts = json.loads((i18n / f"{lang}.json").read_text(encoding="utf-8"))
        missing = [c for c in MESSAGES if f"server.{c}" not in texts]
        missing += [k for k in STORAGE_LABELS if f"storage.{k}" not in texts]
        assert not missing, f"{lang}.json – brak: {missing}"
        # Te same parametry ({minutes}…) co w tekście angielskim serwera.
        for code, text in MESSAGES.items():
            assert set(re.findall(r"\{(\w+)\}", texts[f"server.{code}"])) == set(re.findall(r"\{(\w+)\}", text)), code
