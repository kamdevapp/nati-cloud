#!/usr/bin/env bash
# Nati Cloud installer (your music server) – Docker on Linux (Ubuntu, Debian, Fedora, Raspberry Pi OS…).
# Instalator chmury Nati – English, Polski, Deutsch, Español, Français, Português.
#
#   ./install.sh                 install (asks for language, music folder, data folder and port)
#   ./install.sh --update        new version from this package; database, covers and music are kept
#   ./install.sh --status        is the cloud running, address, version, disk space
#   ./install.sh --logs          recent server log
#   ./install.sh --uninstall     removes the cloud; data is kept unless you confirm deleting it
#   ./install.sh --help          this help
#
# Polskie nazwy też działają: ./instaluj.sh --aktualizuj / --stan / --logi / --odinstaluj / --pomoc
# Without questions:  NATI_LANG=en NATI_MUSIC_DIR=/srv/music NATI_PORT=8090 ./install.sh --yes
set -euo pipefail

VERSION="__WERSJA__"                       # set when the package is built
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${NATI_CONF:-/etc/nati-chmura.conf}"   # pointer: where the cloud is installed
DEFAULT_HOME=/srv/nati

c_ok=$'\033[1;32m'; c_info=$'\033[1;35m'; c_warn=$'\033[1;33m'; c_err=$'\033[1;31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'

# ------------------------------------------------------------------ languages

LANGS=(en pl de es fr pt)
declare -A LANG_NAME=([en]="English" [pl]="Polski" [de]="Deutsch" [es]="Español" [fr]="Français" [pt]="Português")
declare -A M

