# Bezpieczeństwo chmury Nati

Chmura Nati trzyma twoją muzykę, konta i playlisty. W sieci domowej wystarczy hasło. Jeśli chcesz
słuchać spoza domu, masz dwie drogi – **bez wystawiania do internetu** (zalecana) albo **przez HTTPS**.

## Co Nati robi samo

- **Blokada zgadywania haseł** – po 5 nieudanych próbach logowania na konto (albo 20 z jednego
  adresu) logowanie jest zablokowane na 15 minut.
- **Zalogowane urządzenia** – Ustawienia → Konto → *Zalogowane urządzenia*: lista z ostatnią
  aktywnością i adresem, wylogowanie wybranych albo wszystkich innych. Zmiana hasła wylogowuje
  pozostałe urządzenia; zmiana hasła przez administratora – wszystkie.
- **Weryfikacja dwuetapowa** – Ustawienia → Konto → *Weryfikacja dwuetapowa*: kod QR do aplikacji
  (Google Authenticator, Aegis, 2FAS, Microsoft Authenticator), przy logowaniu potrzebny 6-cyfrowy
  kod z telefonu. Dostajesz 10 kodów zapasowych (każdy działa raz). Zgubiony telefon: administrator
  wyłącza weryfikację w *Użytkownicy chmury*.
- **Krótkie tokeny w adresach** – odsłuch z internetu przekazuje w adresie tylko token ważny 12 godzin
  (i tylko do strumieni); tokeny w adresach są wycinane z logów serwera.
- **Hasła** są przechowywane jako skróty Argon2.

## Droga 1 (zalecana): bez wystawiania – Tailscale albo WireGuard

Chmura zostaje widoczna tylko w sieci domowej, a telefon / laptop poza domem łączy się z nią
przez prywatny tunel. Nic nie jest otwarte na świat.

**Tailscale** (najprościej):

1. Załóż konto na tailscale.com i zainstaluj Tailscale na serwerze (NAS, komputer z Nati) oraz na
   telefonie / laptopie. Na NAS-ach (Synology, QNAP, TrueNAS, Unraid) jest gotowa aplikacja.
2. Zaloguj oba urządzenia tym samym kontem.
3. W Nati jako adres chmury podaj nazwę serwera z Tailscale, np. `http://nas:8080`
   (albo jego adres `100.x.y.z:8080`).

Ruch jest szyfrowany przez Tailscale, więc HTTPS nie jest potrzebne.

**WireGuard** – to samo bez zewnętrznego konta: router albo serwer jako punkt dostępu, na telefonie
aplikacja WireGuard. Wiele routerów (np. FRITZ!Box, MikroTik, OpenWrt) ma to wbudowane.

## Droga 2: wystawienie do internetu przez HTTPS (Caddy)

Potrzebujesz domeny (albo darmowej nazwy, np. z duckdns.org) wskazującej na twój domowy adres
i przekierowania portów 80 i 443 w routerze na serwer. **Nigdy nie wystawiaj portu 8080 bezpośrednio** –
hasło szłoby przez internet bez szyfrowania.

`docker-compose.yml` z Caddy (Caddy sam pobiera i odnawia certyfikat Let's Encrypt):

```yaml
services:
  nati:
    build: ./server
    volumes:
      - ./music:/music:ro
      - ./data:/data
    restart: unless-stopped
    # Bez „ports:” – Nati jest dostępne tylko przez Caddy.

  caddy:
    image: caddy:2
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./caddy-data:/data
    restart: unless-stopped
```

`Caddyfile`:

```
muzyka.twoja-domena.pl {
    encode zstd gzip
    reverse_proxy nati:8080
}
```

W Nati jako adres chmury podaj `https://muzyka.twoja-domena.pl`.

Przy wystawieniu do internetu:

- włącz **weryfikację dwuetapową** przynajmniej dla kont administratorów,
- używaj długich, różnych haseł,
- co jakiś czas zajrzyj w *Zalogowane urządzenia*,
- rób **kopie zapasowe** (Ustawienia → *Chmura: miejsce i kopie zapasowe*).

## Kopie zapasowe a bezpieczeństwo

Kopia zapasowa zawiera bazę z kontami (skróty haseł, sekrety weryfikacji dwuetapowej) i klucz
podpisujący tokeny. Traktuj plik kopii jak hasło: nie wysyłaj go nikomu, trzymaj na zaszyfrowanym
dysku albo w zaszyfrowanym archiwum.
