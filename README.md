# Nati Cloud

**Your own home music server for the [Nati](https://github.com/kamdevapp/nati) player.** Your music
library, accounts for everyone at home, playlists, and music from the internet saved to *your* disk.
No subscription, no ads, no tracking. The Nati apps (computer, phone, TV) connect to it at home and on the go.

- **Your music folders** are scanned and read only: Nati never changes or deletes your files.
- **Accounts for the whole household**, each with their own favourites, playlists and history. Two-step
  verification, login throttling, and a list of signed-in devices you can log out remotely.
- **Download from the internet into your cloud** (YouTube Music, SoundCloud, Bandcamp… via
  [yt-dlp](https://github.com/yt-dlp/yt-dlp)), with tags and covers filled in automatically (MusicBrainz).
  The administrator can switch this off.
- **Streaming in any quality.** The original file or Opus transcoded on the fly, for mobile data.
- **Play on…** One device controls another: start music on the TV from your phone.
- **Small and simple.** Python + SQLite, one container, runs on a Raspberry Pi.

## Install

### Linux server or Raspberry Pi (Docker)
```bash
git clone https://github.com/kamdevapp/nati-cloud && cd nati-cloud && ./chmura/install.sh
```
Later updates: `git pull && ./chmura/install.sh --update` (your music, accounts and playlists stay).
Without git: download `nati-chmura-<version>.tar.gz` from
[Releases](https://github.com/kamdevapp/nati/releases), then
`tar xzf nati-chmura-*.tar.gz && cd nati-chmura-*/ && ./install.sh`.

The installer asks for the language, the data folder, your music folder and the port. It sets up Docker
if needed and prints the address to type into the app. On the first connection you create the
administrator account. Details: [`chmura/README.md`](chmura/README.md).

### NAS (Synology, QNAP, Unraid, Portainer)
Use [`chmura/docker-compose.nas.yml`](chmura/docker-compose.nas.yml): set the two paths (music, data)
and create a project / stack from it.

### Your desktop computer, without Docker
In the Nati app choose **Cloud on this computer**. It downloads a self-contained package and runs the
cloud as a systemd user service: Python, ffmpeg and QuickJS are included. The package is built by
[`pakiety/linux/zbuduj-chmure-lokalna.sh`](pakiety/linux/zbuduj-chmure-lokalna.sh).

## Run from source
```bash
cd server
python3 -m venv .venv && .venv/bin/pip install -e . pytest
NATI_MUSIC_DIRS=~/Music NATI_DATA_DIR=./data .venv/bin/uvicorn app.main:app --port 8080
.venv/bin/pytest          # tests
```
Needs `ffmpeg` and, for YouTube, a JS runtime (`deno`, `node` or `qjs`).

## Settings (environment variables)
| Variable | Default | |
|---|---|---|
| `NATI_MUSIC_DIRS` | `/music` | music folders, comma separated (read only) |
| `NATI_DATA_DIR` | `./data` | database, covers, downloads, backups |
| `NATI_DOWNLOADS_DIR` | `data/downloads` | where music from the internet goes |
| `NATI_ONLINE_ENABLED` | `true` | allow downloading from the internet (also a switch in the app) |
| `NATI_WRITE_TAGS` | `true` | write tag edits into the files too (when writable) |
| `NATI_SCAN_ON_STARTUP` | `true` | scan the music folders when the server starts |
| `NATI_TOKEN_TTL_DAYS` | `30` | how long a login lasts |
| `NATI_TRASH_DAYS` | `30` | deleted tracks are kept in `data/trash` this long |
| `NATI_DOWNLOAD_WORKERS` | `2` | parallel downloads |
| `NATI_MUSICBRAINZ`, `NATI_ENRICH`, `NATI_LYRICS` | `true` | tags, artist genres & photos, lyrics (LRCLIB) from the internet |

Security and access from outside your home: [`docs/bezpieczenstwo.md`](docs/bezpieczenstwo.md).
HTTP API: [`docs/api.md`](docs/api.md). Both are in Polish for now.

## Licence
Nati Cloud is free software under the **GNU General Public License v3.0 or later** ([`LICENSE`](LICENSE)).
The Nati apps are a separate program, free to download from
[kamdevapp/nati](https://github.com/kamdevapp/nati). They talk to the cloud only over its HTTP API.

Bug reports and pull requests are welcome. Translations of the app:
[kamdevapp/nati-translations](https://github.com/kamdevapp/nati-translations).

---

## 🇵🇱 Po polsku
**Nati Cloud to Twój domowy serwer muzyki dla odtwarzacza Nati.** Twoja biblioteka, konta dla domowników,
playlisty i muzyka z internetu zapisywana na *Twoim* dysku. Bez abonamentu, reklam i śledzenia.

- **Serwer albo Raspberry Pi:**
  `git clone https://github.com/kamdevapp/nati-cloud && cd nati-cloud && ./chmura/instaluj.sh`.
  Aktualizacja: `git pull && ./chmura/instaluj.sh --aktualizuj`. Albo paczka `nati-chmura-<wersja>.tar.gz`
  z [Releases](https://github.com/kamdevapp/nati/releases) i `./instaluj.sh`. Instalator po polsku pyta
  o folder danych, folder z muzyką i port. Szczegóły: [`chmura/CZYTAJ.md`](chmura/CZYTAJ.md).
- **NAS:** [`chmura/docker-compose.nas.yml`](chmura/docker-compose.nas.yml).
- **Komputer bez Dockera:** w aplikacji Nati wybierz **Chmura na tym komputerze**.

Licencja: GNU GPL v3 lub nowsza. Aplikacje Nati to osobny program, darmowy do pobrania.
