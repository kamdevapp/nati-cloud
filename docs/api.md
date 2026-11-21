# API serwera Nati (v0.7)

Autoryzacja: nagłówek `Authorization: Bearer <token>`. Endpointy multimediów (`/stream`, `/cover`)
przyjmują też `?token=<token>` – dla odtwarzaczy, które nie wysyłają nagłówków.

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/info` | Publiczne. `{name, version, setup_required}` – aplikacja sprawdza, czy to serwer Nati |
| POST | `/api/setup` | Pierwsze konto (admin). Tylko gdy brak kont. `{username, password}` |
| POST | `/api/auth/login` | Logowanie `{username, password, code?, device?}` → `{access_token, user}`. Konto z weryfikacją dwuetapową bez kodu – 401 `{mfa_required: true}`; po 5 złych próbach – 429 |
| POST | `/api/auth/logout` | Wylogowanie tego urządzenia (token przestaje działać) |
| GET | `/api/auth/sessions` | Zalogowane urządzenia `[{id, device, ip, created_at, last_seen, current}]` |
| DELETE | `/api/auth/sessions/{id}` / `/api/auth/sessions` | Wylogowanie wybranego / wszystkich innych urządzeń |
| GET | `/api/auth/media-token` | Krótki token (12 h) tylko do adresów strumieni (`?token=`) |
| POST | `/api/auth/2fa/setup` → `/api/auth/2fa/enable {code}` | Weryfikacja dwuetapowa: sekret + `otpauth://` do QR, włączenie kodem → kody zapasowe |
| POST | `/api/auth/2fa/backup-codes {code}` / `/api/auth/2fa/disable {password, code}` | Nowe kody zapasowe / wyłączenie |
| GET | `/api/auth/me` | Zalogowany użytkownik |
| GET/POST | `/api/users` | Lista / dodanie użytkownika (admin) |
| GET | `/api/home` | Strona główna: `{pins, recent_albums, genres}` (przypięte – osobno dla każdego konta) |
| POST | `/api/pins` | Przypnij `{kind: "album"\|"artist"\|"playlist", id}` |
| DELETE | `/api/pins/{kind}/{id}` | Odepnij |
| PUT | `/api/pins` | Nowa kolejność przypiętych `[{kind, id}, …]` |
| GET / PUT | `/api/order/{kind}` | Własna kolejność (`album` / `artist` / `playlist` / `track` / `genre`) – lista id (gatunki – nazwy); aplikacja układa: najpierw z listy, potem reszta. Utwory: `/api/tracks?sort=custom` (ze stronami) |
| GET/POST | `/api/library/enrich` | Stan / ponowne uzupełnianie z internetu (admin): gatunki wykonawców (MusicBrainz) i zdjęcia (YouTube Music) – działa samo w tle po skanie i pobraniach (`NATI_ENRICH`) |
| POST | `/api/upload` | Wysłanie pliku z urządzenia: treść = plik, nagłówek `X-Filename`. Trafia do `Wykonawca/Album/` wg tagów; duplikat → 409 (`X-Nati-Track-Id`) |
| GET/PUT/DELETE | `/api/playlists/{id}/image` | Obrazek playlisty (właściciel) |
| POST | `/api/playlists/{id}/image/url` | Obrazek playlisty z propozycji `{url}` |
| GET | `/api/images/search?q=&source=` | Darmowe obrazki `[{url, full_url, title, subtitle, source}]` – `url` to podgląd, `full_url` pełny obraz do zapisania; `source` = `commons` / `openverse` (bez – oba); wyniki pamiętane godzinę |
| GET | `/api/genres?catalog=` | Gatunki z biblioteki `[{id, name, track_count, album_count, custom, icon, color1, color2, image_id}]` (najpopularniejsze najpierw); `catalog=true` – cały katalog Nati (~60 wbudowanych + własne, też nieużywane) |
| POST | `/api/genres` | Nowy własny gatunek w katalogu `{name}` |
| PATCH | `/api/genres/{id}` | Wygląd kafla `{icon, color1, color2}` (`""` – domyślny) i zmiana nazwy `{name}` – nowa nazwa trafia do tagów utworów, nazwa istniejącego gatunku łączy oba (kurator) |
| DELETE | `/api/genres/{id}` | Usuń gatunek z bazy i z tagów wszystkich jego utworów; usunięty wbudowany nie wraca (kurator) |
| GET/PUT/DELETE | `/api/genres/{id}/image` | Obrazek kafla gatunku (PUT – plik w treści; `POST …/image/url` – z darmowych zdjęć) |
| GET/PUT/DELETE | `/api/{albums,artists,playlists}/{id}/banner` | Zdjęcie w tle nagłówka (panorama pod gradientem); `POST …/banner/url` – z darmowych zdjęć. Albumy i wykonawcy – kurator, playlisty – kto może je zmieniać. W odpowiedziach `banner_id`. „Ulubione”: `GET/PUT/DELETE /api/favorites/{image,banner}` i `POST …/url` |
| GET | `/api/tracks/{id}/genre-suggestions`, `/api/albums/{id}/genre-suggestions` | Podpowiedzi gatunku `{library, online}` – z biblioteki (album, wykonawca) i z internetu (iTunes, MusicBrainz) |
| GET | `/api/tracks/{id}/lyrics?auto=true` | Tekst `{text, synced, source, updated_at}`: z chmury → plik `.lrc` obok utworu → (auto) LRCLIB; 404 – brak |
| PUT | `/api/tracks/{id}/lyrics` | Zapis tekstu `{text}` (zwykły albo LRC `[mm:ss.xx]`) |
| DELETE | `/api/tracks/{id}/lyrics` | Usunięcie tekstu (nie wraca sam z internetu) |
| POST | `/api/tracks/{id}/lyrics/fetch` | Pobranie tekstu z LRCLIB na żądanie; 404 – nie ma, 502 – brak połączenia |
| GET | `/api/artists?q=&genre=&offset=&limit=` | Wykonawcy (strony) |
| GET | `/api/artists/{id}` | Wykonawca z albumami |
| GET | `/api/albums?q=&genre=&sort=&offset=&limit=` | Albumy; `sort` = `year` (domyślnie) / `title` / `artist` / `added` |
| GET | `/api/albums/{id}` | Album z utworami (posortowane: płyta, numer) |
| GET | `/api/albums/{id}/cover` | Okładka |
| GET | `/api/tracks?q=&genre=&sort=&offset=&limit=` | Utwory; `q` szuka w tytule, wykonawcy, albumie; `sort` = `title` / `artist` / `album` / `added` / `year` |
| GET | `/api/tracks/{id}` | Utwór |
| GET | `/api/stream/{id}?bitrate=` | Plik audio z obsługą `Range`. Z `bitrate=32..320` – transkodowanie do Opus (gdy oryginał ma wyższy bitrate); nagłówki `X-Nati-Transcoded`, `X-Nati-Estimated-Size` |
| GET/POST | `/api/library/scan` | Stan skanu / uruchom skan (admin) |
| GET/POST | `/api/playlists` | Playlisty użytkownika / nowa `{name, track_ids}` |
| GET/PATCH/DELETE | `/api/playlists/{id}` | Szczegóły / zmiana `{name?, track_ids?}` / usunięcie |

