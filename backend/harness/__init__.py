"""Harness layer: runtime maintenance and coordination for LZAgent.

The harness is the stable middle layer between the core runtime and
user-facing extensions. It is responsible for keeping the system alive,
organizing the extension surface, and exposing a single control point
for FastAPI routes and IM commands.

The harness stays focused on orchestration rather than owning the
business logic of every subsystem. In practice that means:

* keeping core runtime state observable,
* exposing a consistent inventory of core versus extensions,
* routing lifecycle commands to the correct managers,
* surfacing health and telemetry for the UI,
* and remaining resilient when some subsystems are unavailable.

This is the "维持运行" layer the user described: the part that keeps
core services coordinated and makes external tools visible and safe to
manage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .extensions.inventory import HarnessInventory, build_inventory


@dataclass(slots=True)
class HarnessRuntime:
    """Stable runtime snapshot used by the UI and API layer."""

    core: dict[str, Any] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "core": dict(self.core),
            "services": dict(self.services),
            "extensions": dict(self.extensions),
        }


@dataclass(slots=True)
class Harness:
    """Single object the FastAPI layer and IM commands talk to.

    The harness is intentionally a coordination facade, not a dumping
    ground for business logic. It keeps the core runtime observable,
    gives the UI a stable inventory and runtime snapshot, and forwards
    lifecycle actions to the correct subsystem owners.

    Every dependency is optional so the app can boot in partial test or
    smoke configurations.
    """

    tool_registry: Optional[Any] = None
    skill_loader: Optional[Any] = None
    mcp_store: Optional[Any] = None
    mcp_manager: Optional[Any] = None
    mcp_lifecycle: Optional[Any] = None
    plugin_loader: Optional[Any] = None
    tool_memo: Optional[Any] = None
    tracer: Optional[Any] = None
    progress: Optional[Any] = None
    daily_review_service: Optional[Any] = None

    def inventory(self) -> HarnessInventory:
        """Return the user-facing extension snapshot."""
        return build_inventory(
            tool_registry=self.tool_registry,
            skill_loader=self.skill_loader,
            mcp_store=self.mcp_store,
            mcp_manager=self.mcp_manager,
            plugin_loader=self.plugin_loader,
        )

    def runtime(self) -> HarnessRuntime:
        """Return a compact runtime snapshot for the UI and APIs."""
        inventory = self.inventory().to_dict()
        return HarnessRuntime(
            core={
                "tool_registry": self.tool_registry is not None,
                "tool_memo": self.tool_memo is not None,
                "tracer": self.tracer is not None,
                "progress": self.progress is not None,
            },
            services={
                "skill_loader": self.skill_loader is not None,
                "mcp_store": self.mcp_store is not None,
                "mcp_manager": self.mcp_manager is not None,
                "mcp_lifecycle": self.mcp_lifecycle is not None,
                "plugin_loader": self.plugin_loader is not None,
                "daily_review_service": self.daily_review_service is not None,
            },
            extensions={
                "skills_items": list(inventory.get("skills", [])),
                "mcps_items": list(inventory.get("mcps", [])),
                "plugins_items": list(inventory.get("plugins", [])),
                "tools_items": list(inventory.get("tools", [])),
                "counts": dict(inventory.get("counts", {})),
                "core_summary": dict(inventory.get("core_summary", {})),
            },
        )

    async def uninstall_skill(self, name: str):
        from .extensions.lifecycle import uninstall_skill
        return await uninstall_skill(self, name)

    async def uninstall_mcp(self, name: str):
        from .extensions.lifecycle import uninstall_mcp
        return await uninstall_mcp(self, name)

    async def uninstall_plugin(self, name: str):
        from .extensions.lifecycle import uninstall_plugin
        return await uninstall_plugin(self, name)

    async def handle_command(self, text: str) -> Optional[str]:
        from .operations.commands import handle_command
        return await handle_command(text, self)


def build_harness(
    *,
    tool_registry: Optional[Any] = None,
    skill_loader: Optional[Any] = None,
    mcp_store: Optional[Any] = None,
    mcp_manager: Optional[Any] = None,
    mcp_lifecycle: Optional[Any] = None,
    plugin_loader: Optional[Any] = None,
) -> Harness:
    """Factory used by ``backend/app.py`` to assemble the harness facade."""
    return Harness(
        tool_registry=tool_registry,
        skill_loader=skill_loader,
        mcp_store=mcp_store,
        mcp_manager=mcp_manager,
        mcp_lifecycle=mcp_lifecycle,
        plugin_loader=plugin_loader,
    )


__all__ = [
    "Harness",
    "HarnessInventory",
    "HarnessRuntime",
    "build_harness",
    "build_inventory",
]
