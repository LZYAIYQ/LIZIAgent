"""Lightweight i18n for LZAgent's fixed UI strings.

The LLM itself handles multi-language generation; this module only
covers **operator-facing strings** that bypass the LLM (session
commands, error messages, system notifications).

Language detection is heuristic: if >30% of CJK characters → zh,
otherwise → en. This is deliberately simple — a mis-detected language
just means a slightly wrong UI string, not a broken agent.
"""
from __future__ import annotations

import re
from typing import Literal

Lang = Literal["zh", "en"]

_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")

# ---------------------------------------------------------------------------
# Translation table
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[Lang, str]] = {
    # Session commands
    "session.reset.ok": {
        "zh": "会话已重置。",
        "en": "Session has been reset.",
    },
    "session.reset.no_history": {
        "zh": "会话已重置（无历史记录需要归档）。",
        "en": "Session reset (no history to archive).",
    },
    "session.reset.failed": {
        "zh": "会话重置失败: {}",
        "en": "Session reset failed: {}",
    },
    "session.summary.empty": {
        "zh": "当前会话没有对话记录。",
        "en": "No conversation history in the current session.",
    },
    "session.summary.header": {
        "zh": "**会话摘要**",
        "en": "**Session Summary**",
    },
    "session.summary.turns": {
        "zh": "- 对话轮数: {}",
        "en": "- Turn count: {}",
    },
    "session.summary.session_id": {
        "zh": "- 会话 ID: `{}`",
        "en": "- Session ID: `{}`",
    },
    "session.summary.topics": {
        "zh": "**用户话题**:",
        "en": "**User topics**:",
    },
    "session.export.empty": {
        "zh": "当前会话没有对话记录可导出。",
        "en": "No conversation history to export.",
    },
    "session.export.ok": {
        "zh": "会话已导出到 `{}`（共 {} 轮对话）。",
        "en": "Session exported to `{}` ({} turns).",
    },
    "session.export.failed": {
        "zh": "导出失败: {}",
        "en": "Export failed: {}",
    },
    "session.unavailable": {
        "zh": "会话管理未初始化，请稍后再试。",
        "en": "Session management not initialized, please try again later.",
    },
    # Tool errors
    "tool.scholar.no_key": {
        "zh": "未配置 SerpAPI key。请在 .env 中设置 SERPAPI_API_KEY。",
        "en": "SerpAPI key not configured. Set SERPAPI_API_KEY in .env.",
    },
    "tool.scholar.failed": {
        "zh": "Google Scholar 搜索失败: {}",
        "en": "Google Scholar search failed: {}",
    },
}


def detect_lang(text: str) -> Lang:
    """Detect language from user text. Returns 'zh' or 'en'.

    For short commands (like /new, /reset), default to Chinese since
    the system is primarily used by Chinese users.
    """
    if not text:
        return "zh"
    stripped = text.strip()
    # Short commands (like /new, /summary) default to Chinese
    if len(stripped) < 20 and not _CJK_RE.search(stripped):
        # Check if it looks like an English sentence (has spaces)
        if " " in stripped:
            return "en"
        return "zh"
    cjk_count = len(_CJK_RE.findall(stripped[:200]))
    total = min(len(stripped[:200]), 200)
    if total > 0 and cjk_count / total > 0.3:
        return "zh"
    return "en"


def t(key: str, *args: object, lang: Lang = "zh") -> str:
    """Translate a UI string.

    Falls back to Chinese if the key or language is missing.
    Supports simple ``{}`` formatting via ``*args``.
    """
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    template = entry.get(lang) or entry.get("zh") or key
    if args:
        try:
            template = template.format(*args)
        except (IndexError, KeyError):
            pass
    return template