Wyszukiwanie ignoruje wielkość liter i polskie znaki („spiewaj” znajdzie „Śpiewaj”).

Gatunki pochodzą z tagu „genre” (kilka rozdzielonych `;`, `/` albo `,`); `genre=` w filtrach nie zwraca uwagi
na wielkość liter i polskie znaki. Albumy, wykonawcy i utwory mają listę `genres`.

Utwory mają też `artist_id` i `artist_image_id` (wykonawca albumu i wersja jego zdjęcia), a playlisty – `image_id`.

Albumy i utwory mają `cover_id`, a wykonawcy `image_id`: to wersja obrazka, która zmienia się po
jego podmianie. Aplikacja po niej poznaje, że trzeba pobrać nowy obrazek.

## Błędy (kody komunikatów)

Każdy błąd to `{"detail": "tekst po angielsku", "code": "auth.bad_credentials", "params": {…}}`.
Aplikacja tłumaczy `code` na swój język (`server.<kod>` w plikach `app/assets/i18n/*.json`), wstawiając
`params` (np. `{minutes}`); kodu, którego nie zna, nie tłumaczy – pokazuje `detail`. Nowy język nie wymaga
więc zmian serwera. Lista kodów: `server/app/messages.py` (test pilnuje, żeby aplikacja miała tłumaczenie
każdego kodu po polsku i po angielsku). Błędy zadań pobierania: `error` + `error_code` + `error_params`;
kopia zapasowa: `step` + `step_code`, `error` + `error_code`; „miejsce na dysku” – nazwy po kluczu (`storage.<key>`).

