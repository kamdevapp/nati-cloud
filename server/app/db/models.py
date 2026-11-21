"""Modele bazy danych."""

from datetime import datetime, timezone

from sqlalchemy import Column, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str]
    is_admin: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    first_name: Mapped[str | None] = mapped_column(String(64))
    last_name: Mapped[str | None] = mapped_column(String(64))
    avatar: Mapped[str | None]  # zdjęcie profilowe – plik w data/covers
    # Kurator: dodaje muzykę i poprawia bibliotekę, ale nie usuwa z chmury i nie zarządza kontami.
    is_curator: Mapped[bool | None]
    # Okładka listy „Ulubione”: ikona, kolory gradientu, obrazek (plik w data/covers).
    fav_icon: Mapped[str | None] = mapped_column(String(64))
    fav_color1: Mapped[str | None] = mapped_column(String(16))
    fav_color2: Mapped[str | None] = mapped_column(String(16))
    fav_image: Mapped[str | None]
    fav_banner: Mapped[str | None]  # zdjęcie w tle nagłówka „Ulubionych”
    # Weryfikacja dwuetapowa (TOTP): sekret (base32), czy włączona, kody zapasowe (JSON: lista skrótów).
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool | None]
    backup_codes: Mapped[str | None]

    # Sesja (urządzenie), z której przyszło bieżące zapytanie – ustawiane przy sprawdzaniu tokenu, nie w bazie.
    session_id = None

    @property
    def role(self) -> str:
        return "admin" if self.is_admin else "curator" if self.is_curator else "user"

    @property
    def can_curate(self) -> bool:
        return bool(self.is_admin or self.is_curator)


class UserSession(Base):
    """Zalogowane urządzenie: token zawiera id sesji – usunięcie wiersza wylogowuje to urządzenie."""

    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # losowy identyfikator
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    device: Mapped[str] = mapped_column(String(128), default="")
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_seen: Mapped[datetime] = mapped_column(default=_now)


class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    search: Mapped[str] = mapped_column(default="")  # znormalizowany tekst do wyszukiwania (app.text)
    # Zdjęcie wykonawcy – nazwa pliku w data/covers.
    image: Mapped[str | None]
    # Kiedy uzupełniono dane z internetu (gatunki, zdjęcie); None – jeszcze nie próbowano.
    enriched_at: Mapped[datetime | None]
    banner: Mapped[str | None]  # zdjęcie w tle nagłówka (panorama) – plik w data/covers

    albums: Mapped[list["Album"]] = relationship(back_populates="artist")


class Album(Base):
    __tablename__ = "albums"
    __table_args__ = (UniqueConstraint("title", "artist_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id"))
    year: Mapped[int | None]
    # Nazwa pliku okładki w data/covers (None = brak okładki).
    cover: Mapped[str | None]
    search: Mapped[str] = mapped_column(default="")  # tytuł + wykonawca, znormalizowane
    banner: Mapped[str | None]  # zdjęcie w tle nagłówka (panorama) – plik w data/covers

    artist: Mapped[Artist] = relationship(back_populates="albums")
    tracks: Mapped[list["Track"]] = relationship(back_populates="album")


