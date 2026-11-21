"""Kody jednorazowe TOTP (RFC 6238) – jak w Google Authenticator, Aegis, 2FAS: 6 cyfr co 30 s."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def now_code(secret: str, at: float | None = None) -> str:
    return _code(secret, int((at or time.time()) // PERIOD))


def verify(secret: str, code: str, at: float | None = None, window: int = 1) -> bool:
    """Kod z bieżących 30 s albo sąsiednich (zegary telefonu i serwera mogą się trochę rozjeżdżać)."""
    code = "".join(code.split())
    if len(code) != DIGITS or not code.isdigit():
        return False
    counter = int((at or time.time()) // PERIOD)
    return any(hmac.compare_digest(_code(secret, counter + d), code) for d in range(-window, window + 1))


def uri(secret: str, account: str, issuer: str = "Nati") -> str:
    """Adres otpauth:// do kodu QR."""
    return (f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}"
            f"&issuer={quote(issuer)}&digits={DIGITS}&period={PERIOD}")


def new_backup_codes(n: int = 10) -> list[str]:
    """Kody zapasowe „xxxx-xxxx” (gdy telefon zginie) – każdy działa raz."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" +
            "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(n)]


def hash_backup_code(code: str) -> str:
    return hashlib.sha256("".join(code.lower().split()).replace("-", "").encode()).hexdigest()