## Edycja i usuwanie (każdy zalogowany; usuwanie: admin albo osoba, która pobrała utwór)

| Metoda | Ścieżka | Opis |
|---|---|---|
| PATCH | `/api/tracks/{id}` | Tagi utworu `{title?, artist?, album?, album_artist?, track_no?, disc_no?, genre?}`; `null` czyści numer |
| PATCH | `/api/albums/{id}` | `{title?, artist?, year?}` – dla wszystkich utworów albumu. Id albumu się nie zmienia (chyba że łączy się z istniejącym) |
| PATCH | `/api/artists/{id}` | `{name}` – zmiana nazwy we wszystkich albumach i utworach |
| PUT | `/api/albums/{id}/cover` | Nowa okładka – treść zapytania to plik JPEG/PNG/WebP |
| POST | `/api/albums/{id}/cover/url` | Okładka z propozycji `{url}` (tylko serwisy z listy propozycji) |
| GET | `/api/albums/{id}/cover/candidates` | Propozycje okładek z YouTube Music i iTunes |
| GET/PUT/DELETE | `/api/artists/{id}/image` | Zdjęcie wykonawcy |
| POST | `/api/artists/{id}/image/url` | Zdjęcie z propozycji `{url}` |
| GET | `/api/artists/{id}/image/candidates` | Propozycje zdjęć (YouTube Music) |
| DELETE | `/api/tracks/{id}` | Usuń utwór z chmury |
| DELETE | `/api/albums/{id}` | Usuń cały album |
| POST | `/api/tracks/delete` | Usuń wiele `{track_ids}` |

Gdzie trafiają zmiany:
- **Tagi** są zapisywane w plikach (gdy pliki są zapisywalne i `NATI_WRITE_TAGS=true`). Jeśli to niemożliwe
  (np. folder z muzyką podpięty w Dockerze jako `:ro`), zmiany zostają w bazie Nati i są nakładane przy każdym skanie.
- **Usunięte pliki** trafiają do `data/trash` i są kasowane na dobre po `NATI_TRASH_DAYS` dniach.
  Z folderu tylko do odczytu nie da się usuwać (błąd 409).