track_genres = Table(
    "track_genres",
    Base.metadata,
    Column("track_id", ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class Genre(Base):
    """Gatunek z tagu „genre” (jeden utwór może mieć kilka)."""

    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    search: Mapped[str] = mapped_column(String(128), unique=True)  # znormalizowana nazwa
    # W katalogu Nati (wbudowany albo utworzony przez użytkownika) – zostaje, nawet gdy nieużywany.
    in_catalog: Mapped[bool | None]
    custom: Mapped[bool | None]  # utworzony przez użytkownika
    # Wbudowany gatunek usunięty przez użytkownika – nie wraca do katalogu przy starcie serwera.
    hidden: Mapped[bool | None]
    # Wygląd kafla: ikona (nazwa z listy w aplikacji), dwa kolory tła (#rrggbb), obrazek (plik w data/covers).
    icon: Mapped[str | None] = mapped_column(String(64))
    color1: Mapped[str | None] = mapped_column(String(7))
    color2: Mapped[str | None] = mapped_column(String(7))
    image: Mapped[str | None]


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str] = mapped_column(String(512), index=True)
    # Wykonawca utworu (może się różnić od wykonawcy albumu, np. featuring).
    artist_name: Mapped[str] = mapped_column(String(512))
    album_id: Mapped[int] = mapped_column(ForeignKey("albums.id"), index=True)
    track_no: Mapped[int | None]
    disc_no: Mapped[int | None]
    duration: Mapped[float] = mapped_column(default=0.0)  # sekundy
    bitrate: Mapped[int | None]  # kbps
    format: Mapped[str] = mapped_column(String(16))
    size: Mapped[int]
    mtime: Mapped[float]
    added_at: Mapped[datetime] = mapped_column(default=_now)
    search: Mapped[str] = mapped_column(default="")  # tytuł, wykonawcy, album – znormalizowane
    # Wersja odczytu tagów – gdy skaner zaczyna czytać nowe pola, pliki są czytane ponownie.
    tags_version: Mapped[int | None]

    album: Mapped[Album] = relationship(back_populates="tracks")
    genres: Mapped[list[Genre]] = relationship(secondary=track_genres, lazy="selectin", order_by="Genre.name")


class TrackEdit(Base):
    """Zmiany tagów wprowadzone w Nati, których nie dało się zapisać w pliku
    (folder tylko do odczytu). Skaner nakłada je na tagi odczytane z pliku."""

    __tablename__ = "track_edits"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(unique=True)
    title: Mapped[str | None]
    artist: Mapped[str | None]
    album: Mapped[str | None]
    album_artist: Mapped[str | None]
    track_no: Mapped[int | None]
    disc_no: Mapped[int | None]
    year: Mapped[int | None]
    genre: Mapped[str | None]  # gatunki rozdzielone „; ”
    cover: Mapped[str | None]  # okładka albumu (plik w data/covers)

    def is_empty(self) -> bool:
        return all(getattr(self, f) is None for f in (*EDIT_FIELDS, "cover"))


EDIT_FIELDS = ("title", "artist", "album", "album_artist", "track_no", "disc_no", "year", "genre")


class TrackLyrics(Base):
    """Tekst utworu trzymany w chmurze Nati (wpisany, wgrany z pliku albo pobrany z internetu).

    Kluczem jest ścieżka pliku – jak w TrackEdit – więc tekst przetrwa ponowny skan biblioteki."""

    __tablename__ = "track_lyrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(unique=True)
    # Treść: zwykły tekst albo LRC („[mm:ss.xx] linijka”). None – brak tekstu.
    text: Mapped[str | None]
    synced: Mapped[bool] = mapped_column(default=False)  # czy tekst ma znaczniki czasu
    # user – wpisany/wgrany, lrclib – pobrany z internetu, removed – usunięty przez użytkownika
    # (nie szukamy go ponownie), none – szukano w internecie i nie znaleziono.
    source: Mapped[str] = mapped_column(String(16), default="user")
    checked_at: Mapped[datetime | None]  # kiedy ostatnio szukano w internecie
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class DownloadJob(Base):
    """Zlecenie pobrania utworu z internetu do chmury."""

    __tablename__ = "download_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # "ytmusic" (id = videoId) albo "url" (dowolny link obsługiwany przez yt-dlp)
    source: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(1024), index=True)
    title: Mapped[str] = mapped_column(String(512))
    artist: Mapped[str | None] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    album_artist: Mapped[str | None] = mapped_column(String(512))
    track_no: Mapped[int | None]
    year: Mapped[int | None]
    thumbnail: Mapped[str | None] = mapped_column(String(2048))
    # queued → downloading → processing → done | error | cancelled
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None]  # tekst angielski (zapasowy)
    error_code: Mapped[str | None]  # kod komunikatu (messages.py) – aplikacja tłumaczy
    error_params: Mapped[str | None]  # JSON: parametry komunikatu
    track_id: Mapped[int | None] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None]
    # Import ze Spotify: po pobraniu utwór trafia na tę playlistę.
    playlist_id: Mapped[int | None]


class Playlist(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    image: Mapped[str | None]  # obrazek playlisty – plik w data/covers
    # Okładka bez obrazka: ikona (nazwa z listy w aplikacji) i dwa kolory tła (#rrggbb).
    icon: Mapped[str | None] = mapped_column(String(64))
    color1: Mapped[str | None] = mapped_column(String(7))
    color2: Mapped[str | None] = mapped_column(String(7))
    banner: Mapped[str | None]  # zdjęcie w tle nagłówka (panorama) – plik w data/covers
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    items: Mapped[list["PlaylistItem"]] = relationship(
        order_by="PlaylistItem.position", cascade="all, delete-orphan"
    )


class PlaylistItem(Base):
    __tablename__ = "playlist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"))
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    position: Mapped[int]

    track: Mapped[Track] = relationship()


class PlaylistShare(Base):
    """Playlista udostępniona innemu użytkownikowi chmury (do słuchania albo też do zmieniania)."""

    __tablename__ = "playlist_shares"
    __table_args__ = (UniqueConstraint("playlist_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    can_edit: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Favorite(Base):
    """Ulubiony utwór użytkownika (serduszko)."""

    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "track_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    # Kolejność ustawiona przez użytkownika; brak – od ostatnio dodanych.
    position: Mapped[int | None]


class PlayHistory(Base):
    """Odsłuchany utwór (zapisywany po kilkudziesięciu sekundach słuchania)."""

    __tablename__ = "play_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    played_at: Mapped[datetime] = mapped_column(default=_now, index=True)


class Pin(Base):
    """Przypięte na stronie głównej (Home): wykonawca, album albo playlista."""

    __tablename__ = "pins"
    __table_args__ = (UniqueConstraint("user_id", "kind", "ref_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # artist | album | playlist
    ref_id: Mapped[int]
    position: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class UserOrder(Base):
    """Własna kolejność albumów, wykonawców albo playlist ustawiona przez użytkownika (lista id)."""

    __tablename__ = "user_orders"
    __table_args__ = (UniqueConstraint("user_id", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # album | artist | playlist
    ids: Mapped[str] = mapped_column(default="[]")  # JSON: [id, …]


class ServerSetting(Base):
    """Ustawienia chmury zmieniane w aplikacji przez administratora (np. pobieranie z internetu)."""

    __tablename__ = "server_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(default="")