load_messages() {
  case "$1" in
  pl) M=(
    [title]="Instalacja chmury Nati %s"
    [already]="Chmura Nati jest już zainstalowana (%s)."
    [already_hint]="Nowa wersja: %s --update   ·   stan: %s --status"
    [no_docker]="Nie ma Dockera – chmura Nati działa w kontenerze Dockera."
    [install_docker]="Zainstalować Dockera teraz (oficjalny skrypt get.docker.com)?"
    [need_docker]="Bez Dockera nie da się zainstalować chmury."
    [docker_group]="Twoje konto nie ma dostępu do Dockera – dopisuję je do grupy „docker” (po ponownym zalogowaniu bez sudo)."
    [docker_down]="Docker nie odpowiada. Uruchom go: sudo systemctl enable --now docker"
    [no_compose]="Brak „docker compose”. Zainstaluj: sudo apt install docker-compose-plugin (albo nowszy Docker)."
    [ask_home]="Gdzie zapisywać dane chmury (baza, okładki, pobrana muzyka)"
    [ask_music]="Folder z Twoją muzyką (tylko do odczytu)"
    [create_music]="Folder %s nie istnieje – utworzyć (pusty, muzykę dodasz później)?"
    [need_music]="Podaj istniejący folder z muzyką."
    [port_busy_suggest]="Port %s jest zajęty przez inny program – proponuję %s."
    [ask_port]="Port chmury"
    [port_busy]="Port %s jest zajęty – wybierz inny."
    [summary_title]="Podsumowanie:"
    [summary_line]="dane: %s · muzyka: %s · port: %s · język: %s"
    [ask_install]="Instalować?"
    [aborted]="Przerwano – nic nie zmieniłem."
    [building]="Buduję chmurę (pierwszy raz kilka minut – pobieranie Pythona, ffmpeg, yt-dlp)…"
    [waiting]="Czekam, aż chmura wystartuje…"
    [not_responding]="Chmura nie odpowiada. Dziennik: %s --logs"
    [ufw]="Zapora (ufw) jest włączona – otworzyć port %s dla sieci domowej?"
    [firewalld]="Zapora (firewalld) jest włączona – otworzyć port %s dla sieci domowej?"
    [port_opened]="Port %s otwarty w zaporze."
    [selinux]="SELinux jest włączony – foldery chmury zostaną odpowiednio oznaczone."
    [running]="Chmura Nati działa!"
    [addr]="Adres do aplikacji:   %s"
    [music_line]="Muzyka:               %s (tylko do odczytu)"
    [data_line]="Dane chmury:          %s"
    [first_connect]="W aplikacji Nati wpisz ten adres – przy pierwszym połączeniu tworzysz konto administratora."
    [scan_soon]="Muzyka z folderu pojawi się po chwili (skanowanie w tle)."
    [updated_kept]="Konta, playlisty i muzyka zostały bez zmian – aplikacje połączą się same."
    [online_hint]="Pobieranie muzyki z internetu włączysz / wyłączysz w aplikacji: Ustawienia → Chmura."
    [later]="Stan: %s --status · aktualizacja: nowa paczka → ./install.sh --update"
    [security]="Dostęp spoza domu i HTTPS: docs/bezpieczenstwo.md w paczce"
    [not_installed]="Nie znalazłem instalacji chmury Nati (%s). Najpierw: %s"
    [take_ownership]="Przejmuję pliki chmury w %s na Twoje konto (jednorazowo, potem aktualizacje bez sudo)…"
    [missing_server]="W paczce brakuje kodu serwera (%s)."
    [updating]="Aktualizacja chmury Nati: %s → %s (baza i muzyka zostają)"
    [update_failed]="Chmura nie odpowiada po aktualizacji. Dziennik: %s --logs"
    [updated]="Zaktualizowano do %s."
    [status_ok]="Chmura działa: %s  (%s)"
    [status_down]="Chmura nie odpowiada na porcie %s."
    [disk_data]="dane"
    [disk_free]="wolne na dysku"
    [uninstalling]="Usuwam chmurę Nati z %s (kontener i program)."
    [continue]="Kontynuować?"
    [aborted_short]="Przerwano."
    [delete_data]="Usunąć też DANE chmury (%s: konta, playlisty, pobraną muzykę)? Tego nie da się cofnąć"
    [data_deleted]="Dane usunięte."
    [data_kept]="Dane zostały w %s – ponowna instalacja z tym samym folderem je odzyska."
    [uninstalled]="Chmura Nati odinstalowana. Twoja muzyka (%s) nie była ruszana."
    [unknown_option]="Nieznana opcja: %s (zobacz: %s --help)"
    [yes_no]="T/n"
    [no_yes]="t/N"
  ) ;;
  de) M=(
    [title]="Installation der Nati-Cloud %s"
    [already]="Die Nati-Cloud ist bereits installiert (%s)."
    [already_hint]="Neue Version: %s --update   ·   Status: %s --status"
    [no_docker]="Docker fehlt – die Nati-Cloud läuft in einem Docker-Container."
    [install_docker]="Docker jetzt installieren (offizielles Skript get.docker.com)?"
    [need_docker]="Ohne Docker kann die Cloud nicht installiert werden."
    [docker_group]="Dein Konto hat keinen Zugriff auf Docker – es wird zur Gruppe „docker“ hinzugefügt (nach erneuter Anmeldung ohne sudo)."
    [docker_down]="Docker antwortet nicht. Starte ihn: sudo systemctl enable --now docker"
    [no_compose]="„docker compose“ fehlt. Installiere: sudo apt install docker-compose-plugin (oder ein neueres Docker)."
    [ask_home]="Wo sollen die Cloud-Daten gespeichert werden (Datenbank, Cover, heruntergeladene Musik)"
    [ask_music]="Ordner mit deiner Musik (nur Lesezugriff)"
    [create_music]="Ordner %s existiert nicht – anlegen (leer, Musik kannst du später hinzufügen)?"
    [need_music]="Gib einen vorhandenen Musikordner an."
    [port_busy_suggest]="Port %s wird von einem anderen Programm benutzt – Vorschlag: %s."
    [ask_port]="Port der Cloud"
    [port_busy]="Port %s ist belegt – wähle einen anderen."
    [summary_title]="Zusammenfassung:"
    [summary_line]="Daten: %s · Musik: %s · Port: %s · Sprache: %s"
    [ask_install]="Installieren?"
    [aborted]="Abgebrochen – nichts wurde geändert."
    [building]="Die Cloud wird gebaut (beim ersten Mal einige Minuten – Python, ffmpeg, yt-dlp)…"
    [waiting]="Warte, bis die Cloud gestartet ist…"
    [not_responding]="Die Cloud antwortet nicht. Protokoll: %s --logs"
    [ufw]="Die Firewall (ufw) ist aktiv – Port %s für das Heimnetz öffnen?"
    [firewalld]="Die Firewall (firewalld) ist aktiv – Port %s für das Heimnetz öffnen?"
    [port_opened]="Port %s in der Firewall geöffnet."
    [selinux]="SELinux ist aktiv – die Cloud-Ordner werden passend gekennzeichnet."
    [running]="Die Nati-Cloud läuft!"
    [addr]="Adresse für die App:  %s"
    [music_line]="Musik:                %s (nur Lesezugriff)"
    [data_line]="Cloud-Daten:          %s"
    [first_connect]="Gib diese Adresse in der Nati-App ein – bei der ersten Verbindung legst du das Administratorkonto an."
    [scan_soon]="Die Musik aus dem Ordner erscheint in Kürze (Scan im Hintergrund)."
    [updated_kept]="Konten, Playlists und Musik sind unverändert – die Apps verbinden sich von selbst."
    [online_hint]="Musik-Downloads aus dem Internet schaltest du in der App ein/aus: Einstellungen → Cloud."
    [later]="Status: %s --status · Update: neues Paket → ./install.sh --update"
    [security]="Zugriff von außerhalb und HTTPS: Ordner docs/ im Paket"
    [not_installed]="Keine Nati-Cloud-Installation gefunden (%s). Zuerst: %s"
    [take_ownership]="Die Cloud-Dateien in %s werden deinem Konto zugewiesen (einmalig, danach Updates ohne sudo)…"
    [missing_server]="Im Paket fehlt der Servercode (%s)."
    [updating]="Update der Nati-Cloud: %s → %s (Datenbank und Musik bleiben erhalten)"
    [update_failed]="Die Cloud antwortet nach dem Update nicht. Protokoll: %s --logs"
    [updated]="Aktualisiert auf %s."
    [status_ok]="Die Cloud läuft: %s  (%s)"
    [status_down]="Die Cloud antwortet nicht auf Port %s."
    [disk_data]="Daten"
    [disk_free]="frei auf dem Datenträger"
    [uninstalling]="Die Nati-Cloud wird aus %s entfernt (Container und Programm)."
    [continue]="Fortfahren?"
    [aborted_short]="Abgebrochen."
    [delete_data]="Auch die DATEN der Cloud löschen (%s: Konten, Playlists, heruntergeladene Musik)? Das lässt sich nicht rückgängig machen"
    [data_deleted]="Daten gelöscht."
    [data_kept]="Die Daten bleiben in %s – eine Neuinstallation mit demselben Ordner übernimmt sie."
    [uninstalled]="Nati-Cloud deinstalliert. Deine Musik (%s) wurde nicht angerührt."
    [unknown_option]="Unbekannte Option: %s (siehe: %s --help)"
    [yes_no]="J/n"
    [no_yes]="j/N"
  ) ;;
  es) M=(
    [title]="Instalación de la nube Nati %s"
    [already]="La nube Nati ya está instalada (%s)."
    [already_hint]="Nueva versión: %s --update   ·   estado: %s --status"
    [no_docker]="No hay Docker – la nube Nati funciona en un contenedor Docker."
    [install_docker]="¿Instalar Docker ahora (script oficial get.docker.com)?"
    [need_docker]="Sin Docker no se puede instalar la nube."
    [docker_group]="Tu cuenta no tiene acceso a Docker – la añado al grupo «docker» (tras volver a iniciar sesión, sin sudo)."
    [docker_down]="Docker no responde. Inícialo: sudo systemctl enable --now docker"
    [no_compose]="Falta «docker compose». Instala: sudo apt install docker-compose-plugin (o un Docker más reciente)."
    [ask_home]="Dónde guardar los datos de la nube (base de datos, carátulas, música descargada)"
    [ask_music]="Carpeta con tu música (solo lectura)"
    [create_music]="La carpeta %s no existe – ¿crearla (vacía, añadirás música más tarde)?"
    [need_music]="Indica una carpeta de música existente."
    [port_busy_suggest]="El puerto %s lo usa otro programa – propongo %s."
    [ask_port]="Puerto de la nube"
    [port_busy]="El puerto %s está ocupado – elige otro."
    [summary_title]="Resumen:"
    [summary_line]="datos: %s · música: %s · puerto: %s · idioma: %s"
    [ask_install]="¿Instalar?"
    [aborted]="Cancelado – no he cambiado nada."
    [building]="Construyendo la nube (la primera vez unos minutos – Python, ffmpeg, yt-dlp)…"
    [waiting]="Esperando a que la nube arranque…"
    [not_responding]="La nube no responde. Registro: %s --logs"
    [ufw]="El cortafuegos (ufw) está activo – ¿abrir el puerto %s para la red doméstica?"
    [firewalld]="El cortafuegos (firewalld) está activo – ¿abrir el puerto %s para la red doméstica?"
    [port_opened]="Puerto %s abierto en el cortafuegos."
    [selinux]="SELinux está activo – las carpetas de la nube se etiquetarán correctamente."
    [running]="¡La nube Nati está funcionando!"
    [addr]="Dirección para la app:  %s"
    [music_line]="Música:                 %s (solo lectura)"
    [data_line]="Datos de la nube:       %s"
    [first_connect]="Escribe esta dirección en la app Nati – en la primera conexión creas la cuenta de administrador."
    [scan_soon]="La música de la carpeta aparecerá en breve (escaneo en segundo plano)."
    [updated_kept]="Cuentas, listas y música siguen igual – las apps se conectarán solas."
    [online_hint]="Las descargas de música de internet se activan/desactivan en la app: Ajustes → Nube."
    [later]="Estado: %s --status · actualizar: paquete nuevo → ./install.sh --update"
    [security]="Acceso desde fuera de casa y HTTPS: carpeta docs/ del paquete"
    [not_installed]="No encontré una instalación de la nube Nati (%s). Primero: %s"
    [take_ownership]="Asignando los archivos de la nube en %s a tu cuenta (una vez, luego actualizaciones sin sudo)…"
    [missing_server]="Al paquete le falta el código del servidor (%s)."
    [updating]="Actualizando la nube Nati: %s → %s (la base de datos y la música se conservan)"
    [update_failed]="La nube no responde tras actualizar. Registro: %s --logs"
    [updated]="Actualizado a %s."
    [status_ok]="La nube funciona: %s  (%s)"
    [status_down]="La nube no responde en el puerto %s."
    [disk_data]="datos"
    [disk_free]="libre en disco"
    [uninstalling]="Eliminando la nube Nati de %s (contenedor y programa)."
    [continue]="¿Continuar?"
    [aborted_short]="Cancelado."
    [delete_data]="¿Borrar también los DATOS de la nube (%s: cuentas, listas, música descargada)? No se puede deshacer"
    [data_deleted]="Datos borrados."
    [data_kept]="Los datos siguen en %s – reinstalar con la misma carpeta los recupera."
    [uninstalled]="Nube Nati desinstalada. Tu música (%s) no se ha tocado."
    [unknown_option]="Opción desconocida: %s (ver: %s --help)"
    [yes_no]="S/n"
    [no_yes]="s/N"
  ) ;;
  fr) M=(
    [title]="Installation du cloud Nati %s"
    [already]="Le cloud Nati est déjà installé (%s)."
    [already_hint]="Nouvelle version : %s --update   ·   état : %s --status"
    [no_docker]="Docker est absent – le cloud Nati fonctionne dans un conteneur Docker."
    [install_docker]="Installer Docker maintenant (script officiel get.docker.com) ?"
    [need_docker]="Sans Docker, impossible d’installer le cloud."
    [docker_group]="Votre compte n’a pas accès à Docker – je l’ajoute au groupe « docker » (après reconnexion, sans sudo)."
    [docker_down]="Docker ne répond pas. Démarrez-le : sudo systemctl enable --now docker"
    [no_compose]="« docker compose » manque. Installez : sudo apt install docker-compose-plugin (ou un Docker plus récent)."
    [ask_home]="Où enregistrer les données du cloud (base, pochettes, musique téléchargée)"
    [ask_music]="Dossier avec votre musique (lecture seule)"
    [create_music]="Le dossier %s n’existe pas – le créer (vide, vous ajouterez la musique plus tard) ?"
    [need_music]="Indiquez un dossier de musique existant."
    [port_busy_suggest]="Le port %s est utilisé par un autre programme – je propose %s."
    [ask_port]="Port du cloud"
    [port_busy]="Le port %s est occupé – choisissez-en un autre."
    [summary_title]="Récapitulatif :"
    [summary_line]="données : %s · musique : %s · port : %s · langue : %s"
    [ask_install]="Installer ?"
    [aborted]="Annulé – rien n’a été modifié."
    [building]="Construction du cloud (la première fois quelques minutes – Python, ffmpeg, yt-dlp)…"
    [waiting]="En attente du démarrage du cloud…"
    [not_responding]="Le cloud ne répond pas. Journal : %s --logs"
    [ufw]="Le pare-feu (ufw) est actif – ouvrir le port %s pour le réseau domestique ?"
    [firewalld]="Le pare-feu (firewalld) est actif – ouvrir le port %s pour le réseau domestique ?"
    [port_opened]="Port %s ouvert dans le pare-feu."
    [selinux]="SELinux est actif – les dossiers du cloud seront étiquetés correctement."
    [running]="Le cloud Nati fonctionne !"
    [addr]="Adresse pour l’app :  %s"
    [music_line]="Musique :             %s (lecture seule)"
    [data_line]="Données du cloud :    %s"
    [first_connect]="Saisissez cette adresse dans l’app Nati – à la première connexion, vous créez le compte administrateur."
    [scan_soon]="La musique du dossier apparaîtra sous peu (analyse en arrière-plan)."
    [updated_kept]="Comptes, playlists et musique sont inchangés – les apps se reconnecteront seules."
    [online_hint]="Le téléchargement de musique depuis internet s’active/désactive dans l’app : Réglages → Cloud."
    [later]="État : %s --status · mise à jour : nouveau paquet → ./install.sh --update"
    [security]="Accès hors de la maison et HTTPS : dossier docs/ du paquet"
    [not_installed]="Aucune installation du cloud Nati trouvée (%s). D’abord : %s"
    [take_ownership]="Attribution des fichiers du cloud dans %s à votre compte (une fois, ensuite mises à jour sans sudo)…"
    [missing_server]="Le code du serveur manque dans le paquet (%s)."
    [updating]="Mise à jour du cloud Nati : %s → %s (la base et la musique sont conservées)"
    [update_failed]="Le cloud ne répond pas après la mise à jour. Journal : %s --logs"
    [updated]="Mis à jour vers %s."
    [status_ok]="Le cloud fonctionne : %s  (%s)"
    [status_down]="Le cloud ne répond pas sur le port %s."
    [disk_data]="données"
    [disk_free]="libre sur le disque"
    [uninstalling]="Suppression du cloud Nati de %s (conteneur et programme)."
    [continue]="Continuer ?"
    [aborted_short]="Annulé."
    [delete_data]="Supprimer aussi les DONNÉES du cloud (%s : comptes, playlists, musique téléchargée) ? Irréversible"
    [data_deleted]="Données supprimées."
    [data_kept]="Les données restent dans %s – une réinstallation avec le même dossier les récupère."
    [uninstalled]="Cloud Nati désinstallé. Votre musique (%s) n’a pas été touchée."
    [unknown_option]="Option inconnue : %s (voir : %s --help)"
    [yes_no]="O/n"
    [no_yes]="o/N"
  ) ;;
  pt) M=(
    [title]="Instalação da nuvem Nati %s"
    [already]="A nuvem Nati já está instalada (%s)."
    [already_hint]="Nova versão: %s --update   ·   estado: %s --status"
    [no_docker]="O Docker não está instalado – a nuvem Nati funciona num contêiner Docker."
    [install_docker]="Instalar o Docker agora (script oficial get.docker.com)?"
    [need_docker]="Sem o Docker não é possível instalar a nuvem."
    [docker_group]="A sua conta não tem acesso ao Docker – vou adicioná-la ao grupo «docker» (após novo login, sem sudo)."
    [docker_down]="O Docker não responde. Inicie-o: sudo systemctl enable --now docker"
    [no_compose]="Falta o «docker compose». Instale: sudo apt install docker-compose-plugin (ou um Docker mais recente)."
    [ask_home]="Onde guardar os dados da nuvem (base de dados, capas, música transferida)"
    [ask_music]="Pasta com a sua música (só leitura)"
    [create_music]="A pasta %s não existe – criá-la (vazia, adiciona a música mais tarde)?"
    [need_music]="Indique uma pasta de música existente."
    [port_busy_suggest]="A porta %s está a ser usada por outro programa – proponho %s."
    [ask_port]="Porta da nuvem"
    [port_busy]="A porta %s está ocupada – escolha outra."
    [summary_title]="Resumo:"
    [summary_line]="dados: %s · música: %s · porta: %s · idioma: %s"
    [ask_install]="Instalar?"
    [aborted]="Cancelado – nada foi alterado."
    [building]="A construir a nuvem (na primeira vez alguns minutos – Python, ffmpeg, yt-dlp)…"
    [waiting]="À espera de que a nuvem arranque…"
    [not_responding]="A nuvem não responde. Registo: %s --logs"
    [ufw]="A firewall (ufw) está ativa – abrir a porta %s para a rede doméstica?"
    [firewalld]="A firewall (firewalld) está ativa – abrir a porta %s para a rede doméstica?"
    [port_opened]="Porta %s aberta na firewall."
    [selinux]="O SELinux está ativo – as pastas da nuvem serão etiquetadas corretamente."
    [running]="A nuvem Nati está a funcionar!"
    [addr]="Endereço para a app:  %s"
    [music_line]="Música:               %s (só leitura)"
    [data_line]="Dados da nuvem:       %s"
    [first_connect]="Introduza este endereço na app Nati – na primeira ligação cria a conta de administrador."
    [scan_soon]="A música da pasta aparecerá em breve (análise em segundo plano)."
    [updated_kept]="Contas, listas e música mantêm-se – as apps voltam a ligar-se sozinhas."
    [online_hint]="As transferências de música da internet ativam-se/desativam-se na app: Definições → Nuvem."
    [later]="Estado: %s --status · atualizar: pacote novo → ./install.sh --update"
    [security]="Acesso fora de casa e HTTPS: pasta docs/ do pacote"
    [not_installed]="Não encontrei uma instalação da nuvem Nati (%s). Primeiro: %s"
    [take_ownership]="A atribuir os ficheiros da nuvem em %s à sua conta (uma vez, depois atualizações sem sudo)…"
    [missing_server]="Falta o código do servidor no pacote (%s)."
    [updating]="A atualizar a nuvem Nati: %s → %s (a base de dados e a música mantêm-se)"
    [update_failed]="A nuvem não responde após a atualização. Registo: %s --logs"
    [updated]="Atualizado para %s."
    [status_ok]="A nuvem está a funcionar: %s  (%s)"
    [status_down]="A nuvem não responde na porta %s."
    [disk_data]="dados"
    [disk_free]="livre no disco"
    [uninstalling]="A remover a nuvem Nati de %s (contêiner e programa)."
    [continue]="Continuar?"
    [aborted_short]="Cancelado."
    [delete_data]="Apagar também os DADOS da nuvem (%s: contas, listas, música transferida)? Não é reversível"
    [data_deleted]="Dados apagados."
    [data_kept]="Os dados ficam em %s – reinstalar com a mesma pasta recupera-os."
    [uninstalled]="Nuvem Nati desinstalada. A sua música (%s) não foi alterada."
    [unknown_option]="Opção desconhecida: %s (ver: %s --help)"
    [yes_no]="S/n"
    [no_yes]="s/N"
  ) ;;
  *) M=(
    [title]="Nati Cloud installation %s"
    [already]="Nati Cloud is already installed (%s)."
    [already_hint]="New version: %s --update   ·   status: %s --status"
    [no_docker]="Docker is missing – Nati Cloud runs in a Docker container."
    [install_docker]="Install Docker now (official get.docker.com script)?"
    [need_docker]="The cloud cannot be installed without Docker."
    [docker_group]="Your account has no access to Docker – adding it to the “docker” group (no sudo needed after you log in again)."
    [docker_down]="Docker is not responding. Start it: sudo systemctl enable --now docker"
    [no_compose]="“docker compose” is missing. Install: sudo apt install docker-compose-plugin (or a newer Docker)."
    [ask_home]="Where to keep the cloud data (database, covers, downloaded music)"
    [ask_music]="Folder with your music (read-only)"
    [create_music]="Folder %s does not exist – create it (empty, add music later)?"
    [need_music]="Enter an existing music folder."
    [port_busy_suggest]="Port %s is used by another program – suggesting %s."
    [ask_port]="Cloud port"
    [port_busy]="Port %s is in use – choose another one."
    [summary_title]="Summary:"
    [summary_line]="data: %s · music: %s · port: %s · language: %s"
    [ask_install]="Install?"
    [aborted]="Cancelled – nothing was changed."
    [building]="Building the cloud (a few minutes the first time – Python, ffmpeg, yt-dlp)…"
    [waiting]="Waiting for the cloud to start…"
    [not_responding]="The cloud is not responding. Log: %s --logs"
    [ufw]="The firewall (ufw) is active – open port %s for your home network?"
    [firewalld]="The firewall (firewalld) is active – open port %s for your home network?"
    [port_opened]="Port %s opened in the firewall."
    [selinux]="SELinux is enabled – the cloud folders will be labelled accordingly."
    [running]="Nati Cloud is running!"
    [addr]="Address for the app:  %s"
    [music_line]="Music:                %s (read-only)"
    [data_line]="Cloud data:           %s"
    [first_connect]="Enter this address in the Nati app – on the first connection you create the administrator account."
    [scan_soon]="Music from the folder will appear shortly (scanning in the background)."
    [updated_kept]="Accounts, playlists and music are unchanged – the apps will reconnect on their own."
    [online_hint]="Downloading music from the internet is switched on/off in the app: Settings → Cloud."
    [later]="Status: %s --status · update: new package → ./install.sh --update"
    [security]="Access from outside your home and HTTPS: docs/ folder in the package"
    [not_installed]="No Nati Cloud installation found (%s). First run: %s"
    [take_ownership]="Giving the cloud files in %s to your account (once – later updates need no sudo)…"
    [missing_server]="The server code is missing from the package (%s)."
    [updating]="Updating Nati Cloud: %s → %s (database and music are kept)"
    [update_failed]="The cloud is not responding after the update. Log: %s --logs"
    [updated]="Updated to %s."
    [status_ok]="The cloud is running: %s  (%s)"
    [status_down]="The cloud is not responding on port %s."
    [disk_data]="data"
    [disk_free]="free on disk"
    [uninstalling]="Removing Nati Cloud from %s (container and program)."
    [continue]="Continue?"
    [aborted_short]="Cancelled."
    [delete_data]="Also delete the cloud DATA (%s: accounts, playlists, downloaded music)? This cannot be undone"
    [data_deleted]="Data deleted."
    [data_kept]="Data kept in %s – reinstalling with the same folder brings it back."
    [uninstalled]="Nati Cloud uninstalled. Your music (%s) was not touched."
    [unknown_option]="Unknown option: %s (see: %s --help)"
    [yes_no]="Y/n"
    [no_yes]="y/N"
  ) ;;
  esac
}

