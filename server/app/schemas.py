"""Schematy odpowiedzi i zapytań API."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth
class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=256)


Role = Literal["admin", "curator", "user"]


class UserOut(BaseModel):
    id: int
    username: str
    is_admin: bool
    role: Role = "user"
    two_factor: bool = False  # weryfikacja dwuetapowa włączona
    first_name: str | None = None
    last_name: str | None = None
    avatar_id: str | None = None  # wersja zdjęcia profilowego (None – brak)


class UserBrief(BaseModel):
    """Inny użytkownik chmury (np. do udostępniania playlist) – bez uprawnień."""
    id: int
    username: str
    first_name: str | None = None
    last_name: str | None = None
    avatar_id: str | None = None


class ProfileUpdate(BaseModel):
    first_name: str | None = Field(default=None, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


class NewUser(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=256)
    is_admin: bool = False
    role: Role | None = None  # ma pierwszeństwo przed is_admin
    first_name: str | None = Field(default=None, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)


class UserAdminUpdate(BaseModel):
    disable_two_factor: bool = False  # zgubiony telefon – administrator wyłącza weryfikację dwuetapową
    is_admin: bool | None = None
    role: Role | None = None
    password: str | None = Field(default=None, min_length=6, max_length=256)
    first_name: str | None = Field(default=None, max_length=64)
    last_name: str | None = Field(default=None, max_length=64)


class ShareIn(BaseModel):
    user_id: int
    can_edit: bool = False


class ShareOut(BaseModel):
    user: UserBrief
    can_edit: bool


class LoginIn(Credentials):
    code: str | None = Field(default=None, max_length=32)  # kod z aplikacji albo kod zapasowy
    device: str | None = Field(default=None, max_length=128)  # nazwa urządzenia na liście sesji


class SessionOut(BaseModel):
    id: str
    device: str
    ip: str | None
    created_at: datetime
    last_seen: datetime
    current: bool = False


class TwoFactorSetup(BaseModel):
    secret: str
    uri: str  # otpauth://… – do kodu QR


class TwoFactorCode(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class TwoFactorDisable(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str | None = Field(default=None, max_length=32)


class BackupCodes(BaseModel):
    codes: list[str]


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ServerInfo(BaseModel):
    name: str = "Nati"
    version: str
    setup_required: bool
    online_enabled: bool = True  # pobieranie z internetu do chmury (przełącznik administratora)


# --- biblioteka
class ArtistOut(ORM):
    id: int
    name: str
    album_count: int = 0
    # Wersja zdjęcia (zmienia się po podmianie) – None = brak zdjęcia.
    image_id: str | None = None
    genres: list[str] = []
    banner_id: str | None = None  # wersja zdjęcia w tle nagłówka
    track_count: int = 0  # utwory na jego albumach


class AlbumOut(ORM):
    id: int
    title: str
    artist_id: int
    artist_name: str
    year: int | None
    has_cover: bool
    # Wersja okładki (zmienia się po podmianie) – do odświeżenia pamięci podręcznej.
    cover_id: str | None = None
    track_count: int = 0
    genres: list[str] = []
    banner_id: str | None = None  # wersja zdjęcia w tle nagłówka


class LyricsOut(BaseModel):
    text: str
    synced: bool  # tekst ze znacznikami czasu (LRC) – można podświetlać linijki
    source: str  # user | lrclib | file (plik .lrc obok utworu)
    updated_at: datetime | None = None


class LyricsIn(BaseModel):
    text: str = Field(max_length=200_000)


class TrackOut(ORM):
    id: int
    title: str
    artist_name: str
    album_id: int
    album_title: str
    album_artist: str
    track_no: int | None
    disc_no: int | None
    duration: float
    bitrate: int | None
    format: str
    size: int
    year: int | None = None
    cover_id: str | None = None  # wersja okładki albumu
    genres: list[str] = []
    artist_id: int | None = None  # wykonawca albumu
    artist_image_id: str | None = None  # wersja jego zdjęcia


class AlbumDetail(AlbumOut):
    tracks: list[TrackOut]


class ArtistDetail(ArtistOut):
    albums: list[AlbumOut]


class Page[T](BaseModel):
    items: list[T]
    total: int
    offset: int
    limit: int


# --- playlisty
class PlaylistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    track_ids: list[int] = []


_HEX = r"^#[0-9a-fA-F]{6}$"


class PlaylistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    # Okładka: ikona i kolory tła (pusty napis – przywróć domyślne).
    icon: str | None = Field(default=None, max_length=64)
    color1: str | None = Field(default=None, pattern=_HEX + "|^$")
    color2: str | None = Field(default=None, pattern=_HEX + "|^$")
    # Jeśli podane – zastępuje całą zawartość playlisty (w tej kolejności).
    track_ids: list[int] | None = None


class PlaylistOut(BaseModel):
    id: int
    name: str
    track_count: int
    duration: float
    updated_at: datetime
    image_id: str | None = None  # wersja obrazka playlisty
    banner_id: str | None = None  # wersja zdjęcia w tle nagłówka
    icon: str | None = None
    color1: str | None = None
    color2: str | None = None
    # Udostępnianie: czyja jest, czy wolno ją zmieniać, ilu osobom ją udostępniono.
    owner_id: int | None = None
    owner_name: str | None = None
    mine: bool = True
    can_edit: bool = True
    share_count: int = 0


class PlaylistDetail(PlaylistOut):
    tracks: list[TrackOut]


# --- edycja
class TrackEdit(BaseModel):
    """Pola do zmiany; pominięte zostają bez zmian, null czyści (numer, płyta)."""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    artist: str | None = Field(default=None, min_length=1, max_length=512)
    album: str | None = Field(default=None, min_length=1, max_length=512)
    album_artist: str | None = Field(default=None, min_length=1, max_length=512)
    track_no: int | None = Field(default=None, ge=0, le=9999)
    disc_no: int | None = Field(default=None, ge=0, le=999)
    genre: str | None = Field(default=None, max_length=512)  # kilka rozdzielonych „;”


class AlbumEdit(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    artist: str | None = Field(default=None, min_length=1, max_length=512)
    year: int | None = Field(default=None, ge=0, le=9999)
    genre: str | None = Field(default=None, max_length=512)  # dla wszystkich utworów albumu


class ArtistEdit(BaseModel):
    name: str = Field(min_length=1, max_length=512)


class ImageUrl(BaseModel):
    url: str = Field(max_length=2048)


class ImageCandidate(BaseModel):
    url: str  # podgląd (miniatura)
    full_url: str | None = None  # pełny obraz do pobrania po wyborze (None – ten sam co url)
    title: str = ""
    subtitle: str = ""
    source: str = ""


class DeleteRequest(BaseModel):
    track_ids: list[int] = Field(min_length=1, max_length=5000)


class GenreOut(BaseModel):
    id: int = 0
    name: str
    track_count: int
    album_count: int
    custom: bool = False  # dodany przez użytkownika
    # Wygląd kafla (None – wyliczony z nazwy) i wersja obrazka.
    icon: str | None = None
    color1: str | None = None
    color2: str | None = None
    image_id: str | None = None


class PinRequest(BaseModel):
    kind: str = Field(pattern="^(artist|album|playlist)$")
    id: int


class PinOut(BaseModel):
    kind: str
    id: int
    album: AlbumOut | None = None
    artist: ArtistOut | None = None
    playlist: PlaylistOut | None = None


class HomeOut(BaseModel):
    pins: list[PinOut]
    recent_albums: list[AlbumOut]
    genres: list[GenreOut]
    # Albumy ostatnio słuchane przez tego użytkownika (najnowsze pierwsze).
    recently_played: list[AlbumOut] = []
    favorite_count: int = 0


class HistoryIn(BaseModel):
    track_id: int


class HistoryEntry(BaseModel):
    track: "TrackOut"
    played_at: datetime