## Internet → chmura (`/api/online`, każdy zalogowany; wyłącznik `NATI_ONLINE_ENABLED=false`)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/online/status` | Wersja yt-dlp, ostatni błąd aktualizacji, `blocked_until` (blokada YouTube), `has_cookies` |
| GET | `/api/online/search?q=&kind=&source=` | `{tracks, albums, artists}`; `kind`: all / songs / albums / artists; `source`: ytmusic / youtube / soundcloud / bandcamp (rodzaje, których serwis nie ma – puste listy). Utwory spoza YouTube Music mają `source`, a `id` = adres strony (YouTube – videoId) |
| GET | `/api/online/albums/{browseId}` | Album z YouTube Music z listą utworów |
| GET | `/api/online/album?id=` | Album po browseId albo adresie (Bandcamp) |
| GET | `/api/online/artists/{browseId}` | Wykonawca z YouTube Music: `{name, thumbnail, tracks, albums, singles}` (pełna dyskografia) |
| GET | `/api/online/services` | Serwisy obsługiwane przez yt-dlp (do „Wklej link”) |
| GET | `/api/online/discover?refresh=` | Polecane: `{tracks, albums}` podobne do słuchanych, bez tego, co jest w chmurze (tylko dodający muzykę; pamięć 6 h) |
| GET | `/api/online/preview-url?url=` | Odsłuch utworu spoza YouTube (SoundCloud, Bandcamp…) po adresie strony |
| POST | `/api/online/downloads/resume` | Wznów kolejkę wstrzymaną przez blokadę YouTube |
| PUT | `/api/online/cookies` | Plik `cookies.txt` zapasowego konta YouTube (multipart `file`, admin) – używany tylko przy blokadzie / utworach wymagających logowania |
| DELETE | `/api/online/cookies` | Usuń ciasteczka (admin) |
| GET | `/api/online/clip?artist=&title=&video_id=&track_id=` | Teledysk do utworu (tło odtwarzacza): `{video_id}`; 404 – brak |
| GET | `/api/online/video/{videoId}` | Obraz teledysku (bez dźwięku, ≤720p) – z `Range` i pamięcią podręczną |
| GET | `/api/online/preview/{videoId}` | Odsłuch bez pobierania – z przewijaniem (`Range`) i pamięcią podręczną (`NATI_PREVIEW_CACHE_MB`) |
| POST | `/api/online/downloads` | `{kind: "track", track}` / `{kind: "album", album_id}` / `{kind: "url", url}` – do kolejki. Kolejka robi przerwy 2–5 s między pobraniami (6–14 s i jedno naraz przy ≥10 w kolejce; `NATI_DOWNLOAD_PAUSE=false` wyłącza). Blokada YouTube → zadanie wraca do kolejki, kolejka czeka 30 min, 1 h, 2 h… (do 6 h) |
| GET | `/api/online/downloads` | Zadania użytkownika (status, postęp, błąd, `track_id`) |
| DELETE | `/api/online/downloads/{id}` | Anuluj / usuń z listy (utwór zostaje w bibliotece) |
| DELETE | `/api/online/downloads` | Wyczyść zakończone |
| POST | `/api/online/downloads/{id}/retry` | Ponów (po błędzie / anulowaniu) |
| POST | `/api/online/ytdlp/update` | Ręczna aktualizacja yt-dlp (admin) |

Pobrane pliki trafiają do `NATI_DOWNLOADS_DIR` (domyślnie `data/downloads/Wykonawca/Album/NN - Tytuł.opus`),
z tagami i okładką. Folder z muzyką użytkownika nie jest zmieniany.
Przy linkach spoza YouTube Music (zwykły YouTube, SoundCloud…) tagi uzupełniane są z MusicBrainz
(album, rok, numer, okładka z Cover Art Archive), o ile utwór da się jednoznacznie rozpoznać (`NATI_MUSICBRAINZ`).


## Konta i role

Administrator – pełna kontrola (edycja biblioteki, dodawanie muzyki, teksty, konta).
Użytkownik – słucha całej biblioteki, ma własny Home i własne playlisty, może je udostępniać.

| Metoda | Ścieżka | Opis |
|---|---|---|
| PATCH | `/api/auth/me` | Profil `{first_name, last_name}` |
| POST | `/api/auth/me/password` | Zmiana hasła `{current_password, new_password}` |
| PUT / DELETE | `/api/auth/me/avatar` | Zdjęcie profilowe (obrazek w treści) / usunięcie |
| GET | `/api/users/{id}/avatar` | Zdjęcie profilowe |
| GET | `/api/users/directory` | Inni użytkownicy (do udostępniania) |
| GET / POST | `/api/users` | Lista kont / nowe konto `{username, password, is_admin, first_name, last_name}` (administrator) |
| PATCH / DELETE | `/api/users/{id}` | Rola, reset hasła, imię i nazwisko / usunięcie konta z playlistami (administrator) |

## „Zaloguj telefonem” (logowanie TV bez hasła)

