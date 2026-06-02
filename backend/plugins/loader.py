"""Local-directory plugin loader.

Walks ``<plugins_dir>/<plugin-id>/`` for sibling ``plugin.json`` +
``plugin.py`` pairs, imports each module, finds the :class:`Plugin`
subclass, and runs ``setup(runtime)`` against a tightly-scoped
:class:`PluginRuntime`.

Failure modes are first-class — every plugin produces a
:class:`LoadedPlugin` record with ``status=loaded`` / ``status=error``
/ ``status=disabled`` plus a free-form ``error`` string. A misbehaving
plugin never blocks boot; ``app.py`` already catches a top-level loader
crash too.

Features:
* Enable/disable plugins via ``disabled.json``
* Hot-reload individual plugins
* SkillGuard integration for tool description scanning
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from loguru import logger

from .base import Plugin, PluginRuntime
from .manifest import ManifestError, PluginManifest, load_manifest


@dataclass(slots=True)
class LoadedPlugin:
    """One row per ``<plugins_dir>/<id>/`` directory the loader saw.

    ``status`` is exactly one of ``"loaded"`` / ``"error"``. ``error``
    is the human-readable reason for the latter, prefixed with a
    short tag (``manifest:``, ``import:``, ``setup:``, …) so callers
    can grep without reading the whole string.
    """

    id: str
    version: str = ""
    status: str = "loaded"
    error: Optional[str] = None
    description: str = ""
    registered_tools: list[str] = field(default_factory=list)
    registered_memory_providers: list[str] = field(default_factory=list)
    manifest: Optional[PluginManifest] = None
    plugin_dir: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "status": self.status,
            "error": self.error,
            "description": self.description,
            "registered_tools": list(self.registered_tools),
            "registered_memory_providers": list(self.registered_memory_providers),
            "plugin_dir": self.plugin_dir,
        }


class PluginLoader:
    """Discovers and loads local-directory plugins. Idempotent.

    Constructor args:

    * ``plugins_dir`` — root directory holding ``<id>/`` subfolders.
    * ``tool_registry`` — shared registry plugins push tools into.
      ``None`` is allowed for tests that only want manifest validation.
    * ``workspace_dir`` — exposed to plugins via :class:`PluginRuntime`
      so they can persist files under their own subdirectory.
    * ``settings`` — full Settings object kept for future plugins that
      need to read general config (we do *not* expose secrets).
    * ``skill_guard`` — when set, every tool description registered
      by a plugin is scanned. A ``dangerous`` verdict refuses
      registration with an ``error`` row.
    """

    def __init__(
        self,
        *,
        plugins_dir: Path,
        tool_registry: Optional[Any] = None,
        workspace_dir: Optional[Path] = None,
        settings: Optional[Any] = None,
        skill_guard: Optional[Any] = None,
        memory_manager: Optional[Any] = None,
    ) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._tool_registry = tool_registry
        self._workspace_dir = (
            Path(workspace_dir) if workspace_dir is not None else None
        )
        self._settings = settings
        self._skill_guard = skill_guard
        self._memory_manager = memory_manager
        self.loaded: list[LoadedPlugin] = []
        self._disabled_file = self._plugins_dir / "disabled.json"
        self._disabled: set[str] = self._load_disabled_set()

    def _load_disabled_set(self) -> set[str]:
        """Load the set of disabled plugin IDs from disabled.json."""
        if not self._disabled_file.exists():
            return set()
        try:
            import json
            data = json.loads(self._disabled_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return {str(x) for x in data}
        except Exception as exc:
            logger.warning("[plugins] failed to load disabled.json: {}", exc)
        return set()

    def _save_disabled_set(self) -> None:
        """Persist the disabled plugin IDs to disabled.json."""
        import json
        self._disabled_file.parent.mkdir(parents=True, exist_ok=True)
        self._disabled_file.write_text(
            json.dumps(sorted(self._disabled), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        """Enable or disable a plugin. Returns True if state changed."""
        if enabled:
            if plugin_id not in self._disabled:
                return False
            self._disabled.discard(plugin_id)
        else:
            if plugin_id in self._disabled:
                return False
            self._disabled.add(plugin_id)
        self._save_disabled_set()
        logger.info("[plugins] {} {}", plugin_id, "enabled" if enabled else "disabled")
        return True

    def is_disabled(self, plugin_id: str) -> bool:
        return plugin_id in self._disabled

    def reload_plugin(self, plugin_id: str) -> LoadedPlugin:
        """Hot-reload a single plugin. Unregisters old tools, re-imports."""
        # Find and remove old plugin
        old_idx = None
        for i, p in enumerate(self.loaded):
            if p.id == plugin_id:
                old_idx = i
                break
        if old_idx is not None:
            old = self.loaded[old_idx]
            # Unregister old tools
            self._rollback_tools(old.registered_tools)
            self.loaded.pop(old_idx)

        # Reload
        plugin_dir = self._plugins_dir / plugin_id
        if not plugin_dir.exists():
            return LoadedPlugin(
                id=plugin_id, status="error",
                error=f"plugin directory not found: {plugin_dir}",
            )
        row = self._load_one(plugin_dir)
        self.loaded.append(row)
        return row

    # ------------------------------------------------------------------
    def load_all(self) -> list[LoadedPlugin]:
        """Scan ``plugins_dir`` once and return one row per plugin.

        Idempotent: subsequent calls return the cached ``self.loaded``
        without re-importing or re-registering.
        """
        if self.loaded:
            return list(self.loaded)

        if not self._plugins_dir.exists() or not self._plugins_dir.is_dir():
            logger.info(
                "[plugins] plugins_dir missing or not a dir: {}",
                self._plugins_dir,
            )
            return []

        rows: list[LoadedPlugin] = []
        for entry in sorted(self._plugins_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            rows.append(self._load_one(entry))

        self.loaded = rows
        loaded_n = sum(1 for r in rows if r.status == "loaded")
        error_n = sum(1 for r in rows if r.status == "error")
        logger.info(
            "[plugins] discovered {} plugin(s) — loaded={} error={}",
            len(rows), loaded_n, error_n,
        )
        return list(rows)

    # ------------------------------------------------------------------
    def _load_one(self, plugin_dir: Path) -> LoadedPlugin:
        plugin_id = plugin_dir.name
        row = LoadedPlugin(id=plugin_id, plugin_dir=str(plugin_dir))

        # Check if plugin is disabled
        if self.is_disabled(plugin_id):
            row.status = "disabled"
            row.error = "plugin is disabled (via disabled.json)"
            return row

        # Step 1 — manifest must parse, and its id must match the dir.
        manifest_path = plugin_dir / "plugin.json"
        try:
            manifest = load_manifest(manifest_path)
        except ManifestError as exc:
            row.status = "error"
            row.error = f"manifest: {exc}"
            return row

        if manifest.id != plugin_id:
            row.status = "error"
            row.error = (
                f"id mismatch: manifest says {manifest.id!r}, dir is {plugin_id!r}"
            )
            return row

        row.manifest = manifest
        row.version = manifest.version
        row.description = manifest.description

        # Step 2 — import plugin.py.
        plugin_py = plugin_dir / "plugin.py"
        if not plugin_py.exists():
            row.status = "error"
            row.error = f"missing plugin.py at {plugin_py}"
            return row

        module_name = f"_lzagent_plugin_{plugin_id.replace('-', '_')}"
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, plugin_py
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot create spec for {plugin_py}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            sys.modules.pop(module_name, None)
            row.status = "error"
            row.error = f"import: {type(exc).__name__}: {exc}"
            return row

        # Step 3 — find a Plugin subclass.
        plugin_cls: Optional[type[Plugin]] = None
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is Plugin:
                continue
            if issubclass(obj, Plugin) and obj.__module__ == module_name:
                plugin_cls = obj
                break
        if plugin_cls is None:
            row.status = "error"
            row.error = "no Plugin subclass found in plugin.py"
            return row

        # Optional: class-level id can be unset; if set, it must match.
        cls_id = getattr(plugin_cls, "id", "") or ""
        if cls_id and cls_id != plugin_id:
            row.status = "error"
            row.error = (
                f"id mismatch (class {plugin_cls.__name__} says {cls_id!r},"
                f" dir is {plugin_id!r})"
            )
            return row

        # Step 4 — run setup() against a SkillGuard-aware runtime.
        runtime = _GuardedRuntime(
            plugin_id=plugin_id,
            tool_registry=self._tool_registry,
            memory_manager=self._memory_manager,
            workspace_dir=self._workspace_dir,
            plugin_dir=plugin_dir,
            skill_guard=self._skill_guard,
        )
        try:
            instance = plugin_cls()
            instance.setup(runtime)
        except _GuardBlocked as exc:
            row.status = "error"
            row.error = str(exc)
            # Roll back any tools the plugin already registered before
            # the blocked one (so the registry mirrors the failed state).
            self._rollback_tools(runtime.registered_tools)
            return row
        except Exception as exc:  # noqa: BLE001
            row.status = "error"
            row.error = f"setup: {type(exc).__name__}: {exc}"
            self._rollback_tools(runtime.registered_tools)
            return row

        row.registered_tools = list(runtime.registered_tools)
        row.registered_memory_providers = list(runtime.registered_memory_providers)
        row.status = "loaded"
        return row

    # ------------------------------------------------------------------
    def _rollback_tools(self, names: Iterable[str]) -> None:
        if self._tool_registry is None:
            return
        for name in list(names):
            try:
                # Best-effort: ToolRegistry doesn't ship an unregister
                # API in v0.23 (deferred); we use the private dict to
                # avoid leaking a half-loaded plugin's tools into the
                # rest of the app.
                inner = getattr(self._tool_registry, "_tools", None)
                if isinstance(inner, dict):
                    inner.pop(name, None)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[plugins] rollback unregister {} failed: {}", name, exc)


class _GuardBlocked(Exception):
    """Internal signal — the SkillGuard refused a tool registration."""


class _GuardedRuntime(PluginRuntime):
    """:class:`PluginRuntime` that runs every tool through SkillGuard first.

    Inherits the bookkeeping from the plain :class:`PluginRuntime` and
    adds a pre-flight scan of the tool's textual surfaces (description
    + name) before delegating to the real registry.
    """

    def __init__(
        self,
        *,
        plugin_id: str,
        tool_registry: Optional[Any],
        memory_manager: Optional[Any],
        workspace_dir: Optional[Path],
        plugin_dir: Path,
        skill_guard: Optional[Any],
    ) -> None:
        super().__init__(
            plugin_id=plugin_id,
            tool_registry=tool_registry,
            memory_manager=memory_manager,
            workspace_dir=workspace_dir,
            plugin_dir=plugin_dir,
        )
        self._skill_guard = skill_guard

    def register_tool(self, tool: Any) -> None:
        if self._skill_guard is not None and getattr(self._skill_guard, "enabled", False):
            description = getattr(tool, "description", "") or ""
            name = getattr(tool, "name", "") or ""
            scan_target = f"{name}\n{description}"
            result = self._skill_guard.scan(scan_target)
            # Plugins are background-trust by default — blocking on
            # caution is the conservative choice and keeps the smoke
            # asserts honest.
            if self._skill_guard.should_block(result, origin="plugin") or (
                result.verdict == "dangerous"
            ):
                raise _GuardBlocked(
                    f"SkillGuard refused plugin tool {name!r}"
                    f" ({result.summary_line()})"
                )
        super().register_tool(tool)


__all__ = ["LoadedPlugin", "PluginLoader"]
