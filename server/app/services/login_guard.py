"""Ochrona logowania przed zgadywaniem haseł: po kilku nieudanych próbach konto (i adres IP)
jest na chwilę blokowane. Stan w pamięci – po restarcie serwera liczniki zaczynają od zera."""

from __future__ import annotations

import threading
import time

ACCOUNT_LIMIT = 5  # nieudanych prób na konto…
IP_LIMIT = 20  # …albo z jednego adresu (zgadywanie różnych kont)
WINDOW = 15 * 60  # w ciągu 15 minut
LOCK = 15 * 60  # blokada na 15 minut


class LoginGuard:
    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        times = [t for t in self._fails.get(key, []) if now - t < WINDOW + LOCK]
        self._fails[key] = times
        return times

    def locked_for(self, username: str, ip: str | None) -> int:
        """Ile sekund jeszcze trwa blokada (0 – można próbować)."""
        now = time.time()
        with self._lock:
            wait = 0.0
            for key, limit in ((f"u:{username.lower()}", ACCOUNT_LIMIT), (f"ip:{ip}", IP_LIMIT)):
                times = [t for t in self._recent(key, now) if now - t < WINDOW]
                if len(times) >= limit:
                    wait = max(wait, times[-1] + LOCK - now)
            return int(wait) + (1 if wait > 0 else 0)

    def failed(self, username: str, ip: str | None) -> None:
        now = time.time()
        with self._lock:
            for key in (f"u:{username.lower()}", f"ip:{ip}"):
                self._recent(key, now).append(now)

    def succeeded(self, username: str) -> None:
        with self._lock:
            self._fails.pop(f"u:{username.lower()}", None)


guard = LoginGuard()