| Metoda | Ścieżka | Opis |
|---|---|---|
| POST | `/api/auth/pair/start` | Publiczne – TV prosi o kod `{device}` → `{pair_id, code, expires_in}` (kod ważny 5 min) |
| POST | `/api/auth/pair/approve` | Zalogowana aplikacja potwierdza kod `{code}` → `{device}` |
| POST | `/api/auth/pair/poll` | TV pyta `{pair_id}`: 202 – czeka, 200 – token konta, które potwierdziło, 410 – kod wygasł |

## „Odtwarzaj na…” (WebSocket `/api/remote/ws`)

Każda aplikacja trzyma połączenie (urządzenie z kilkoma profilami – po jednym na profil). Wiadomości JSON:

| Kierunek | Wiadomość | Opis |
|---|---|---|
| → | `{"type": "hello", "token", "device_id", "name", "kind": "tv/desktop/phone"}` | Pierwsza wiadomość |
| ← | `{"type": "devices", "devices": [{id, name, kind, state}]}` | Urządzenia online tego konta (po każdej zmianie) |
| → / ← | `{"type": "state", "state": {track, playing, position_ms, duration_ms}}` | Co gra na urządzeniu (do pozostałych) |
| → | `{"type": "command", "to": id, "command": {action, …}}` | Polecenie: `play` (`tracks`, `index`, `position_ms`), `pause`, `resume`, `toggle`, `next`, `previous`, `seek`, `stop`, `handoff` („graj tutaj”) |
| ← | `{"type": "command", "from", "from_name", "command"}` | Polecenie od innego urządzenia |

Układ strony głównej (osobno dla konta): `GET/PUT /api/order/home` – lista `"sekcja:widok:1/0"`.

## Udostępnianie playlist

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/playlists/shared` | Playlisty udostępnione mi |
| GET / PUT | `/api/playlists/{id}/shares` | Komu udostępniona: `[{user_id, can_edit}]` (właściciel) |
| DELETE | `/api/playlists/{id}` | Właściciel – usuwa; osoba, której udostępniono – rezygnuje z niej |

## Ulubione i historia (osobne dla każdego konta)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/favorites`, `/api/favorites/ids` | Ulubione utwory (najnowsze pierwsze) / same id |
| PUT / DELETE | `/api/favorites/{track_id}` | Dodanie / usunięcie z ulubionych |
| POST | `/api/history` | Odsłuch `{track_id}` (aplikacja zgłasza po 30 s słuchania) |
| GET / DELETE | `/api/history?limit=` | Historia (najnowsze pierwsze) / wyczyszczenie |

Rola `curator` (Kurator): dodaje muzykę i poprawia bibliotekę (tagi, okładki, teksty, gatunki) – bez usuwania i kont.
`POST/PATCH /api/users` przyjmują `role`: `admin` | `curator` | `user`.

## Administracja chmurą (administrator)

| Metoda | Ścieżka | Opis |
|---|---|---|
| GET | `/api/admin/storage` | Zajęte miejsce: muzyka (twoja / pobrana), okładki, baza, kosz, pamięć podręczna, kopie, narzędzia + wolne miejsce na dysku |
| DELETE | `/api/admin/storage/{trash,cache}` | Opróżnienie kosza / pamięci podręcznej |
| GET / POST | `/api/admin/backups` | Lista kopii i stan tworzenia / nowa kopia `{include_downloads}` (w tle) |
| GET / DELETE | `/api/admin/backups/{name}` | Pobranie pliku kopii (.tar) / usunięcie |
| PUT | `/api/admin/backups/upload` | Wgranie kopii (plik w treści) |
| POST | `/api/admin/backups/{name}/restore` | Przywrócenie `{password}` – wykonywane przy starcie, serwer uruchamia się ponownie; ścieżki do muzyki przepisywane na obecne foldery |
| POST | `/api/admin/wipe` | Całkowite wyczyszczenie `{password, include_downloads, confirm: "WYCZYŚĆ"}` – jak wyżej |
| POST | `/api/admin/restart` | Ponowne uruchomienie (kod wyjścia 75 – Docker / uruchom.sh podnoszą serwer) |

