from __future__ import annotations

import re
from functools import lru_cache

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from paperbot.infrastructure.stores.subscriber_store import SubscriberStore

router = APIRouter()

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


@lru_cache(maxsize=1)
def _get_subscriber_store() -> SubscriberStore:
    """Lazy-init subscriber store on first use, not at import time."""
    return SubscriberStore()


class SubscribeRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=256)


class SubscribeResponse(BaseModel):
    ok: bool
    email: str
    message: str


@router.post("/newsletter/subscribe", response_model=SubscribeResponse)
def subscribe(req: SubscribeRequest):
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email format")

    result = _get_subscriber_store().add_subscriber(email)
    return SubscribeResponse(
        ok=True,
        email=result["email"],
        message="Subscribed successfully",
    )


@router.get("/newsletter/unsubscribe/{token}")
def unsubscribe(token: str):
    if not token or len(token) > 64:
        raise HTTPException(status_code=400, detail="Invalid token")

    ok = _get_subscriber_store().remove_subscriber(token)
    if not ok:
        raise HTTPException(status_code=404, detail="Token not found")

    return HTMLResponse(
        """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Unsubscribed</title></head>
<body style="font-family:sans-serif;max-width:480px;margin:60px auto;text-align:center;">
<h2>Unsubscribed</h2>
<p>You have been removed from the PaperBot DailyPaper newsletter.</p>
</body></html>"""
    )


class SubscriberCountResponse(BaseModel):
    active: int
    total: int


@router.get("/newsletter/subscribers", response_model=SubscriberCountResponse)
def list_subscribers():
    counts = _get_subscriber_store().get_subscriber_count()
    return SubscriberCountResponse(**counts)