# t KEY [args…] – translated message (printf-style placeholders)
t() {
  local key="$1"
  shift
  # shellcheck disable=SC2059
  printf "${M[$key]:-$key}" "$@"
}

info() { printf '%s♪%s %s\n' "$c_info" "$c_off" "$*"; }
ok() { printf '%s✓%s %s\n' "$c_ok" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_warn" "$c_off" "$*"; }
fail() { printf '%s✗%s %s\n' "$c_err" "$c_off" "$*" >&2; exit 1; }

detect_lang() {
  local l="${NATI_LANG:-${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}}"
  l="${l:0:2}"
  for x in "${LANGS[@]}"; do [[ $x == "$l" ]] && { printf '%s\n' "$l"; return; }; done
  printf 'en\n'
}

YES=0
ACTION=install
for arg in "$@"; do
  case "$arg" in
    --yes|--tak|-y) YES=1 ;;
    --update|--aktualizuj) ACTION=update ;;
    --status|--stan) ACTION=status ;;
    --logs|--logi) ACTION=logs ;;
    --uninstall|--odinstaluj) ACTION=uninstall ;;
    -h|--help|--pomoc) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) load_messages "$(detect_lang)"; fail "$(t unknown_option "$arg" "$0")" ;;
  esac
done

# Question with a default answer (Enter = default; with --yes or without a terminal – the default).
ask() {
  local prompt="$1" def="$2" answer
  if [[ $YES == 1 || ! -t 0 ]]; then
    printf '%s\n' "$def"
    return
  fi
  read -r -p "$(printf '%s?%s %s [%s]: ' "$c_info" "$c_off" "$prompt" "$def")" answer </dev/tty
  printf '%s\n' "${answer:-$def}"
}

