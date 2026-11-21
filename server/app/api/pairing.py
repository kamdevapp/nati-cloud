"""„Zaloguj telefonem” – logowanie telewizora bez wpisywania hasła pilotem.

1. TV: `POST /api/auth/pair/start` → krótki kod do pokazania na ekranie i tajny identyfikator prośby.
2. Zalogowana aplikacja (telefon, komputer): `POST /api/auth/pair/approve` z tym kodem – potwierdza.
3. TV co chwilę pyta `POST /api/auth/pair/poll`: najpierw 202 (czeka), po potwierdzeniu – token konta,
   które potwierdziło (osobna sesja na liście urządzeń).

Prośby żyją w pamięci kilka minut (restart serwera je kasuje – TV po prostu poprosi o nowy kod)."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..errors import AppError
from ..db.models import User
from ..schemas import TokenOut
from ..security import DB, CurrentUser
from .auth import _logged_in

router = APIRouter(prefix="/api/auth/pair", tags=["auth"])

TTL = 5 * 60  # kod ważny 5 minut
MAX_PENDING = 100  # ochrona przed zalewem próśb
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bez 0/O i 1/I – łatwo przepisać z ekranu


@dataclass
class _Pairing:
    code: str
    device: str
    created: float
    user_id: int | None = None


class _Store:
    def __init__(self) -> None:
        self._by_id: dict[str, _Pairing] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        for pid in [p for p, x in self._by_id.items() if now - x.created > TTL]:
            del self._by_id[pid]

    def start(self, device: str) -> tuple[str, str]:
        now = time.time()
        with self._lock:
            self._purge(now)
            if len(self._by_id) >= MAX_PENDING:
                raise AppError(status.HTTP_429_TOO_MANY_REQUESTS, "pair.too_many")
            codes = {p.code for p in self._by_id.values()}
            code = ""
            while not code or code in codes:
                code = "".join(secrets.choice(_ALPHABET) for _ in range(6))
            pid = secrets.token_urlsafe(32)
            self._by_id[pid] = _Pairing(code=code, device=device, created=now)
            return pid, code

    def approve(self, code: str, user_id: int) -> str:
        """Zwraca nazwę urządzenia; nieznany / przeterminowany kod – 404."""
        code = code.strip().upper().replace("-", "").replace(" ", "")
        with self._lock:
            self._purge(time.time())
            for p in self._by_id.values():
                if p.code == code and p.user_id is None:
                    p.user_id = user_id
                    return p.device
        raise AppError(status.HTTP_404_NOT_FOUND, "pair.bad_code")

    def poll(self, pid: str) -> _Pairing | None:
        """None – nie ma (wygasła); z user_id – potwierdzona (i usunięta – token wydaje się raz)."""
        with self._lock:
            self._purge(time.time())
            p = self._by_id.get(pid)
            if p is not None and p.user_id is not None:
                del self._by_id[pid]
            return p


store = _Store()


class PairStartIn(BaseModel):
    device: str | None = Field(default=None, max_length=128)


class PairStartOut(BaseModel):
    pair_id: str
    code: str
    expires_in: int = TTL


class PairPollIn(BaseModel):
    pair_id: str = Field(max_length=128)


class PairApproveIn(BaseModel):
    code: str = Field(min_length=4, max_length=16)


class PairApproveOut(BaseModel):
    device: str


@router.post("/start", response_model=PairStartOut)
def start(body: PairStartIn):
    """Publiczne – TV prosi o kod do pokazania na ekranie."""
    pid, code = store.start((body.device or "Telewizor").strip()[:128] or "Telewizor")
    return PairStartOut(pair_id=pid, code=code)


@router.post("/approve", response_model=PairApproveOut)
def approve(body: PairApproveIn, user: CurrentUser):
    """Zalogowany użytkownik potwierdza kod z telewizora – TV zaloguje się na jego konto."""
    return PairApproveOut(device=store.approve(body.code, user.id))


@router.post("/poll", response_model=TokenOut, responses={202: {"description": "Czeka na potwierdzenie"}})
def poll(body: PairPollIn, request: Request, db: DB):
    """TV pyta, czy kod już potwierdzono. 202 – jeszcze nie, 410 – kod wygasł (trzeba nowy)."""
    p = store.poll(body.pair_id)
    if p is None:
        raise AppError(status.HTTP_410_GONE, "pair.expired")
    if p.user_id is None:
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"status": "pending"})
    user = db.get(User, p.user_id)
    if user is None:
        raise AppError(status.HTTP_410_GONE, "pair.account_gone")
    return _logged_in(db, user, request, p.device)
