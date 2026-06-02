"""Security hardening layer.

Provides three protections:

1. **Input length limiting** — reject messages exceeding a configurable
   threshold before they reach the LLM, preventing token-bomb attacks.

2. **Output filtering** — strip system prompt fragments from LLM
   responses before they reach the user, preventing accidental leaks.

3. **Per-user rate limiting** — sliding-window rate limiter that
   prevents a single user from flooding the system.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# 1. Input length limiting
# ---------------------------------------------------------------------------

DEFAULT_MAX_INPUT_CHARS = 8000
DEFAULT_MAX_INPUT_LINES = 200


@dataclass(slots=True)
class InputValidation:
    ok: bool = True
    error: str = ""
    truncated_text: str = ""


def validate_input(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_INPUT_CHARS,
    max_lines: int = DEFAULT_MAX_INPUT_LINES,
) -> InputValidation:
    """Validate and optionally truncate user input.

    Returns InputValidation with ok=True if input is acceptable,
    or ok=False with error message if it exceeds limits.
    """
    if not text:
        return InputValidation(ok=True, truncated_text="")

    # Line count check
    lines = text.split("\n")
    if len(lines) > max_lines:
        return InputValidation(
            ok=False,
            error=f"消息超过行数限制（{len(lines)} > {max_lines} 行）。请缩短消息。",
        )

    # Character count check
    if len(text) > max_chars:
        return InputValidation(
            ok=False,
            error=f"消息超过长度限制（{len(text)} > {max_chars} 字符）。请缩短消息。",
        )

    return InputValidation(ok=True, truncated_text=text)


# ---------------------------------------------------------------------------
# 2. Output filtering — system prompt leak prevention
# ---------------------------------------------------------------------------

# Patterns that might indicate system prompt fragments in output
_SYSTEM_PROMPT_PATTERNS = [
    # Fence tags used by memory system
    re.compile(r"<memory-context>.*?</memory-context>", re.DOTALL | re.IGNORECASE),
    # System note markers
    re.compile(r"\[System note:.*?\]", re.DOTALL | re.IGNORECASE),
    # Common system prompt prefixes
    re.compile(r"You are a helpful assistant.*?(?=\n\n|\Z)", re.IGNORECASE),
    # Tool schema JSON fragments
    re.compile(r'"type":\s*"function".*?"parameters":\s*\{', re.IGNORECASE),
]


def filter_output(text: str) -> str:
    """Filter system prompt fragments from LLM output.

    This is a defense-in-depth measure. The primary protection is
    proper system prompt isolation in the LLM call, but this catches
    edge cases where the model might echo parts of its instructions.
    """
    if not text:
        return text

    filtered = text
    for pattern in _SYSTEM_PROMPT_PATTERNS:
        filtered = pattern.sub("[filtered]", filtered)

    # Clean up multiple consecutive [filtered] markers
    filtered = re.sub(r"(\[filtered\]\s*){2,}", "[filtered]\n", filtered)

    return filtered.strip()


# ---------------------------------------------------------------------------
# 3. Per-user rate limiting (sliding window)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RateLimitConfig:
    requests_per_minute: int = 30
    requests_per_hour: int = 200
    burst_size: int = 10  # max concurrent requests


@dataclass(slots=True)
class UserWindow:
    """Sliding window counters for one user."""

    minute_timestamps: list[float] = field(default_factory=list)
    hour_timestamps: list[float] = field(default_factory=list)
    active_requests: int = 0


class RateLimiter:
    """Per-user sliding-window rate limiter.

    Tracks request timestamps per user and rejects requests that
    exceed the configured limits.
    """

    def __init__(self, config: Optional[RateLimitConfig] = None) -> None:
        self._config = config or RateLimitConfig()
        self._windows: dict[str, UserWindow] = defaultdict(UserWindow)

    def check(self, user_id: str) -> tuple[bool, str]:
        """Check if a request from user_id is allowed.

        Returns (allowed, reason). If allowed is False, reason explains why.
        """
        now = time.monotonic()
        window = self._windows[user_id]

        # Clean old timestamps
        cutoff_minute = now - 60.0
        cutoff_hour = now - 3600.0
        window.minute_timestamps = [t for t in window.minute_timestamps if t > cutoff_minute]
        window.hour_timestamps = [t for t in window.hour_timestamps if t > cutoff_hour]

        # Check burst (concurrent requests)
        if window.active_requests >= self._config.burst_size:
            return False, f"并发请求数超限（{window.active_requests} >= {self._config.burst_size}）。请稍后再试。"

        # Check per-minute limit
        if len(window.minute_timestamps) >= self._config.requests_per_minute:
            return False, f"请求频率超限（每分钟 {self._config.requests_per_minute} 次）。请稍后再试。"

        # Check per-hour limit
        if len(window.hour_timestamps) >= self._config.requests_per_hour:
            return False, f"请求频率超限（每小时 {self._config.requests_per_hour} 次）。请稍后再试。"

        return True, ""

    def record_start(self, user_id: str) -> None:
        """Record the start of a request."""
        now = time.monotonic()
        window = self._windows[user_id]
        window.minute_timestamps.append(now)
        window.hour_timestamps.append(now)
        window.active_requests += 1

    def record_end(self, user_id: str) -> None:
        """Record the end of a request."""
        window = self._windows[user_id]
        window.active_requests = max(0, window.active_requests - 1)

    def get_stats(self, user_id: str) -> dict[str, Any]:
        """Get rate limit stats for a user."""
        now = time.monotonic()
        window = self._windows[user_id]
        cutoff_minute = now - 60.0
        cutoff_hour = now - 3600.0
        recent_minute = sum(1 for t in window.minute_timestamps if t > cutoff_minute)
        recent_hour = sum(1 for t in window.hour_timestamps if t > cutoff_hour)
        return {
            "user_id": user_id,
            "requests_last_minute": recent_minute,
            "requests_last_hour": recent_hour,
            "active_requests": window.active_requests,
            "limits": {
                "per_minute": self._config.requests_per_minute,
                "per_hour": self._config.requests_per_hour,
                "burst": self._config.burst_size,
            },
        }
