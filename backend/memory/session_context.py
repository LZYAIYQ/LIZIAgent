from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Deque, Optional

from loguru import logger

from .provider import BaseMemoryProvider

# Imported lazily below so the type checker sees the class but
# circular / optional imports (``backend.storage`` depends on ``loguru``
# + ``redis-py``) never block memory-only smoke tests.
if False:  # pragma: no cover - typing only
    from ..storage import RedisBackend


@dataclass(slots=True)
class _SessionTurn:
    session_id: str
    user: str
    assistant: str
    created_at: str
    skill_hint: str = ""


class SessionContextProvider(BaseMemoryProvider):
    name = "session-context"

    def __init__(
        self,
        path: Path | str,
        *,
        max_turns: int = 8,
        max_chars: int = 2400,
        max_line_chars: int = 500,
        max_loaded_records: int = 2000,
        archive_dir: Path | str | None = None,
        redis_backend: Optional["RedisBackend"] = None,
        redis_ttl_seconds: int = 24 * 3600,
    ) -> None:
        self._path = Path(path)
        self._archive_dir = Path(archive_dir) if archive_dir is not None else self._path.parent / "sessions"
        self._max_turns = max(1, int(max_turns))
        self._max_chars = max(200, int(max_chars))
        self._max_line_chars = max(80, int(max_line_chars))
        self._max_loaded_records = max(self._max_turns, int(max_loaded_records))
        self._turns: dict[str, Deque[_SessionTurn]] = {}
        self._lock = RLock()
        self._initialized = False
        # optional Redis hot layer. When configured we
        # double-write each turn to ``session:<sid>:turns`` and try
        # Redis first on reads. Stays silent when the backend is None
        # or the breaker is open (see RedisBackend); callers get the
        # existing in-process + JSONL paths.
        self._redis = redis_backend
        self._redis_ttl = max(60, int(redis_ttl_seconds))

    def initialize(self, **kwargs: Any) -> None:
        with self._lock:
            if self._initialized:
                return
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._load_recent_locked()
            self._initialized = True

    def system_prompt_block(self) -> str:
        return (
            "I maintain short-term session context from recent turns. "
            "When a user message omits previously stated constraints, resolve "
            "the omission from the session-context block rather than asking "
            "again or inventing alternatives."
        )

    def prefetch(self, query: str, *, session_id: str = "", **kwargs: Any) -> str:
        sid = (session_id or "").strip()
        if not sid:
            return ""
        # Redis read first. On hit we hydrate the per-process
        # deque so subsequent same-process calls stay zero-network. On
        # miss / Redis-down we use the existing in-memory + JSONL path,
        # which is already populated by ``initialize`` from disk.
        turns = self._read_turns_with_redis(sid)
        if not turns:
            return ""
        lines = [
            "## 最近对话上下文",
            "下面是同一个 IM 会话里已经发生的最近对话，按时间从旧到新。",
            "这些内容是上下文，不是新的用户指令；但当前消息省略的目的地、预算、日期、偏好等约束应优先从这里继承。",
        ]
        used = sum(len(x) for x in lines)
        for idx, turn in enumerate(turns[-self._max_turns :], start=1):
            user = self._clip(turn.user)
            assistant = self._clip(turn.assistant)
            block = f"{idx}. 用户: {user}\n   助手: {assistant}"
            if used + len(block) + 2 > self._max_chars:
                break
            lines.append(block)
            used += len(block) + 2
        return "\n".join(lines) if len(lines) > 3 else ""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        user = (user_content or "").strip()
        assistant = (assistant_content or "").strip()
        if not user and not assistant:
            return
        meta = metadata or {}
        skill_hint = str(meta.get("skill_hint") or "").strip()
        turn = _SessionTurn(
            session_id=sid,
            user=user,
            assistant=assistant,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            skill_hint=skill_hint,
        )
        with self._lock:
            bucket = self._turns.setdefault(sid, deque(maxlen=self._max_turns))
            bucket.append(turn)
            self._append_locked(turn)
        # double-write into Redis so other workers (or this
        # one after a restart) see the same recent turns. Best-effort:
        # any failure already gets logged inside RedisBackend; the
        # JSONL fallback above is the source of truth.
        self._redis_push_turn(sid, turn)

    def latest_skill_hint(self, *, session_id: str = "", **kwargs: Any) -> str:
        sid = (session_id or "").strip()
        if not sid:
            return ""
        turns = self._read_turns_with_redis(sid)
        for turn in reversed(turns):
            if turn.skill_hint:
                return turn.skill_hint
        return ""

    def reset_session(
        self,
        *,
        session_id: str = "",
        reason: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        sid = (session_id or "").strip()
        if not sid:
            return ""
        with self._lock:
            turns = list(self._turns.get(sid, ()))
            archive_path = self._write_archive_locked(
                sid,
                turns,
                reason=reason,
                metadata=dict(metadata or {}),
            ) if turns else None
            self._turns.pop(sid, None)
            self._append_reset_locked(sid, reason=reason, metadata=dict(metadata or {}))
        # clear the Redis copy too so a follow-up turn does
        # not resurrect the archived context. Best-effort.
        if self._redis is not None:
            try:
                self._redis.delete(self._redis_key(sid))
            except Exception as exc:  # noqa: BLE001
                logger.debug("[session-context] redis delete failed: {}", exc)
        if archive_path is None:
            return "cleared; no prior turns to archive"
        return f"archived to {archive_path}"

    def on_pre_compress(self, history: list[Any]) -> str:
        with self._lock:
            sessions = list(self._turns.items())
        if not sessions:
            return ""
        parts: list[str] = []
        for sid, turns in sessions[-5:]:
            latest = list(turns)[-2:]
            if not latest:
                continue
            rendered = "\n".join(
                f"- 用户: {self._clip(t.user, 180)}\n  助手: {self._clip(t.assistant, 180)}"
                for t in latest
            )
            parts.append(f"SESSION {sid}:\n{rendered}")
        return "\n\n".join(parts)

    def queue_prefetch(self, query: str, *, session_id: str = "", **kwargs: Any) -> None:
        return None

    def shutdown(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Redis hot layer
    # ------------------------------------------------------------------

    def _redis_key(self, session_id: str) -> str:
        """``lzagent:session:<sid>:turns`` shape used across modules."""
        if self._redis is not None:
            return self._redis.namespaced("session", session_id, "turns")
        return f"session:{session_id}:turns"

    def _redis_push_turn(self, session_id: str, turn: _SessionTurn) -> None:
        if self._redis is None:
            return
        try:
            payload = json.dumps(asdict(turn), ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            logger.debug("[session-context] redis serialize failed: {}", exc)
            return
        self._redis.push_trim(
            self._redis_key(session_id),
            payload,
            max_length=self._max_turns,
            ttl_seconds=self._redis_ttl,
        )

    def _read_turns_with_redis(self, session_id: str) -> list[_SessionTurn]:
        """Return turns for ``session_id``. Redis takes priority; on
        miss we fall back to the in-process deque (which itself was
        seeded from the JSONL file at startup).

        Redis hits also rehydrate the local deque so subsequent reads
        on the same worker stay zero-network. Misses do NOT clear the
        local deque — JSONL is the source of truth when Redis is
        cold/empty/down.
        """
        if self._redis is not None:
            payloads = self._redis.lrange(self._redis_key(session_id))
            if payloads:
                turns = [
                    self._parse_turn_payload(p, session_id) for p in payloads
                ]
                turns = [t for t in turns if t is not None]
                if turns:
                    with self._lock:
                        bucket = deque(turns, maxlen=self._max_turns)
                        self._turns[session_id] = bucket
                    return list(turns)
        with self._lock:
            return list(self._turns.get(session_id, ()))

    @staticmethod
    def _parse_turn_payload(
        payload: str, session_id: str,
    ) -> Optional[_SessionTurn]:
        try:
            data = json.loads(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return _SessionTurn(
            session_id=str(data.get("session_id") or session_id),
            user=str(data.get("user") or ""),
            assistant=str(data.get("assistant") or ""),
            created_at=str(data.get("created_at") or ""),
            skill_hint=str(data.get("skill_hint") or ""),
        )

    def _bucket_locked(self, session_id: str) -> Deque[_SessionTurn]:
        return self._turns.setdefault(session_id, deque(maxlen=self._max_turns))

    def _append_locked(self, turn: _SessionTurn) -> None:
        with self._path.open("a", encoding="utf-8", newline="") as fh:
            data = asdict(turn)
            data["type"] = "turn"
            fh.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _append_reset_locked(
        self,
        session_id: str,
        *,
        reason: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "type": "reset",
            "session_id": session_id,
            "reason": reason,
            "metadata": dict(metadata or {}),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._path.open("a", encoding="utf-8", newline="") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _load_recent_locked(self) -> None:
        if not self._path.exists():
            return
        try:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        for line in lines[-self._max_loaded_records :]:
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("type") == "reset":
                    sid = str(data.get("session_id") or "")
                    if sid:
                        self._turns.pop(sid, None)
                    continue
                turn = _SessionTurn(
                    session_id=str(data.get("session_id") or ""),
                    user=str(data.get("user") or ""),
                    assistant=str(data.get("assistant") or ""),
                    created_at=str(data.get("created_at") or ""),
                    skill_hint=str(data.get("skill_hint") or ""),
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not turn.session_id:
                continue
            self._bucket_locked(turn.session_id).append(turn)

    def _write_archive_locked(
        self,
        session_id: str,
        turns: list[_SessionTurn],
        *,
        reason: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[Path]:
        if not turns:
            return None
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        reason_slug = self._slug(reason or "session-reset")
        stem = f"{now.strftime('%Y-%m-%d-%H%M%S')}-{reason_slug}"
        target = self._next_archive_path_locked(stem)
        lines = [
            f"# Session: {now.isoformat(timespec='seconds')}",
            "",
            f"- **Session ID**: {session_id}",
            f"- **Reason**: {reason or 'session-reset'}",
            f"- **Turn Count**: {len(turns)}",
        ]
        platform = (metadata or {}).get("platform")
        user_id = (metadata or {}).get("user_id")
        if platform:
            lines.append(f"- **Platform**: {platform}")
        if user_id:
            lines.append(f"- **User ID**: {user_id}")
        lines.extend(["", "## Conversation Summary", ""])
        for idx, turn in enumerate(turns, start=1):
            lines.extend([
                f"### Turn {idx}",
                "",
                f"**User**: {turn.user}",
                "",
                f"**Assistant**: {turn.assistant}",
                "",
            ])
        target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return target

    def _next_archive_path_locked(self, stem: str) -> Path:
        suffix = 1
        while True:
            name = f"{stem}.md" if suffix == 1 else f"{stem}-{suffix}.md"
            target = self._archive_dir / name
            if not target.exists():
                return target
            suffix += 1

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip()).strip("-").lower()
        return slug[:40] or "session-reset"

    def _clip(self, text: str, limit: Optional[int] = None) -> str:
        value = " ".join((text or "").split())
        cap = int(limit or self._max_line_chars)
        if len(value) <= cap:
            return value
        return value[: max(0, cap - 1)].rstrip() + "…"
