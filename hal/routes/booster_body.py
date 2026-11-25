"""Booster K1 body routes: built-in arm gestures and bounded walking steps.

Mounted only when the motion driver exposes a Booster body
(`BoosterHeadMotionService.body`). Every walk request is clamped in speed and
time and always ends with zero velocity; `/booster/stop` stops walking at once.
None of these change the robot's mode: stand it up from the remote first.
"""
from __future__ import annotations

import threading
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import hal.app_state as state

router = APIRouter(tags=["Booster body"])

# One body action at a time: a new gesture or walk waits for the previous one.
_busy = threading.Lock()


class GestureRequest(BaseModel):
    name: Literal["wave", "handshake", "greet"]
    hold_s: float = Field(3.0, ge=0.5, le=8.0, description="Seconds before the gesture is ended")


class WalkRequest(BaseModel):
    vx: float = Field(0.0, ge=-1.0, le=1.0, description="Forward m/s (+ forward); clamped by the driver")
    vy: float = Field(0.0, ge=-1.0, le=1.0, description="Sideways m/s (+ left); clamped by the driver")
    vyaw: float = Field(0.0, ge=-2.0, le=2.0, description="Turn rad/s (+ left); clamped by the driver")
    seconds: float = Field(1.0, ge=0.0, le=10.0, description="Duration; clamped by the driver")


def _body():
    svc = state.animation_service
    body = getattr(svc, "body", None)
    if body is None:
        raise HTTPException(503, "Booster body not available")
    return body


@router.post("/booster/gesture")
def gesture(req: GestureRequest):
    """Play a built-in Booster arm gesture (blocks until it ends)."""
    body = _body()
    if not _busy.acquire(timeout=0.1):
        raise HTTPException(409, "another body action is running")
    try:
        body.gesture(req.name, req.hold_s)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    finally:
        _busy.release()
    return {"status": "ok", "gesture": req.name}


@router.post("/booster/walk")
def walk(req: WalkRequest):
    """Walk at a clamped velocity for a bounded time, then stop (blocks)."""
    body = _body()
    if not _busy.acquire(timeout=0.1):
        raise HTTPException(409, "another body action is running")
    try:
        applied = body.walk(req.vx, req.vy, req.vyaw, req.seconds)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    finally:
        _busy.release()
    return {"status": "ok", "applied": applied}


@router.post("/booster/stop")
def stop():
    """Stop walking now. Never waits for, or is blocked by, another action."""
    _body().stop_walking()
    return {"status": "ok"}


@router.get("/booster/status")
def status():
    body = _body()
    try:
        mode = body.mode()
    except Exception as e:
        raise HTTPException(503, f"mode unavailable: {e}")
    return {"mode": mode, "busy": _busy.locked()}
