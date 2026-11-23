"""Połączenie z bazą (SQLite) i sesje."""

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(db_url: str) -> None:
    global _engine, _SessionLocal
    _engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(_engine)
    _add_missing_columns(_engine)
    _SessionLocal = sessionmaker(_engine, expire_on_commit=False)
    from .unknown import migrate_unknown  # noqa: PLC0415 – modele muszą być już zarejestrowane
    with _SessionLocal() as db:
        migrate_unknown(db)


def close_db() -> None:
    """Zamyka połączenia z bazą (koniec pracy serwera) – na Windowsie otwartego pliku bazy nie da się
    potem usunąć ani podmienić (przywracanie kopii, czyszczenie chmury)."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = _SessionLocal = None


def new_session() -> Session:
    assert _SessionLocal is not None, "init_db() nie zostało wywołane"
    return _SessionLocal()


def get_db() -> Iterator[Session]:
    """Zależność FastAPI: sesja na czas jednego zapytania."""
    with new_session() as session:
        yield session


def _add_missing_columns(engine: Engine) -> None:
    """Prosta migracja: nowe (opcjonalne) kolumny w istniejących tabelach.

    create_all tworzy tylko brakujące tabele – kolumny dodane w nowszej wersji Nati
    dopisujemy tutaj. Wystarcza, dopóki zmiany schematu to wyłącznie dodawanie pól."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                if not col.nullable:
                    raise RuntimeError(f"Nie umiem dodać wymaganej kolumny {table.name}.{col.name}")
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
