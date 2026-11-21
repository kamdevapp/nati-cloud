"""Konfiguracja serwera Nati (zmienne środowiskowe z prefiksem NATI_)."""

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing import Annotated


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NATI_", env_file=".env", extra="ignore")

    # Foldery z muzyką do skanowania, w zmiennej środowiskowej rozdzielone przecinkiem.
    music_dirs: Annotated[list[Path], NoDecode] = Field(default_factory=lambda: [Path("/music")])
    # Katalog na bazę danych, okładki i (później) pobrane utwory.
    data_dir: Path = Path("./data")
    # Klucz do podpisywania tokenów; jeśli pusty, generowany i zapisywany w data_dir.
    secret_key: str = ""
    token_ttl_days: int = 30
    scan_on_startup: bool = True

    # Pobieranie z internetu (YouTube Music, SoundCloud, Bandcamp… przez yt-dlp).
    online_enabled: bool = True
    # Folder na pobrane z internetu; domyślnie data_dir/downloads. Folder z muzyką
    # użytkownika (music_dirs) zostaje nietknięty.
    downloads_dir: Path | None = None
    ytdlp_auto_update: bool = True
    download_workers: int = 2
    # Przerwy między pobraniami z internetu (ochrona przed blokadą YouTube).
    download_pause: bool = True
    # Uzupełnianie tagów z MusicBrainz przy pobieraniu z linków (SoundCloud, zwykły YouTube…).
    musicbrainz: bool = True
    # Uzupełnianie z internetu w tle: gatunki wykonawców (MusicBrainz) i ich zdjęcia (YouTube Music).
    enrich: bool = True
    # Teksty utworów: gdy utwór nie ma tekstu, szukaj go w internecie (LRCLIB – darmowa baza).
    lyrics: bool = True
    # Pamięć podręczna odsłuchu z internetu (MB) – ponowne odsłuchanie startuje od razu.
    preview_cache_mb: int = 300

    # Edycja tagów: zapisuj zmiany także w plikach (gdy są zapisywalne). Gdy folder jest
    # tylko do odczytu albo to wyłączysz – zmiany są tylko w bazie Nati.
    write_tags: bool = True
    # Usunięte utwory trafiają do data/trash i są kasowane na dobre po tylu dniach.
    trash_days: int = 30

    @field_validator("music_dirs", mode="before")
    @classmethod
    def _split_dirs(cls, v):
        if isinstance(v, str):
            return [Path(p.strip()) for p in v.split(",") if p.strip()]
        return v

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nati.db"

    @property
    def covers_dir(self) -> Path:
        return self.data_dir / "covers"

    @property
    def online_dir(self) -> Path:
        return self.downloads_dir or self.data_dir / "downloads"

    @property
    def tools_dir(self) -> Path:
        """Aktualizowana kopia yt-dlp (obraz Dockera jest tylko do odczytu)."""
        return self.data_dir / "tools"

    @property
    def cookies_file(self) -> Path:
        """Ciasteczka konta YouTube (cookies.txt) – opcjonalne, używane awaryjnie."""
        return self.data_dir / "cookies.txt"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def library_roots(self) -> list[Path]:
        """Foldery skanowane do biblioteki: muzyka użytkownika + pobrane z internetu."""
        # Pobrane z internetu i wysłane z urządzeń – zawsze w bibliotece (też gdy pobieranie jest wyłączone).
        return [*self.music_dirs, self.online_dir]

    def prepare(self) -> None:
        """Tworzy katalogi i ustala klucz tokenów."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
        self.online_dir.mkdir(parents=True, exist_ok=True)
        if not self.secret_key:
            key_file = self.data_dir / "secret.key"
            if not key_file.exists():
                key_file.write_text(secrets.token_urlsafe(48))
                key_file.chmod(0o600)
            self.secret_key = key_file.read_text().strip()


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.prepare()
    return s
