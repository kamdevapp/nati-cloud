"""Błędy z kodem komunikatu (zob. messages.py): odpowiedź {"detail": tekst angielski, "code", "params"}."""

from __future__ import annotations

from fastapi import HTTPException

from .messages import render


class CodedError(Exception):
    """Błąd usługi (edycja, yt-dlp, Spotify, kopie…) z kodem – API zamienia go na AppError."""

    def __init__(self, code: str, status: int = 422, **params):
        self.code = code
        self.status = status
        self.params = {k: v for k, v in params.items() if v is not None}
        super().__init__(render(code, self.params))


class AppError(HTTPException):
    """Odpowiedź z błędem dla aplikacji: kod komunikatu (tłumaczony w aplikacji) i parametry."""

    def __init__(self, status_code: int, code: str, headers: dict | None = None, **params):
        self.code = code
        self.params = {k: v for k, v in params.items() if v is not None}
        super().__init__(status_code, render(code, self.params), headers=headers)

    @classmethod
    def of(cls, e: CodedError, status_code: int | None = None) -> AppError:
        return cls(status_code or e.status, e.code, **e.params)


def error_body(code: str, params: dict | None = None, **extra) -> dict:
    """Treść odpowiedzi z błędem (np. dla JSONResponse z dodatkowymi polami)."""
    return {"detail": render(code, params), "code": code, "params": params or {}, **extra}