# Yes/no. Every supported language says “no” starting with n (no, nie, nein, non, não).
confirm() {
  local prompt="$1" def="${2:-y}" answer
  if [[ $YES == 1 || ! -t 0 ]]; then [[ $def == y ]]; return; fi
  read -r -p "$(printf '%s?%s %s [%s]: ' "$c_info" "$c_off" "$prompt" "$([[ $def == y ]] && t yes_no || t no_yes)")" answer </dev/tty
  [[ -z $answer ]] && { [[ $def == y ]]; return; }
  [[ ${answer,,} != n* ]]
}

choose_lang() {
  local def
  def="$(detect_lang)"
  NATI_LANG="$def"
  if [[ $YES == 1 || ! -t 0 || -n ${NATI_LANG_SET:-} ]]; then return; fi
  echo
  local i=1 di=1
  for l in "${LANGS[@]}"; do
    [[ $l == "$def" ]] && di=$i
    printf '   %s) %s\n' "$i" "${LANG_NAME[$l]}"
    i=$((i + 1))
  done
  local answer
  read -r -p "$(printf '%s?%s Language / Język / Sprache / Idioma / Langue [%s]: ' "$c_info" "$c_off" "$di")" answer </dev/tty
  answer="${answer:-$di}"
  if [[ $answer =~ ^[0-9]+$ ]] && ((answer >= 1 && answer <= ${#LANGS[@]})); then
    NATI_LANG="${LANGS[$((answer - 1))]}"
  fi
}

# sudo only where needed (folder in /srv, docker group…). Root – no sudo.
SUDO=""
[[ $(id -u) -ne 0 ]] && SUDO="sudo"
[[ ${NATI_BEZ_SUDO:-0} == 1 ]] && SUDO=""   # installer test without admin rights
as_root() { if [[ -n $SUDO ]]; then sudo "$@"; else "$@"; fi; }

# ------------------------------------------------------------------ Docker

DOCKER=(docker)
check_docker() {
  if ! command -v docker >/dev/null; then
    warn "$(t no_docker)"
    confirm "$(t install_docker)" y || fail "$(t need_docker)"
    command -v curl >/dev/null || as_root apt-get install -y curl
    curl -fsSL https://get.docker.com | as_root sh
  fi
  if ! docker info >/dev/null 2>&1; then
    if [[ -n $SUDO ]] && ! id -nG | grep -qw docker; then
      info "$(t docker_group)"
      as_root usermod -aG docker "$USER" || true
    fi
    DOCKER=(as_root docker)
    "${DOCKER[@]}" info >/dev/null 2>&1 || fail "$(t docker_down)"
  fi
  "${DOCKER[@]}" compose version >/dev/null 2>&1 || fail "$(t no_compose)"
}

compose() { "${DOCKER[@]}" compose -f "$NATI_HOME/docker-compose.yml" -p nati "$@"; }

# ------------------------------------------------------------------ helpers

port_busy() { (ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | awk '{print $4}' | grep -Eq "[:.]$1\$"; }

free_port() {
  local p="$1"
  while port_busy "$p"; do p=$((p + 10)); done
  printf '%s\n' "$p"
}

host_ip() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [[ -z $ip ]] && ip="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"
  printf '%s\n' "${ip:-SERVER-ADDRESS}"
}

selinux_on() { command -v getenforce >/dev/null && [[ $(getenforce 2>/dev/null) == Enforcing ]]; }

load_conf() {
  [[ -f $CONF_FILE ]] || fail "$(t not_installed "$CONF_FILE" "$0")"
  # shellcheck disable=SC1090
  source "$CONF_FILE"
  # shellcheck disable=SC1091
  [[ -f $NATI_HOME/nati.conf ]] && source "$NATI_HOME/nati.conf"
  # Before 0.18.2 the data folder was always „dane”.
  NATI_DATA="${NATI_DATA:-$NATI_HOME/dane}"
  NATI_LANG="${NATI_LANG:-pl}"
  load_messages "$NATI_LANG"
  return 0
}

# Older installs (0.18.0): program files and the music folder belonged to root – give them to the user,
# so updates and copying music need no sudo. Without a sudo password – via Docker (an account in the
# “docker” group has that permission anyway).
take_ownership() {
  local paths=() p
  for p in "$NATI_HOME/program" "$NATI_HOME/docker-compose.yml" "$NATI_HOME/instaluj.sh" "$NATI_HOME/install.sh" "$NATI_MUSIC"; do
    [[ -e $p && ! -L $p && ! -w $p ]] || continue
    [[ $p == "$NATI_MUSIC" && $p != "$NATI_HOME"/* ]] && continue   # your own music folder – not touched
    paths+=("$p")
  done
  [[ ${#paths[@]} -eq 0 ]] && return 0
  info "$(t take_ownership "$NATI_HOME")"
  if [[ -z $SUDO ]] || sudo -n true 2>/dev/null; then
    as_root chown -R "$(id -u):$(id -g)" "${paths[@]}"
  elif [[ ${DOCKER[0]} == docker ]]; then
    local mounts=() args=()
    for p in "${paths[@]}"; do mounts+=(-v "$p:/x$p"); args+=("/x$p"); done
    docker run --rm --user 0 --entrypoint chown "${mounts[@]}" "nati-chmura:${NATI_VERSION:-latest}" \
      -R "$(id -u):$(id -g)" "${args[@]}"
  else
    as_root chown -R "$(id -u):$(id -g)" "${paths[@]}"
  fi
}

wait_ready() {
  local port="$1" i
  info "$(t waiting)"
  for i in $(seq 1 90); do
    if curl -fsS "http://127.0.0.1:$port/api/info" >/dev/null 2>&1; then return 0; fi
    sleep 2
  done
  return 1
}

# Program (server code) from this package → $NATI_HOME/program (the old one is replaced entirely).
copy_program() {
  [[ -f $HERE/server/Dockerfile ]] || fail "$(t missing_server "$HERE/server")"
  rm -rf "$NATI_HOME/program.new"
  mkdir -p "$NATI_HOME/program.new"
  cp -r "$HERE/server/." "$NATI_HOME/program.new/"
  rm -rf "$NATI_HOME/program"
  mv "$NATI_HOME/program.new" "$NATI_HOME/program"
  if [[ $HERE != "$NATI_HOME" ]]; then
    rm -f "$NATI_HOME/install.sh" "$NATI_HOME/instaluj.sh"
    cp "$HERE/install.sh" "$NATI_HOME/install.sh"
    ln -s install.sh "$NATI_HOME/instaluj.sh"
  fi
  chmod +x "$NATI_HOME/install.sh"
}

write_compose() {
  local online="true" music_opt=":ro" data_opt=""
  [[ ${NATI_INTERNET:-t} == n* ]] && online="false"
  # SELinux (Fedora, RHEL): without labels the container cannot read the folders.
  if selinux_on; then music_opt=":ro,z"; data_opt=":Z"; fi
  cat > "$NATI_HOME/docker-compose.yml" <<EOF
# Nati Cloud – generated by install.sh (./install.sh --update writes it again).
services:
  nati:
    build: ./program
    image: nati-chmura:$VERSION
    container_name: nati
    ports:
      - "$NATI_PORT:8080"
    volumes:
      - "$NATI_MUSIC:/music$music_opt"   # your music – read-only (Nati never changes these files)
      - "$NATI_DATA:/data$data_opt"   # database, covers, downloaded music, trash, backups
    user: "$NATI_UID:$NATI_GID"
    environment:
      NATI_ONLINE_ENABLED: "$online"
      HOME: /data/.dom            # container home (yt-dlp cache) – inside the cloud data
    restart: unless-stopped      # starts again after a server reboot and after restoring a backup
EOF
}

save_conf() {
  cat > "$NATI_HOME/nati.conf" <<EOF
# Nati Cloud – saved by install.sh
NATI_HOME="$NATI_HOME"
NATI_DATA="$NATI_DATA"
NATI_MUSIC="$NATI_MUSIC"
NATI_PORT="$NATI_PORT"
NATI_UID="$NATI_UID"
NATI_GID="$NATI_GID"
NATI_INTERNET="${NATI_INTERNET:-t}"
NATI_LANG="$NATI_LANG"
NATI_VERSION="$VERSION"
EOF
}

open_firewall() {
  [[ ${NATI_FIREWALL:-1} == 0 ]] && return 0   # NATI_FIREWALL=0 – nie ruszaj zapory (np. testy instalatora)
  if command -v ufw >/dev/null && as_root ufw status 2>/dev/null | grep -q "Status: active"; then
    if confirm "$(t ufw "$NATI_PORT")" y; then
      as_root ufw allow "$NATI_PORT/tcp" comment "Nati" >/dev/null && ok "$(t port_opened "$NATI_PORT")"
    fi
  elif command -v firewall-cmd >/dev/null && as_root firewall-cmd --state >/dev/null 2>&1; then
    if confirm "$(t firewalld "$NATI_PORT")" y; then
      as_root firewall-cmd --permanent --add-port="$NATI_PORT/tcp" >/dev/null \
        && as_root firewall-cmd --reload >/dev/null && ok "$(t port_opened "$NATI_PORT")"
    fi
  fi
}

summary() {
  echo
  ok "$(t running)"
  echo
  echo "   $(t addr "${c_ok}http://$(host_ip):$NATI_PORT${c_off}")"
  echo "   $(t music_line "$NATI_MUSIC")"
  echo "   $(t data_line "$NATI_DATA")"
  echo
  if [[ ${1:-} != update ]]; then
    echo "   $(t first_connect)"
    echo "   $(t scan_soon)"
  else
    echo "   $(t updated_kept)"
  fi
  echo "   $(t online_hint)"
  echo
  echo "   ${c_dim}$(t later "$NATI_HOME/install.sh")${c_off}"
  echo "   ${c_dim}$(t security)${c_off}"
}

# ------------------------------------------------------------------ actions

install() {
  choose_lang
  load_messages "$NATI_LANG"
  echo
  info "$(t title "$VERSION")"
  echo
  if [[ -f $CONF_FILE ]]; then
    warn "$(t already "$CONF_FILE")"
    echo "   $(t already_hint "$0" "$0")"
    exit 1
  fi
  check_docker
  ok "Docker: $("${DOCKER[@]}" --version | sed 's/,.*//')"

  NATI_HOME="${NATI_DATA_DIR:-${NATI_DANE:-$(ask "$(t ask_home)" "$DEFAULT_HOME")}}"
  NATI_HOME="${NATI_HOME%/}"
  NATI_DATA="$NATI_HOME/data"
  NATI_MUSIC="${NATI_MUSIC_DIR:-${NATI_MUZYKA:-$(ask "$(t ask_music)" "$NATI_HOME/music")}}"
  NATI_MUSIC="${NATI_MUSIC%/}"
  if [[ ! -d $NATI_MUSIC ]]; then
    if confirm "$(t create_music "$NATI_MUSIC")" y; then
      as_root mkdir -p "$NATI_MUSIC"
      as_root chown "$(id -u):$(id -g)" "$NATI_MUSIC"   # copy music there without sudo
    else
      fail "$(t need_music)"
    fi
  fi
  local suggested
  suggested="$(free_port "${NATI_PORT:-8080}")"
  [[ $suggested != "${NATI_PORT:-8080}" ]] && warn "$(t port_busy_suggest "${NATI_PORT:-8080}" "$suggested")"
  NATI_PORT="$(ask "$(t ask_port)" "$suggested")"
  port_busy "$NATI_PORT" && fail "$(t port_busy "$NATI_PORT")"
  # Internet downloads are switched on/off in the app (Settings → Cloud); NATI_INTERNET=n – start switched off.
  NATI_INTERNET="${NATI_INTERNET:-t}"
  NATI_UID="$(id -u)"
  NATI_GID="$(id -g)"

  echo
  info "$(t summary_title)"
  echo "   $(t summary_line "$NATI_HOME" "$NATI_MUSIC" "$NATI_PORT" "${LANG_NAME[$NATI_LANG]}")"
  confirm "$(t ask_install)" y || fail "$(t aborted)"

  as_root mkdir -p "$NATI_DATA"
  as_root chown "$NATI_UID:$NATI_GID" "$NATI_HOME" "$NATI_DATA"
  # Pointer in /etc (where the cloud lives) – the only file outside $NATI_HOME, written once.
  printf '# Nati Cloud – full settings: %s/nati.conf\nNATI_HOME="%s"\n' "$NATI_HOME" "$NATI_HOME" | as_root tee "$CONF_FILE" >/dev/null
  selinux_on && info "$(t selinux)"
  copy_program
  write_compose
  save_conf
  info "$(t building)"
  compose up -d --build
  wait_ready "$NATI_PORT" || fail "$(t not_responding "$NATI_HOME/install.sh")"
  open_firewall
  summary
}

update() {
  load_conf
  check_docker
  take_ownership
  local old="${NATI_VERSION:-}"
  info "$(t updating "${old:-?}" "$VERSION")"
  copy_program
  write_compose
  save_conf
  compose up -d --build
  wait_ready "$NATI_PORT" || fail "$(t update_failed "$NATI_HOME/install.sh")"
  # The previous version's image is no longer needed (about 800 MB).
  [[ -n $old && $old != "$VERSION" ]] && "${DOCKER[@]}" rmi "nati-chmura:$old" >/dev/null 2>&1 || true
  "${DOCKER[@]}" image prune -f >/dev/null 2>&1 || true
  ok "$(t updated "$VERSION")"
  summary update
}

status() {
  load_conf
  check_docker
  local info_json
  info_json="$(curl -fsS "http://127.0.0.1:$NATI_PORT/api/info" 2>/dev/null || true)"
  if [[ -n $info_json ]]; then
    ok "$(t status_ok "http://$(host_ip):$NATI_PORT" "$info_json")"
  else
    warn "$(t status_down "$NATI_PORT")"
  fi
  compose ps
  echo
  du -sh "$NATI_DATA" 2>/dev/null | awk -v l="$(t disk_data)" '{print "   " l ": " $1}' || true
  df -h "$NATI_HOME" | tail -1 | awk -v l="$(t disk_free)" '{print "   " l ": " $4}'
}

logs() {
  load_conf
  check_docker
  compose logs --tail 80 nati
}

uninstall() {
  load_conf
  check_docker
  warn "$(t uninstalling "$NATI_HOME")"
  confirm "$(t continue)" n || fail "$(t aborted_short)"
  compose down --rmi local || true
  rm -rf "$NATI_HOME/program" "$NATI_HOME/docker-compose.yml" "$NATI_HOME/install.sh" "$NATI_HOME/instaluj.sh" \
    "$NATI_HOME/nati.conf"
  if confirm "$(t delete_data "$NATI_DATA")" n; then
    as_root rm -rf "$NATI_DATA"
    ok "$(t data_deleted)"
  else
    ok "$(t data_kept "$NATI_DATA")"
  fi
  as_root rm -f "$CONF_FILE"
  ok "$(t uninstalled "$NATI_MUSIC")"
}

load_messages "$(detect_lang)"
case "$ACTION" in
  install) install ;;
  update) update ;;
  status) status ;;
  logs) logs ;;
  uninstall) uninstall ;;
esac
