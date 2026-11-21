# Chmura Nati __WERSJA__ – instalacja

Chmura Nati to serwer z Twoją muzyką: aplikacje Nati (komputer, telefon, TV) łączą się z nim w domu
i poza domem. Działa w Dockerze na Linuxie (Ubuntu, Debian, Fedora…).

## Wymagania
- Linux z Dockerem i `docker compose` (instalator zaproponuje instalację Dockera, jeśli go nie ma)
- konto z prawem `sudo` (hasło – raz, przy tworzeniu folderów)
- ok. 1 GB miejsca na program + miejsce na pobraną muzykę i okładki

## Instalacja
```bash
tar xzf nati-chmura-__WERSJA__.tar.gz
cd nati-chmura-__WERSJA__
./instaluj.sh      # albo: ./install.sh (to samo; najpierw wybór języka)
```
Instalator zapyta o:
0. **język** – polski, angielski, niemiecki, hiszpański, francuski, portugalski (domyślnie język systemu),
1. **miejsce na dane chmury** – domyślnie `/srv/nati` (baza, okładki, pobrana muzyka, kopie zapasowe),
2. **folder z Twoją muzyką** – Nati tylko go czyta (nie zmienia ani nie usuwa plików),
3. **port** – domyślnie 8080; zajęty → sam zaproponuje wolny.

Pobieranie muzyki z internetu do chmury włączasz i wyłączasz później w aplikacji
(Ustawienia → Chmura: miejsce, kopie i ustawienia) – bez ponownej instalacji.

Na końcu poda adres, np. `http://192.168.1.10:8090` – wpisz go w aplikacji Nati. Przy pierwszym
połączeniu tworzysz konto administratora.

## Później
| Polecenie | Co robi |
|---|---|
| `/srv/nati/instaluj.sh --stan` | czy działa, adres, miejsce na dysku |
| `/srv/nati/instaluj.sh --logi` | dziennik serwera |
| `./instaluj.sh --aktualizuj` | z nowej paczki – nowa wersja; dane zostają |
| `/srv/nati/instaluj.sh --odinstaluj` | usuwa chmurę; dane zostają (chyba że potwierdzisz) |

Chmura wstaje sama po restarcie serwera. Fedora / RHEL (SELinux, firewalld) – obsługiwane.

## NAS (Synology, QNAP, Unraid, Portainer)
Zamiast instalatora: plik `docker-compose.nas.yml` – podmień w nim dwie ścieżki (muzyka i dane)
i utwórz z niego projekt w panelu kontenerów NAS-a. Dostęp spoza domu i HTTPS: `docs/bezpieczenstwo.md`.
