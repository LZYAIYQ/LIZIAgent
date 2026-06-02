"""Operator REST surface for session management.

Endpoints:

* ``GET    /api/sessions``                         — list active sessions
* ``POST   /api/sessions/{session_id}/reset``      — reset (archive + clear)
* ``GET    /api/sessions/{session_id}/export``     — export as Markdown
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from ..memory.session_context import SessionContextProvider

SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _get_session_provider(request: Request) -> SessionContextProvider:
    """Find the SessionContextProvider from the memory manager."""
    memory_manager = getattr(request.app.state, "memory_manager", None)
    if memory_manager is None:
        raise HTTPException(status_code=503, detail="memory manager not initialised")
    for provider in memory_manager.providers:
        if isinstance(provider, SessionContextProvider):
            return provider
    raise HTTPException(status_code=503, detail="session context provider not found")


# -----------------------------------------------------------------------------
# Response models
# -----------------------------------------------------------------------------


class SessionOut(BaseModel):
    session_id: str
    turn_count: int
    latest_user: str = ""
    latest_assistant: str = ""


class ResetOut(BaseModel):
    session_id: str
    result: str


class ExportOut(BaseModel):
    session_id: str
    file_path: str
    turn_count: int


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@router.get("", response_model=list[SessionOut])
def list_sessions(request: Request) -> list[dict[str, Any]]:
    """List all active (non-empty) sessions."""
    provider = _get_session_provider(request)
    sessions: list[dict[str, Any]] = []
    for sid, turns in provider._turns.items():
        turn_list = list(turns)
        if not turn_list:
            continue
        latest = turn_list[-1]
        sessions.append({
            "session_id": sid,
            "turn_count": len(turn_list),
            "latest_user": (latest.user or "")[:100],
            "latest_assistant": (latest.assistant or "")[:100],
        })
    return sessions


@router.post("/{session_id:path}/reset", response_model=ResetOut)
def reset_session(request: Request, session_id: str) -> dict[str, str]:
    """Reset a session — archive old turns and clear context."""
    provider = _get_session_provider(request)
    try:
        result = provider.reset_session(
            session_id=session_id,
            reason="api_reset",
        )
        return {"session_id": session_id, "result": result or "reset complete"}
    except Exception as exc:
        logger.exception("[sessions API] reset failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{session_id:path}/export", response_model=ExportOut)
def export_session(request: Request, session_id: str) -> dict[str, Any]:
    """Export a session as Markdown."""
    provider = _get_session_provider(request)
    turns = provider._read_turns_with_redis(session_id)
    if not turns:
        raise HTTPException(status_code=404, detail="no turns found for this session")

    from ..core.config import get_settings
    workspace_dir = get_settings().workspace_dir

    now = datetime.now(SHANGHAI_TZ)
    safe_sid = re.sub(r"[^\w\-]", "_", session_id)
    filename = f"session_{safe_sid}_{now.strftime('%Y%m%d_%H%M%S')}.md"
    export_dir = workspace_dir / "sessions"
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / filename

    lines = [
        f"# 会话导出",
        f"",
        f"- **会话 ID**: {session_id}",
        f"- **导出时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- **对话轮数**: {len(turns)}",
        f"",
        f"---",
        f"",
        f"## 对话记录",
        f"",
    ]

    for i, turn in enumerate(turns, 1):
        user_text = (turn.user or "").strip()
        assistant_text = (turn.assistant or "").strip()
        lines.append(f"### {i}. 用户")
        lines.append(user_text if user_text else "(空)")
        lines.append("")
        lines.append(f"### {i}. 助手")
        lines.append(assistant_text if assistant_text else "(空)")
        lines.append("")
        lines.append("---")
        lines.append("")

    export_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("[sessions API] exported to {}", export_path)
    return {
        "session_id": session_id,
        "file_path": str(export_path),
        "turn_count": len(turns),
    }
