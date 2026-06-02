"""Session management commands: /new, /reset, /summary, /export.

These commands are intercepted before the agent loop runs, so they
respond instantly without consuming LLM tokens.

Commands:
  /new or /reset — archive current session, start fresh
  /summary      — generate a brief summary of the current session
  /export       — export current session as Markdown file
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from loguru import logger

from ..core.i18n import detect_lang, t

if TYPE_CHECKING:
    from ..memory.manager import MemoryManager
    from ..memory.session_context import SessionContextProvider

SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

# Command patterns — match at the start of the message, case-insensitive
_CMD_RE = re.compile(
    r"^\s*/(?:new|reset|summary|export)\s*$",
    re.IGNORECASE,
)


def _find_session_provider(memory_manager: "MemoryManager") -> Optional["SessionContextProvider"]:
    """Find the SessionContextProvider in the memory manager's provider list."""
    from ..memory.session_context import SessionContextProvider
    for provider in memory_manager.providers:
        if isinstance(provider, SessionContextProvider):
            return provider
    return None


def try_handle_command(
    text: str,
    session_id: str,
    memory_manager: "MemoryManager",
    workspace_dir: Optional[Path] = None,
) -> Optional[str]:
    """If ``text`` is a session command, execute and return a reply.

    Returns None if ``text`` is not a command — the caller should
    proceed with the normal agent loop.
    """
    if not text or not _CMD_RE.match(text.strip()):
        return None

    lang = detect_lang(text)
    cmd = text.strip().lower()
    provider = _find_session_provider(memory_manager)
    if provider is None:
        return t("session.unavailable", lang=lang)

    if cmd in ("/new", "/reset"):
        return _handle_new(session_id, memory_manager, lang)
    elif cmd == "/summary":
        return _handle_summary(session_id, provider, lang)
    elif cmd == "/export":
        return _handle_export(session_id, provider, workspace_dir, lang)

    return None


def _handle_new(session_id: str, memory_manager: "MemoryManager", lang: str = "zh") -> str:
    """Reset the current session — archive old turns, start fresh."""
    try:
        results = memory_manager.reset_session(
            session_id=session_id,
            reason="user_command",
        )
        if results:
            detail = "; ".join(results)
            return f"{t('session.reset.ok', lang=lang)} {detail}"
        return t("session.reset.no_history", lang=lang)
    except Exception as exc:
        logger.exception("[session_commands] reset failed")
        return t("session.reset.failed", exc, lang=lang)


def _handle_summary(session_id: str, provider: "SessionContextProvider", lang: str = "zh") -> str:
    """Generate a brief summary of the current session turns."""
    try:
        turns = provider._read_turns_with_redis(session_id)
        if not turns:
            return t("session.summary.empty", lang=lang)

        topics: list[str] = []
        turn_count = len(turns)
        for turn in turns:
            user_text = (turn.user or "").strip()
            if user_text:
                first_line = user_text.split("\n")[0].strip()
                if len(first_line) > 60:
                    first_line = first_line[:60] + "..."
                topics.append(first_line)

        lines = [
            t("session.summary.header", lang=lang),
            t("session.summary.turns", turn_count, lang=lang),
            t("session.summary.session_id", session_id, lang=lang),
            "",
            t("session.summary.topics", lang=lang),
        ]
        for i, topic in enumerate(topics[-10:], 1):
            lines.append(f"  {i}. {topic}")

        if turn_count > 10:
            if lang == "zh":
                lines.append(f"  ... (共 {turn_count} 轮)")
            else:
                lines.append(f"  ... ({turn_count} turns total)")

        return "\n".join(lines)
    except Exception as exc:
        logger.exception("[session_commands] summary failed")
        if lang == "zh":
            return f"生成摘要失败: {exc}"
        return f"Summary generation failed: {exc}"


def _handle_export(
    session_id: str,
    provider: "SessionContextProvider",
    workspace_dir: Optional[Path] = None,
    lang: str = "zh",
) -> str:
    """Export current session as a Markdown file."""
    try:
        turns = provider._read_turns_with_redis(session_id)
        if not turns:
            return t("session.export.empty", lang=lang)

        now = datetime.now(SHANGHAI_TZ)
        safe_sid = re.sub(r"[^\w\-]", "_", session_id)
        filename = f"session_{safe_sid}_{now.strftime('%Y%m%d_%H%M%S')}.md"

        if workspace_dir:
            export_dir = workspace_dir / "sessions"
        else:
            export_dir = Path("workspace/sessions")
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / filename

        lines = [
            f"# {'会话导出' if lang == 'zh' else 'Session Export'}",
            f"",
            f"- **{'会话 ID' if lang == 'zh' else 'Session ID'}**: {session_id}",
            f"- **{'导出时间' if lang == 'zh' else 'Exported at'}**: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **{'对话轮数' if lang == 'zh' else 'Turn count'}**: {len(turns)}",
            f"",
            f"---",
            f"",
            f"## {'对话记录' if lang == 'zh' else 'Conversation'}",
            f"",
        ]

        for i, turn in enumerate(turns, 1):
            user_text = (turn.user or "").strip()
            assistant_text = (turn.assistant or "").strip()
            user_label = "用户" if lang == "zh" else "User"
            asst_label = "助手" if lang == "zh" else "Assistant"
            empty_label = "(空)" if lang == "zh" else "(empty)"
            lines.append(f"### {i}. {user_label}")
            lines.append(user_text if user_text else empty_label)
            lines.append(f"")
            lines.append(f"### {i}. {asst_label}")
            lines.append(assistant_text if assistant_text else empty_label)
            lines.append(f"")
            lines.append(f"---")
            lines.append(f"")

        export_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("[session_commands] exported session to {}", export_path)
        return t("session.export.ok", export_path, len(turns), lang=lang)
    except Exception as exc:
        logger.exception("[session_commands] export failed")
        return t("session.export.failed", exc, lang=lang)
