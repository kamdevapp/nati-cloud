"""Utrzymanie chmury: zajęte miejsce, kopia zapasowa (utworzenie / przywrócenie) i całkowite wyczyszczenie.

Przywrócenie kopii i wyczyszczenie podmieniają bazę danych, więc nie robimy ich „na żywo”: zapisujemy
zlecenie w data/ i wykonujemy je przy następnym starcie serwera (apply_pending), zanim baza zostanie
otwarta. Serwer uruchamia się ponownie sam (restart z kodem 75 – Docker, uruchom.sh, systemd).

Kopia (.tar): manifest.json, nati.db (spójna kopia przez API SQLite), secret.key (urządzenia zostają
zalogowane), covers/ (okładki, zdjęcia, obrazki), opcjonalnie downloads/ (muzyka w chmurze: pobrana z internetu i wgrana z urządzeń).
Twojego folderu z muzyką kopia nie zawiera – przy przeprowadzce ścieżki do niego są przepisywane
na nowe (ten sam układ podfolderów), więc playlisty i ulubione zostają."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from ..messages import STORAGE_LABELS, render
from ..errors import CodedError
from .. import __version__
from ..config import Settings

log = logging.getLogger(__name__)

FORMAT = 1
RESTART_CODE = 75  # „uruchom ponownie” – wrapper (uruchom.sh) albo Docker podnosi serwer znowu
_RESTORE = "restore-pending.json"
_WIPE = "wipe-pending.json"


def backups_dir(s: Settings) -> Path:
    return s.data_dir / "backups"


# ---------------------------------------------------------------- miejsce

def dir_size(path: Path) -> tuple[int, int]:
    """(bajty, pliki) – rekurencyjnie, bez podążania za dowiązaniami."""
    total = files = 0
    if not path.exists():
        return 0, 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += (Path(root) / n).lstat().st_size
                files += 1
            except OSError:
                pass
    return total, files


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def storage(s: Settings, track_sizes: list[tuple[str, int]]) -> dict:
    """Co ile zajmuje. [track_sizes] – (ścieżka, rozmiar) utworów z bazy (szybciej niż liczenie plików)."""
    music_roots = [str(r.resolve()) for r in s.music_dirs]
    online_root = str(s.online_dir.resolve())
    own = own_n = online = online_n = 0
    for path, size in track_sizes:
        if path.startswith(online_root + os.sep):
            online, online_n = online + size, online_n + 1
        elif any(path.startswith(r + os.sep) for r in music_roots):
            own, own_n = own + size, own_n + 1
    db_files = [s.db_path, s.db_path.with_name(s.db_path.name + "-wal"), s.db_path.with_name(s.db_path.name + "-shm")]
    db_bytes = sum(p.stat().st_size for p in db_files if p.exists())
    items = [
        {"key": "music", "label": STORAGE_LABELS["music"], "bytes": own, "files": own_n, "in_data": False},
        {"key": "downloads", "label": STORAGE_LABELS["downloads"], "bytes": dir_size(s.online_dir)[0], "files": online_n,
         "in_data": _inside(s.online_dir, s.data_dir)},
    ]
    for key, label, path in (
        ("covers", STORAGE_LABELS["covers"], s.covers_dir),
        ("trash", STORAGE_LABELS["trash"], s.trash_dir),
        ("cache", STORAGE_LABELS["cache"], s.cache_dir),
        ("backups", STORAGE_LABELS["backups"], backups_dir(s)),
        ("tools", STORAGE_LABELS["tools"], s.tools_dir),
    ):
        b, n = dir_size(path)
        items.append({"key": key, "label": label, "bytes": b, "files": n, "in_data": True})
    items.insert(3, {"key": "database", "label": STORAGE_LABELS["database"], "bytes": db_bytes, "files": 1, "in_data": True})
    disk = shutil.disk_usage(s.data_dir)
    return {"items": items, "disk_total": disk.total, "disk_free": disk.free,
            "data_bytes": sum(i["bytes"] for i in items if i["in_data"])}


def clear(s: Settings, key: str) -> None:
    """Opróżnia kosz albo pamięć podręczną."""
    path = {"trash": s.trash_dir, "cache": s.cache_dir}[key]
    if path.exists():
        for child in path.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink(missing_ok=True)


# ---------------------------------------------------------------- kopia zapasowa

@dataclass
class BackupStatus:
    running: bool = False
    step: str = ""  # tekst angielski (zapasowy)
    step_code: str = ""  # kod kroku (messages.py) – aplikacja tłumaczy
    error: str | None = None
    error_code: str | None = None
    last: str | None = None  # nazwa ostatnio utworzonej kopii


class Backups:
    def __init__(self, settings: Settings):
        self.s = settings
        self.status = BackupStatus()
        self._lock = threading.Lock()

    def as_dict(self) -> dict:
        return asdict(self.status)

    def start(self, include_downloads: bool) -> bool:
        with self._lock:
            if self.status.running:
                return False
            self.status = BackupStatus(running=True, step=render("backup.step.prepare"), step_code="backup.step.prepare")
        threading.Thread(target=self._run, args=(include_downloads,), daemon=True, name="nati-backup").start()
        return True

    def _step(self, code: str) -> None:
        self.status.step, self.status.step_code = render(code), code

    def _run(self, include_downloads: bool) -> None:
        try:
            name = create_backup(self.s, include_downloads, progress=self._step)
            self.status = BackupStatus(last=name)
        except Exception as e:  # dysk pełny, brak uprawnień…
            log.exception("Kopia zapasowa nie powiodła się")
            self.status = BackupStatus(error=str(e), error_code=getattr(e, "code", None))


def _counts(db_file: Path) -> dict:
    con = sqlite3.connect(db_file)
    try:
        def n(table):
            try:
                return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            except sqlite3.Error:
                return 0
        return {"tracks": n("tracks"), "playlists": n("playlists"), "users": n("users"), "favorites": n("favorites")}
    finally:
        con.close()


def create_backup(s: Settings, include_downloads: bool, progress=lambda _: None) -> str:
    out_dir = backups_dir(s)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"nati-kopia-{datetime.now():%Y-%m-%d-%H%M%S}{'-z-muzyka' if include_downloads else ''}.tar"
    part = out_dir / (name + ".part")
    with tempfile.TemporaryDirectory(dir=s.data_dir) as tmp:
        progress("backup.step.database")
        db_copy = Path(tmp) / "nati.db"
        src, dst = sqlite3.connect(s.db_path), sqlite3.connect(db_copy)
        try:
            src.backup(dst)  # spójna kopia, także gdy serwer w tym czasie zapisuje
        finally:
            dst.close()
            src.close()
        manifest = {
            "format": FORMAT, "app": "nati", "version": __version__, "created": datetime.now().isoformat(),
            "music_dirs": [str(r.resolve()) for r in s.music_dirs], "online_dir": str(s.online_dir.resolve()),
            "include_downloads": include_downloads, "counts": _counts(db_copy),
        }
        (Path(tmp) / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        with tarfile.open(part, "w") as tar:
            tar.add(Path(tmp) / "manifest.json", "manifest.json")
            tar.add(db_copy, "nati.db")
            key = s.data_dir / "secret.key"
            if key.exists():
                tar.add(key, "secret.key")
            progress("backup.step.covers")
            if s.covers_dir.exists():
                tar.add(s.covers_dir, "covers")
            if include_downloads and s.online_dir.exists():
                progress("backup.step.music")
                tar.add(s.online_dir, "downloads")
    part.rename(out_dir / name)
    return name


def read_manifest(path: Path) -> dict:
    """Manifest kopii; ValueError, gdy plik nie jest kopią Nati."""
    try:
        with tarfile.open(path) as tar:
            member = tar.getmember("manifest.json")
            data = json.loads(tar.extractfile(member).read())
            names = set(tar.getnames())
    except (tarfile.TarError, KeyError, json.JSONDecodeError, OSError) as e:
        raise CodedError("backup.invalid") from e
    if data.get("app") != "nati" or "nati.db" not in names:
        raise CodedError("backup.invalid")
    if data.get("format", 0) > FORMAT:
        raise CodedError("backup.newer_version")
    return data


def list_backups(s: Settings) -> list[dict]:
    out = []
    d = backups_dir(s)
    if not d.exists():
        return out
    for f in sorted(d.glob("*.tar"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            m = read_manifest(f)
        except ValueError:
            continue
        out.append({"name": f.name, "bytes": f.stat().st_size, "created": m.get("created"),
                    "version": m.get("version"), "include_downloads": m.get("include_downloads", False),
                    "counts": m.get("counts", {})})
    return out


def backup_path(s: Settings, name: str) -> Path:
    """Plik kopii po nazwie (bez wychodzenia poza folder kopii)."""
    path = backups_dir(s) / name
    if "/" in name or "\\" in name or not name.endswith(".tar") or not path.is_file():
        raise FileNotFoundError(name)
    return path


# ---------------------------------------------------------------- zlecenia na następny start

def schedule_restore(s: Settings, name: str) -> dict:
    path = backup_path(s, name)
    manifest = read_manifest(path)
    (s.data_dir / _RESTORE).write_text(json.dumps({"backup": str(path)}))
    (s.data_dir / _WIPE).unlink(missing_ok=True)
    return manifest


def schedule_wipe(s: Settings, include_downloads: bool) -> None:
    (s.data_dir / _WIPE).write_text(json.dumps({"downloads": include_downloads}))
    (s.data_dir / _RESTORE).unlink(missing_ok=True)


def pending(s: Settings) -> str | None:
    if (s.data_dir / _RESTORE).exists():
        return "restore"
    if (s.data_dir / _WIPE).exists():
        return "wipe"
    return None


def _remove_db(s: Settings) -> None:
    for suffix in ("", "-wal", "-shm"):
        s.db_path.with_name(s.db_path.name + suffix).unlink(missing_ok=True)


def _empty(path: Path) -> None:
    if path.exists():
        for child in path.iterdir():
            shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink(missing_ok=True)


def apply_pending(s: Settings) -> str | None:
    """Wykonuje zlecone przywrócenie / wyczyszczenie. Wywoływane przy starcie, zanim baza zostanie otwarta."""
    restore, wipe = s.data_dir / _RESTORE, s.data_dir / _WIPE
    if restore.exists():
        try:
            backup = Path(json.loads(restore.read_text())["backup"])
            _restore(s, backup)
            log.warning("Przywrócono kopię zapasową %s", backup.name)
            return "restore"
        except Exception:
            log.exception("Przywracanie kopii nie powiodło się – serwer startuje z dotychczasowymi danymi")
            return None
        finally:
            restore.unlink(missing_ok=True)
    if wipe.exists():
        downloads = json.loads(wipe.read_text()).get("downloads", False)
        _remove_db(s)
        for path in (s.covers_dir, s.trash_dir, s.cache_dir, s.data_dir / "tmp"):
            _empty(path)
        if downloads:
            _empty(s.online_dir)
        (s.data_dir / "secret.key").unlink(missing_ok=True)
        _reload_secret(s)
        wipe.unlink(missing_ok=True)
        log.warning("Chmura wyczyszczona%s", " razem z pobraną muzyką" if downloads else "")
        return "wipe"
    return None


def _reload_secret(s: Settings) -> None:
    """Klucz tokenów z pliku (nowy po wyczyszczeniu, z kopii po przywróceniu) – chyba że podany w konfiguracji."""
    if os.environ.get("NATI_SECRET_KEY"):
        return
    key_file = s.data_dir / "secret.key"
    if not key_file.exists():
        import secrets as _secrets
        key_file.write_text(_secrets.token_urlsafe(48))
        key_file.chmod(0o600)
    s.secret_key = key_file.read_text().strip()


def _safe_members(tar: tarfile.TarFile, prefix: str) -> list[tarfile.TarInfo]:
    out = []
    for m in tar.getmembers():
        if not (m.name == prefix or m.name.startswith(prefix + "/")):
            continue
        p = Path(m.name)
        if p.is_absolute() or ".." in p.parts or not (m.isfile() or m.isdir()):
            continue  # nic poza folderem docelowym, bez dowiązań
        out.append(m)
    return out


def _restore(s: Settings, backup: Path) -> None:
    manifest = read_manifest(backup)
    with tempfile.TemporaryDirectory(dir=s.data_dir) as tmp, tarfile.open(backup) as tar:
        tmp_path = Path(tmp)
        for prefix in ("nati.db", "secret.key", "covers", "downloads"):
            members = _safe_members(tar, prefix)
            if members:
                tar.extractall(tmp_path, members=members, filter="data")
        _remove_db(s)
        shutil.move(str(tmp_path / "nati.db"), s.db_path)
        if (tmp_path / "covers").exists():
            shutil.rmtree(s.covers_dir, ignore_errors=True)
            shutil.move(str(tmp_path / "covers"), s.covers_dir)
        if (tmp_path / "downloads").exists():
            s.online_dir.mkdir(parents=True, exist_ok=True)
            for child in (tmp_path / "downloads").iterdir():
                target = s.online_dir / child.name
                if target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
                shutil.move(str(child), target)
        if (tmp_path / "secret.key").exists() and not os.environ.get("NATI_SECRET_KEY"):
            shutil.move(str(tmp_path / "secret.key"), s.data_dir / "secret.key")
            (s.data_dir / "secret.key").chmod(0o600)
    _reload_secret(s)
    remap_paths(s.db_path, _path_map(s, manifest))


def _path_map(s: Settings, manifest: dict) -> list[tuple[str, str]]:
    """Stare katalogi (z kopii) → obecne: foldery z muzyką po kolei, muzyka w chmurze."""
    old_music = manifest.get("music_dirs") or []
    new_music = [str(r.resolve()) for r in s.music_dirs]
    pairs = [(o, n) for o, n in zip(old_music, new_music) if o != n]
    old_online, new_online = manifest.get("online_dir"), str(s.online_dir.resolve())
    if old_online and old_online != new_online:
        pairs.append((old_online, new_online))
    return pairs


def remap_paths(db_file: Path, pairs: list[tuple[str, str]]) -> None:
    """Przepisuje ścieżki utworów (i tagów / tekstów zapisanych w Nati) na nowe katalogi."""
    if not pairs:
        return
    con = sqlite3.connect(db_file)
    try:
        for old, new in pairs:
            for table in ("tracks", "track_edits", "track_lyrics"):
                con.execute(f"UPDATE {table} SET path = ? || substr(path, ?) WHERE path LIKE ? ESCAPE '\\'",
                            (new, len(old) + 1, old.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                             + os.sep + "%"))
        con.commit()
    finally:
        con.close()


def restart_later(delay: float = 0.6) -> None:
    """Kończy proces z kodem „uruchom ponownie” po wysłaniu odpowiedzi."""
    def _exit():
        time.sleep(delay)
        log.warning("Ponowne uruchomienie serwera")
        os._exit(RESTART_CODE)
    threading.Thread(target=_exit, daemon=True).start()
