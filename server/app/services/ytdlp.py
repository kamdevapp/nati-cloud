"""yt-dlp: uruchamiany jako osobny proces (łatwo przerwać, łatwo zaktualizować).

Aktualna wersja instalowana jest do data/tools (pip --target), bo obraz Dockera
jest tylko do odczytu. Jeśli jej nie ma – używamy wersji dołączonej do serwera."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from ..errors import CodedError

log = logging.getLogger(__name__)

UPDATE_EVERY = 24 * 3600


class YtDlpError(CodedError):
    def __init__(self, code: str = "ytdlp.error", *, blocked: bool = False, needs_login: bool = False, **params):
        super().__init__(code, 502, **params)
        self.blocked = blocked  # YouTube blokuje adres (za dużo zapytań / „potwierdź, że nie jesteś botem”)
        self.needs_login = needs_login  # utwór wymaga zalogowania (np. ograniczenie wiekowe)


def is_blocked(msg: str) -> bool:
    low = msg.lower()
    return ("not a bot" in low or "http error 429" in low or "too many requests" in low
            or "rate-limited" in low or "rate limited" in low)


def needs_login(msg: str) -> bool:
    low = msg.lower()
    return not is_blocked(msg) and ("sign in" in low or "login required" in low
                                    or ("age" in low and "restricted" in low))


@dataclass
class DownloadResult:
    path: Path
    info: dict


class YtDlp:
    def __init__(self, tools_dir: Path):
        self.tools_dir = tools_dir
        self._update_lock = asyncio.Lock()
        # Ciasteczka konta YouTube (plik cookies.txt) – używane tylko awaryjnie:
        # przy blokadzie albo gdy utwór wymaga zalogowania ([with_cookies_until]).
        self.cookies_file: Path | None = None
        self.cookies_until = 0.0

    @property
    def has_cookies(self) -> bool:
        return self.cookies_file is not None and self.cookies_file.is_file()

    def cookies_active(self) -> bool:
        return self.has_cookies and time.time() < self.cookies_until

    def _cookie_args(self, force: bool = False) -> list[str]:
        if self.has_cookies and (force or self.cookies_active()):
            return ["--cookies", str(self.cookies_file)]
        return []

    @property
    def _current(self) -> Path:
        """Dowiązanie do aktualnej, kompletnej instalacji (tools/current → tools/releases/<czas>)."""
        return self.tools_dir / "current"

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if (self._current / "yt_dlp").is_dir():
            env["PYTHONPATH"] = str(self._current.resolve()) + os.pathsep + env.get("PYTHONPATH", "")
        return env

    @staticmethod
    def _js_runtime_args() -> list[str]:
        # YouTube wymaga silnika JS do części formatów. deno jest domyślny; node – jeśli jest;
        # QuickJS (qjs, 2 MB) – w paczce „Chmura na tym komputerze” (bez Dockera).
        if shutil.which("deno"):
            return []
        if shutil.which("node"):
            return ["--js-runtimes", "node"]
        if shutil.which("qjs"):
            return ["--js-runtimes", "quickjs"]
        return []

    async def _run(self, *args: str, on_line: Callable[[str], None] | None = None, timeout: float = 900,
                   cookies: bool = False) -> str:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "yt_dlp", *self._js_runtime_args(), *self._cookie_args(cookies), *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._env(),
        )
        out: list[str] = []
        err_tail: list[str] = []

        async def read_out():
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                out.append(line)
                if on_line:
                    on_line(line)

        async def read_err():
            async for raw in proc.stderr:
                err_tail.append(raw.decode(errors="replace").rstrip())
                del err_tail[:-20]

        try:
            await asyncio.wait_for(asyncio.gather(read_out(), read_err(), proc.wait()), timeout)
        finally:
            if proc.returncode is None:  # przerwane (anulowanie, timeout)
                proc.kill()
                await proc.wait()
        if proc.returncode != 0:
            errors = [l for l in err_tail if l.startswith("ERROR")] or err_tail[-3:]
            raw = " ".join(errors)
            code, params = _friendly(raw) if raw.strip() else ("ytdlp.failed", {"code": proc.returncode})
            raise YtDlpError(code, blocked=is_blocked(raw), needs_login=needs_login(raw), **params)
        return "\n".join(out)

    async def version(self) -> str:
        return (await self._run("--version", timeout=30)).strip()

    async def search(self, term: str, timeout: float = 60) -> list[dict]:
        """Wyszukiwanie przez yt-dlp (np. „scsearch20:…”) – lista wyników bez szczegółów."""
        out = await self._run("--flat-playlist", "-J", "--no-warnings", term, timeout=timeout)
        return json.loads(out).get("entries") or []

    async def extractors(self) -> list[str]:
        """Nazwy obsługiwanych serwisów (bez wariantów „serwis:coś” i wyszukiwarek)."""
        out = await self._run("--list-extractors", timeout=60)
        names: dict[str, str] = {}
        for line in out.splitlines():
            line = line.strip()
            if not line or "(currently broken)" in line.lower():
                continue
            base = line.split(":")[0].strip()
            if base.lower() in ("generic", "") or base.lower().endswith("search"):
                continue
            names.setdefault(base.lower(), base)
        return sorted(names.values(), key=str.lower)

    async def download(self, url: str, workdir: Path, on_progress: Callable[[float], None],
                       cookies: bool = False) -> DownloadResult:
        """Pobiera najlepszy dźwięk bez ponownego kodowania (YouTube → Opus)."""
        workdir.mkdir(parents=True, exist_ok=True)

        def parse(line: str):
            if not line.startswith("NATI "):
                return
            done, total, estimate = (line.split() + ["NA"] * 3)[1:4]
            try:
                size = float(total if total != "NA" else estimate)
                on_progress(min(float(done) / size, 1.0))
            except (ValueError, ZeroDivisionError):
                pass

        await self._run(
            "-f", "bestaudio/best", "-x", "--audio-format", "best",
            "--no-playlist", "--newline", "--no-colors", "--write-info-json",
            "--progress-template", "download:NATI %(progress.downloaded_bytes)s %(progress.total_bytes)s "
            "%(progress.total_bytes_estimate)s",
            "-o", str(workdir / "audio.%(ext)s"),
            url,
            on_line=parse, cookies=cookies,
        )
        audio = [p for p in workdir.iterdir() if p.name.startswith("audio.") and not p.name.endswith(".json")]
        if not audio:
            raise YtDlpError("ytdlp.file_missing")
        info_file = workdir / "audio.info.json"
        info = json.loads(info_file.read_text()) if info_file.exists() else {}
        return DownloadResult(audio[0], info)

    async def fetch_audio(self, url: str, workdir: Path, on_info: Callable[[str, int | None], None],
                          fmt: str = "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio") -> None:
        """Pobiera dźwięk do odsłuchu (albo obraz teledysku – inny [fmt]) bez konwersji do workdir/audio.<ext>.

        on_info(ext, rozmiar) jest wołane, zanim zacznie się pobieranie – plik rośnie
        potem po kolei, więc można go już czytać."""
        workdir.mkdir(parents=True, exist_ok=True)

        def parse(line: str):
            if not line.startswith("NATIINFO "):
                return
            ext, size, approx = (line.split() + ["NA"] * 3)[1:4]
            on_info(ext, int(size) if size.isdigit() else None)

        await self._run(
            "-f", fmt, "--no-playlist", "--no-part",
            "--no-simulate", "--print", "before_dl:NATIINFO %(ext)s %(filesize)s %(filesize_approx)s",
            "-o", str(workdir / "audio.%(ext)s"), url,
            on_line=parse, timeout=600,
        )

    # ---------------------------------------------------------------- aktualizacja

    @property
    def _stamp(self) -> Path:
        return self.tools_dir / ".last-update"

    def update_due(self) -> bool:
        try:
            return time.time() - self._stamp.stat().st_mtime > UPDATE_EVERY
        except FileNotFoundError:
            return True

    async def update(self) -> str:
        """Instaluje najnowszy yt-dlp do nowego folderu i dopiero gotowy przełącza jako bieżący –
        trwające pobierania nigdy nie widzą wersji „w połowie podmienionej”. Zwraca nową wersję."""
        async with self._update_lock:
            releases = self.tools_dir / "releases"
            target = releases / str(int(time.time()))
            target.mkdir(parents=True, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                "--target", str(target), "yt-dlp[default]",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                shutil.rmtree(target, ignore_errors=True)
                raise YtDlpError("ytdlp.update_failed", error=out.decode(errors="replace")[-500:])
            # Atomowe przełączenie dowiązania.
            tmp_link = self.tools_dir / "current.new"
            tmp_link.unlink(missing_ok=True)
            tmp_link.symlink_to(target.relative_to(self.tools_dir), target_is_directory=True)
            os.replace(tmp_link, self._current)
            # Stare wersje – usuwamy (trwające procesy mają już swoje pliki otwarte).
            for old in releases.iterdir():
                if old != target:
                    shutil.rmtree(old, ignore_errors=True)
            self._stamp.touch()
            version = await self.version()
            log.info("yt-dlp zaktualizowany do %s", version)
            return version


def _friendly(msg: str) -> tuple[str, dict]:
    """Komunikaty yt-dlp po ludzku (najczęstsze przypadki) – kod i parametry (messages.py)."""
    low = msg.lower()
    if "video unavailable" in low or "not available" in low:
        return "ytdlp.unavailable", {}
    if is_blocked(msg):
        return "ytdlp.blocked", {}
    if needs_login(msg):
        return "ytdlp.login_required", {}
    if "unsupported url" in low:
        return "ytdlp.unsupported_link", {}
    return "ytdlp.error", {"error": msg.removeprefix("ERROR: ").strip()}
