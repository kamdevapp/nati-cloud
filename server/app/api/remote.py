"""„Odtwarzaj na…” – sterowanie odtwarzaniem na innych urządzeniach tej chmury (TV, komputer, telefon).

Każda aplikacja trzyma otwarte połączenie WebSocket `/api/remote/ws`:

1. Pierwsza wiadomość: `{"type": "hello", "token": …, "device_id": …, "name": …, "kind": "tv|desktop|phone"}`.
   Urządzenie z kilkoma profilami domowników (np. TV) otwiera osobne połączenie dla każdego profilu –
   dzięki temu widzi je każdy z domowników.
2. Serwer odsyła listę urządzeń online tego konta: `{"type": "devices", "devices": [{id, name, kind, state}]}`
   (i wysyła ją ponownie, gdy ktoś się połączy / rozłączy).
3. `{"type": "state", "state": {…}}` – co gra na urządzeniu (utwór, pauza, pozycja); trafia do pozostałych
   urządzeń konta jako `{"type": "state", "device": id, "state": …}`.
4. `{"type": "command", "to": id, "command": {…}}` – polecenie dla urządzenia (zagraj, pauza, następny…);
   cel dostaje `{"type": "command", "from": id, "from_name": …, "command": …}`. Polecenia idą tylko między
   urządzeniami tego samego konta.

Stan jest w pamięci serwera (po restarcie aplikacje łączą się same ponownie)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..messages import render
from ..db import new_session
from ..security import _user_from_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/remote", tags=["remote"])

KINDS = {"tv", "desktop", "phone"}
MAX_MESSAGE = 512 * 1024  # kolejka kilkuset utworów mieści się z zapasem


@dataclass
class _Conn:
    ws: WebSocket
    user_id: int
    device_id: str
    name: str
    kind: str
    state: dict = field(default_factory=dict)

    def info(self) -> dict:
        return {"id": self.device_id, "name": self.name, "kind": self.kind, "state": self.state}


class Hub:
    def __init__(self) -> None:
        # (konto, urządzenie) → połączenie
        self._conns: dict[tuple[int, str], _Conn] = {}
        self._lock = asyncio.Lock()

    def devices(self, user_id: int) -> list[dict]:
        return [c.info() for (uid, _), c in self._conns.items() if uid == user_id]

    async def _send(self, conn: _Conn, message: dict) -> None:
        try:
            await conn.ws.send_json(message)
        except Exception:  # rozłączone w międzyczasie – sprzątnie je jego własna pętla
            pass

    async def broadcast_devices(self, user_id: int) -> None:
        devices = self.devices(user_id)
        for (uid, _), conn in list(self._conns.items()):
            if uid == user_id:
                await self._send(conn, {"type": "devices", "devices": devices})

    async def add(self, conn: _Conn) -> None:
        async with self._lock:
            old = self._conns.get((conn.user_id, conn.device_id))
            self._conns[(conn.user_id, conn.device_id)] = conn
        if old is not None:  # to samo urządzenie połączyło się ponownie
            try:
                await old.ws.close()
            except Exception:
                pass
        await self.broadcast_devices(conn.user_id)

    async def remove(self, conn: _Conn) -> None:
        async with self._lock:
            if self._conns.get((conn.user_id, conn.device_id)) is conn:
                del self._conns[(conn.user_id, conn.device_id)]
        await self.broadcast_devices(conn.user_id)

    async def state(self, conn: _Conn, state: dict) -> None:
        conn.state = state
        for (uid, did), other in list(self._conns.items()):
            if uid == conn.user_id and did != conn.device_id:
                await self._send(other, {"type": "state", "device": conn.device_id, "state": state})

    async def command(self, conn: _Conn, to: str, command: dict) -> bool:
        target = self._conns.get((conn.user_id, to))
        if target is None:
            return False
        await self._send(target, {"type": "command", "from": conn.device_id, "from_name": conn.name,
                                  "command": command})
        return True


hub = Hub()


def _err(code: str, **params) -> dict:
    """Błąd dla aplikacji: kod (tłumaczony w aplikacji), parametry i tekst angielski."""
    return {"message": render(code, params), "code": code, "params": params}


def _authenticate(token: str) -> int:
    with new_session() as db:
        return _user_from_token(token, db).id


@router.websocket("/ws")
async def remote_ws(ws: WebSocket):
    await ws.accept()
    try:
        hello = await asyncio.wait_for(ws.receive_json(), timeout=15)
        if not isinstance(hello, dict) or hello.get("type") != "hello":
            raise ValueError("expected “hello”")
        user_id = await asyncio.to_thread(_authenticate, str(hello.get("token") or ""))
        device_id = str(hello.get("device_id") or "")[:64]
        if not device_id:
            raise ValueError("missing device_id")
        kind = hello.get("kind") if hello.get("kind") in KINDS else "desktop"
        conn = _Conn(ws=ws, user_id=user_id, device_id=device_id,
                     name=str(hello.get("name") or "Device")[:80], kind=kind)
    except (HTTPException, ValueError, TypeError, asyncio.TimeoutError, WebSocketDisconnect) as e:
        detail = getattr(e, "detail", None) or str(e) or "sign-in failed"
        try:
            await ws.send_json({"type": "error", **_err("remote.no_access", error=str(detail))})
            await ws.close(code=4401)
        except Exception:
            pass
        return

    await ws.send_json({"type": "welcome", "device_id": device_id})
    await hub.add(conn)
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > MAX_MESSAGE:
                await ws.send_json({"type": "error", **_err("remote.too_large")})
                continue
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "state" and isinstance(msg.get("state"), dict):
                await hub.state(conn, msg["state"])
            elif kind == "command" and isinstance(msg.get("command"), dict):
                if not await hub.command(conn, str(msg.get("to") or ""), msg["command"]):
                    await ws.send_json({"type": "error", **_err("remote.device_offline")})
            elif kind == "devices":
                await ws.send_json({"type": "devices", "devices": hub.devices(user_id)})
            # „ping” i nieznane – bez odpowiedzi (utrzymanie połączenia)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Połączenie „Odtwarzaj na…” przerwane")
    finally:
        await hub.remove(conn)
