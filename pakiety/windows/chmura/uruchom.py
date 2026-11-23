"""Nati – chmura na tym komputerze (Windows), bez Dockera i bez praw administratora.

Uruchamiane bez okna przez pythonw.exe (aplikacja Nati i wpis „Uruchom” po zalogowaniu):
    python\\pythonw.exe uruchom.py <plik ustawień>

Plik ustawień: wiersze NAZWA=wartość (zmienne NATI_*, patrz server/app/config.py; NATI_PORT, NATI_HOST).
Serwer (python.exe -m uvicorn) działa bez okna konsoli, dziennik w <NATI_DATA_DIR>\\nati-chmura.log,
po awarii startuje ponownie. Zawsze jeden egzemplarz (mutex „Local\\NatiChmura”); identyfikator procesu
w <NATI_DATA_DIR>\\nati-chmura.pid – aplikacja zatrzymuje chmurę, kończąc ten proces razem z serwerem.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CREATE_NO_WINDOW = 0x08000000
ERROR_ALREADY_EXISTS = 183
RESTART_CODE = 75  # serwer prosi o ponowne uruchomienie (przywrócona kopia, wyczyszczona chmura)
LOG_LIMIT = 5 * 1024 * 1024


def read_settings(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\NatiChmura")
    # Uchwyt zostaje otwarty do końca procesu – mutex żyje tyle, co ten proces.
    globals()["_mutex"] = handle
    return bool(handle) and ctypes.get_last_error() != ERROR_ALREADY_EXISTS


def open_log(data: Path):
    log = data / "nati-chmura.log"
    if log.exists() and log.stat().st_size > LOG_LIMIT:
        os.replace(log, data / "nati-chmura.previous.log")
    return log.open("ab", buffering=0)


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    settings = read_settings(Path(sys.argv[1]))
    if not single_instance():
        return 0  # chmura już działa
    data = Path(settings.get("NATI_DATA_DIR") or HERE.parent.parent / "data")
    data.mkdir(parents=True, exist_ok=True)
    (data / "nati-chmura.pid").write_text(str(os.getpid()))

    env = dict(os.environ)
    env.update(settings)
    env["PATH"] = os.pathsep.join([str(HERE / "bin"), str(HERE / "ffmpeg"), env.get("PATH", "")])
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"  # pliki i wyjście procesów (yt-dlp, ffmpeg) w UTF-8, nie w stronie kodowej Windowsa
    cmd = [str(HERE / "python" / "python.exe"), "-m", "uvicorn", "app.main:app",
           "--host", settings.get("NATI_HOST", "0.0.0.0"), "--port", settings.get("NATI_PORT", "8080")]

    pause = 5
    while True:
        log = open_log(data)
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} start chmury\n".encode())
        started = time.monotonic()
        code = subprocess.call(cmd, cwd=data, env=env, stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        if code == RESTART_CODE:
            log.write(b"=== ponowne uruchomienie na prosbe serwera\n")
            log.close()
            pause = 5
            time.sleep(0.5)
            continue
        log.write(f"=== serwer zakończył się (kod {code}), ponowny start za {pause} s\n".encode())
        log.close()
        time.sleep(pause)
        # Szybkie awarie jedna po drugiej – coraz dłuższa przerwa (do minuty).
        pause = 5 if time.monotonic() - started > 60 else min(pause * 2, 60)


if __name__ == "__main__":
    sys.exit(main())
