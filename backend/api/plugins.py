"""``/api/plugins`` listing, enable/disable, and hot-reload."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/plugins", tags=["plugins"])

    @router.get("")
    async def list_plugins(request: Request) -> dict[str, Any]:
        loader = getattr(request.app.state, "plugin_loader", None)
        if loader is None:
            return {"enabled": False, "items": [], "count": 0}
        items = [p.to_dict() for p in loader.loaded]
        return {
            "enabled": True,
            "items": items,
            "count": len(items),
            "loaded_count": sum(1 for p in items if p["status"] == "loaded"),
            "error_count": sum(1 for p in items if p["status"] == "error"),
            "disabled_count": sum(1 for p in items if p["status"] == "disabled"),
        }

    class EnablePayload(BaseModel):
        enabled: bool

    @router.post("/{plugin_id}/enable")
    async def set_plugin_enabled(
        plugin_id: str, payload: EnablePayload, request: Request,
    ) -> dict[str, Any]:
        loader = getattr(request.app.state, "plugin_loader", None)
        if loader is None:
            raise HTTPException(status_code=503, detail="plugin loader not initialized")
        changed = loader.set_enabled(plugin_id, payload.enabled)
        if not changed:
            current = "enabled" if not loader.is_disabled(plugin_id) else "disabled"
            return {"ok": True, "plugin_id": plugin_id, "status": current, "changed": False}
        return {
            "ok": True,
            "plugin_id": plugin_id,
            "status": "enabled" if payload.enabled else "disabled",
            "changed": True,
        }

    @router.post("/{plugin_id}/reload")
    async def reload_plugin(plugin_id: str, request: Request) -> dict[str, Any]:
        loader = getattr(request.app.state, "plugin_loader", None)
        if loader is None:
            raise HTTPException(status_code=503, detail="plugin loader not initialized")
        row = loader.reload_plugin(plugin_id)
        return row.to_dict()

    return router


__all__ = ["build_router"]
