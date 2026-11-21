# Nati Cloud __WERSJA__ – installation

Nati Cloud is the server with your music: the Nati apps (computer, phone, TV) connect to it at home
and on the go. It runs in Docker on Linux (Ubuntu, Debian, Fedora, Raspberry Pi OS…).

## Requirements
- Linux with Docker and `docker compose` (the installer offers to install Docker if it is missing)
- an account with `sudo` (password asked once, to create folders)
- about 1 GB for the program + space for downloaded music and covers

## Install
```bash
tar xzf nati-chmura-__WERSJA__.tar.gz
cd nati-chmura-__WERSJA__
./install.sh
```
The installer asks for:
0. **language** – English, Polski, Deutsch, Español, Français, Português (default: your system language),
1. **where to keep the cloud data** – default `/srv/nati` (database, covers, downloaded music, backups),
2. **your music folder** – Nati only reads it (never changes or deletes files),
3. **port** – default 8080; if taken, a free one is suggested.

At the end it shows the address, e.g. `http://192.168.1.10:8090` – enter it in the Nati app. On the first
connection you create the administrator account. Internet music downloads are switched on and off later
in the app (Settings → Cloud) – no reinstall needed.

## Later
| Command | What it does |
|---|---|
| `/srv/nati/install.sh --status` | is it running, address, disk space |
| `/srv/nati/install.sh --logs` | server log |
| `./install.sh --update` | from a new package – new version; data is kept |
| `/srv/nati/install.sh --uninstall` | removes the cloud; data is kept unless you confirm |

The cloud starts on its own after a reboot. Fedora / RHEL (SELinux, firewalld) are supported.

## NAS (Synology, QNAP, Unraid, Portainer)
Instead of the installer use `docker-compose.nas.yml`: change the two paths (music and data) and create
a project / stack from it in the NAS container panel.
