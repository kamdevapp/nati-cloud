"""Ustawienia chmury zmieniane w aplikacji (administrator) – zapisane w bazie, działają od razu.

Zmienna środowiskowa (np. NATI_ONLINE_ENABLED) to tylko wartość domyślna – dopóki nikt nie przestawi
przełącznika w aplikacji."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import ServerSetting

_ONLINE = "online_enabled"
_cache: dict[str, str] = {}


def load(db: Session) -> None:
    """Przy starcie serwera (i po przywróceniu kopii): wartości z bazy do pamięci."""
    _cache.clear()
    for row in db.query(ServerSetting).all():
        _cache[row.key] = row.value


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(ServerSetting, key)
    if row is None:
        db.add(ServerSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()
    _cache[key] = value


def online_enabled() -> bool:
    """Czy wolno szukać, słuchać i pobierać muzykę z internetu do chmury."""
    value = _cache.get(_ONLINE)
    return get_settings().online_enabled if value is None else value == "1"


def set_online_enabled(db: Session, on: bool) -> None:
    _set(db, _ONLINE, "1" if on else "0")
