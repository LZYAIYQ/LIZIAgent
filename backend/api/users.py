"""User management REST API.

Endpoints:
- GET  /api/users           — list all users
- GET  /api/users/{key}     — get user by key
- POST /api/users/{key}/role — set user role
- POST /api/users/{key}/enable — enable/disable user
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/users", tags=["users"])


def _get_user_manager(request: Request) -> Any:
    from ..db.users import UserManager
    manager = getattr(request.app.state, "user_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="user manager not initialized")
    return manager


@router.get("")
def list_users(request: Request) -> list[dict[str, Any]]:
    manager = _get_user_manager(request)
    return manager.list_users()


@router.get("/{user_key:path}")
def get_user(user_key: str, request: Request) -> dict[str, Any]:
    manager = _get_user_manager(request)
    user = manager.get_by_key(user_key)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    return user


class RolePayload(BaseModel):
    role: str  # admin | user | guest


@router.post("/{user_key:path}/role")
def set_role(user_key: str, payload: RolePayload, request: Request) -> dict[str, Any]:
    manager = _get_user_manager(request)
    if not manager.set_role(user_key, payload.role):
        raise HTTPException(status_code=400, detail="invalid role or user not found")
    return {"ok": True, "user_key": user_key, "role": payload.role}


class EnablePayload(BaseModel):
    enabled: bool


@router.post("/{user_key:path}/enable")
def set_enabled(
    user_key: str, payload: EnablePayload, request: Request,
) -> dict[str, Any]:
    manager = _get_user_manager(request)
    if not manager.set_enabled(user_key, payload.enabled):
        raise HTTPException(status_code=404, detail="user not found")
    return {"ok": True, "user_key": user_key, "enabled": payload.enabled}
