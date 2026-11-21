"""Własna kolejność albumów, wykonawców i playlist (osobno dla każdego konta, ta sama na wszystkich urządzeniach),
a także układ strony głównej („home”: sekcje w kolejności, z widokiem i widocznością – np. "albums:grid:1").

Aplikacja układa listę u siebie: najpierw elementy w zapisanej kolejności, potem nowe (w domyślnym
sortowaniu). Po przesunięciu wysyła całą kolejność."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, Body, HTTPException, status
from sqlalchemy import select

from ..errors import AppError
from ..db.models import UserOrder
from ..security import DB, CurrentUser

router = APIRouter(prefix="/api/order", tags=["order"])

Kind = Literal["album", "artist", "playlist", "track", "genre", "home"]


@router.get("/{kind}", response_model=list[int | str])
def get_order(kind: Kind, user: CurrentUser, db: DB):
    """Lista id (gatunki – nazwy)."""
    row = db.scalar(select(UserOrder).where(UserOrder.user_id == user.id, UserOrder.kind == kind))
    return json.loads(row.ids) if row else []


@router.put("/{kind}", response_model=list[int | str])
def set_order(kind: Kind, ids: Annotated[list[int | str], Body(max_length=200_000)], user: CurrentUser, db: DB):
    """Gatunki i układ Home – lista napisów, pozostałe – lista id liczbowych."""
    if kind in ("genre", "home"):
        ids = [str(x)[:100] for x in ids]
    elif not all(isinstance(x, int) for x in ids):
        raise AppError(status.HTTP_422_UNPROCESSABLE_CONTENT, "order.expected_numbers")
    unique = list(dict.fromkeys(ids))
    row = db.scalar(select(UserOrder).where(UserOrder.user_id == user.id, UserOrder.kind == kind))
    if row is None:
        row = UserOrder(user_id=user.id, kind=kind)
        db.add(row)
    row.ids = json.dumps(unique)
    db.commit()
    return unique
