"""User management for multi-user support.

Provides user registration, permission levels, and user-scoped
memory isolation. Users are identified by ``platform:user_id``
(e.g., ``weixin:o9cq80-xxx``).

Permission levels:
- ``admin``: full access, can manage other users
- ``user``: standard access, isolated memory
- ``guest``: limited access, no persistent memory
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from loguru import logger

from .models import User
from .session import session_scope


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"
    GUEST = "guest"


def _row_to_dict(row: User) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_key": row.user_key,
        "platform": row.platform,
        "user_id": row.user_id,
        "display_name": row.display_name,
        "role": row.role,
        "knowledge_base_id": row.knowledge_base_id,
        "enabled": row.enabled,
        "turn_count": row.turn_count,
        "last_active_at": row.last_active_at.isoformat() if row.last_active_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class UserManager:
    """Manages user registration and lookup."""

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}

    def get_or_create(
        self,
        platform: str,
        user_id: str,
        *,
        display_name: str = "",
    ) -> dict[str, Any]:
        """Get existing user or create a new one."""
        user_key = f"{platform}:{user_id}"

        # Check cache first
        if user_key in self._cache:
            return self._cache[user_key]

        with session_scope() as session:
            row = session.query(User).filter(User.user_key == user_key).first()
            if row is None:
                # Auto-create with default role
                kb_id = f"user_{platform}_{user_id}"[:64]
                row = User(
                    user_key=user_key,
                    platform=platform,
                    user_id=user_id,
                    display_name=display_name,
                    role=UserRole.USER.value,
                    knowledge_base_id=kb_id,
                )
                session.add(row)
                session.flush()
                logger.info("[users] auto-created user: {} (kb={})", user_key, kb_id)
            result = _row_to_dict(row)

        self._cache[user_key] = result
        return result

    def get_by_key(self, user_key: str) -> Optional[dict[str, Any]]:
        """Get user by platform:user_id key."""
        if user_key in self._cache:
            return self._cache[user_key]

        with session_scope() as session:
            row = session.query(User).filter(User.user_key == user_key).first()
            if row is None:
                return None
            result = _row_to_dict(row)

        self._cache[user_key] = result
        return result

    def list_users(self) -> list[dict[str, Any]]:
        """List all registered users."""
        with session_scope() as session:
            rows = session.query(User).order_by(User.created_at.desc()).all()
            return [_row_to_dict(r) for r in rows]

    def set_role(self, user_key: str, role: str) -> bool:
        """Update user role."""
        if role not in [r.value for r in UserRole]:
            return False
        with session_scope() as session:
            row = session.query(User).filter(User.user_key == user_key).first()
            if row is None:
                return False
            row.role = role
        self._cache.pop(user_key, None)
        return True

    def set_enabled(self, user_key: str, enabled: bool) -> bool:
        """Enable or disable a user."""
        with session_scope() as session:
            row = session.query(User).filter(User.user_key == user_key).first()
            if row is None:
                return False
            row.enabled = enabled
        self._cache.pop(user_key, None)
        return True

    def increment_turn(self, user_key: str) -> None:
        """Increment turn count and update last_active_at."""
        with session_scope() as session:
            row = session.query(User).filter(User.user_key == user_key).first()
            if row is not None:
                row.turn_count = (row.turn_count or 0) + 1
                row.last_active_at = datetime.utcnow()

    def get_knowledge_base_id(self, platform: str, user_id: str) -> str:
        """Get the knowledge_base_id for a user (creates if needed)."""
        user = self.get_or_create(platform, user_id)
        return user["knowledge_base_id"]

    def is_admin(self, platform: str, user_id: str) -> bool:
        """Check if a user has admin role."""
        user = self.get_or_create(platform, user_id)
        return user["role"] == UserRole.ADMIN.value

    def is_enabled(self, platform: str, user_id: str) -> bool:
        """Check if a user is enabled."""
        user = self.get_or_create(platform, user_id)
        return user["enabled"]

    def invalidate_cache(self) -> None:
        """Clear the user cache."""
        self._cache.clear()


# Import Base from models
from .models import Base  # noqa: E402
