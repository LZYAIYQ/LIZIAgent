"""Tool reliability layer: fallback suggestions + health tracking.

This module sits between the tool guardrails and the tool loop runner,
adding two capabilities:

1. **Fallback suggestions**: when a tool fails, suggest alternative tools
   that might accomplish the same goal. The LLM sees the suggestion in
   the tool error message and can switch automatically.

2. **Per-tool health tracking**: track success rates across turns. When
   a tool's success rate drops below a threshold, inject a warning into
   its error messages so the LLM knows to avoid it.

Design:
- Pure read-mostly overlay on the existing guardrails + tracer.
- Never blocks execution — only enriches error messages with hints.
- Resets per-turn like the guardrails.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Fallback map: tool → list of alternative tools
# ---------------------------------------------------------------------------

FALLBACK_MAP: dict[str, list[str]] = {
    "scholar_search": ["web_search", "read_url"],
    "web_search": ["read_url"],
    "read_url": ["web_search"],
    "read_file": [],
    "write_file": [],
    "code_execution": [],
    "memory_manage": [],
    "skill_manage": [],
    "cron_manage": [],
    "mcp_manage": [],
    "knowledge_ingest": [],
    "knowledge_inspect": [],
    "knowledge_mode_manage": [],
    "tool_search": [],
    "send_message": [],
    "delegate": [],
}

# Descriptions for fallback tools (shown to LLM)
_FALLBACK_DESCRIPTIONS: dict[str, str] = {
    "web_search": "通用网页搜索（DuckDuckGo/Bing）",
    "read_url": "读取指定 URL 的内容",
    "scholar_search": "Google Scholar 学术搜索",
}


# ---------------------------------------------------------------------------
# Tool health tracker (cross-turn)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ToolHealth:
    """Per-tool success/failure counters with time window."""

    success: int = 0
    failure: int = 0
    last_failure_at: float = 0.0
    last_error: str = ""

    @property
    def total(self) -> int:
        return self.success + self.failure

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 1.0
        return self.success / self.total

    @property
    def is_healthy(self) -> bool:
        """Healthy if success rate >= 50% or fewer than 3 total calls."""
        if self.total < 3:
            return True
        return self.success_rate >= 0.5

    def record_success(self) -> None:
        self.success += 1

    def record_failure(self, error: str = "") -> None:
        self.failure += 1
        self.last_failure_at = time.monotonic()
        self.last_error = error[:200]


class ToolHealthTracker:
    """Cross-turn tool health tracking. Persists for the process lifetime."""

    def __init__(self, window_seconds: float = 3600.0) -> None:
        self._window = window_seconds
        self._health: dict[str, ToolHealth] = {}

    def record(self, tool_name: str, ok: bool, error: str = "") -> None:
        health = self._get_or_create(tool_name)
        if ok:
            health.record_success()
        else:
            health.record_failure(error)

    def is_healthy(self, tool_name: str) -> bool:
        return self._get_or_create(tool_name).is_healthy

    def get_health(self, tool_name: str) -> ToolHealth:
        return self._get_or_create(tool_name)

    def get_all_health(self) -> dict[str, dict[str, Any]]:
        """Return health summary for all tracked tools."""
        result: dict[str, dict[str, Any]] = {}
        for name, health in self._health.items():
            result[name] = {
                "success": health.success,
                "failure": health.failure,
                "success_rate": round(health.success_rate, 3),
                "is_healthy": health.is_healthy,
                "last_error": health.last_error[:80] if health.last_error else "",
            }
        return result

    def _get_or_create(self, tool_name: str) -> ToolHealth:
        if tool_name not in self._health:
            self._health[tool_name] = ToolHealth()
        return self._health[tool_name]


# ---------------------------------------------------------------------------
# Fallback suggestion generator
# ---------------------------------------------------------------------------


def suggest_fallback(
    tool_name: str,
    error: str,
    *,
    health_tracker: Optional[ToolHealthTracker] = None,
) -> str:
    """Generate a fallback suggestion string for a failed tool call.

    Returns a hint string to append to the tool error message, or empty
    string if no fallback is available.
    """
    fallbacks = FALLBACK_MAP.get(tool_name, [])
    if not fallbacks:
        return ""

    # Filter out unhealthy alternatives
    healthy_fallbacks: list[str] = []
    for fb in fallbacks:
        if health_tracker and not health_tracker.is_healthy(fb):
            continue
        healthy_fallbacks.append(fb)

    if not healthy_fallbacks:
        return ""

    suggestions: list[str] = []
    for fb in healthy_fallbacks:
        desc = _FALLBACK_DESCRIPTIONS.get(fb, fb)
        suggestions.append(f"`{fb}` ({desc})")

    return (
        f" [提示: {tool_name} 失败，可尝试替代工具: "
        + "、".join(suggestions)
        + "]"
    )


def enrich_error_with_fallback(
    tool_name: str,
    error: str,
    *,
    health_tracker: Optional[ToolHealthTracker] = None,
) -> str:
    """Enrich a tool error message with fallback suggestions."""
    hint = suggest_fallback(
        tool_name, error, health_tracker=health_tracker,
    )
    if hint:
        return error + hint
    return error
