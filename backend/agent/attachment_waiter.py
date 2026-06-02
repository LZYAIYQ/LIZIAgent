"""Attachment arrival detection and waiting.

When a user sends a message like "帮我看看这个 PDF" with an attachment,
the text message often arrives before the attachment is fully uploaded.
This module detects file-related intent and optionally waits for
attachments to arrive before processing.

Design:
- Pattern-match user text for file-related keywords
- If keywords present but no attachment, wait up to N seconds
- Poll attachment state periodically
- After timeout, proceed anyway (agent will ask for the file)
"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ..gateways.base import IncomingMessage

# File-related keywords (Chinese + English)
_FILE_KEYWORDS = re.compile(
    r"pdf|文件|文档|附件|excel|word|ppt|csv|txt|图片|照片|截图|"
    r"file|document|attach|upload|看看这个|帮我看看|打开|解析|提取",
    re.IGNORECASE,
)

# Attachment-related keywords that suggest the user WILL send a file
_FILE_INTENT = re.compile(
    r"这个|这份|下面的| attached| here is| sending| 跟着发| 一起发",
    re.IGNORECASE,
)

DEFAULT_WAIT_SECONDS = 3.0
POLL_INTERVAL_SECONDS = 0.5


def _has_file_intent(text: str) -> bool:
    """Check if the user text suggests they want to send a file."""
    if not text:
        return False
    return bool(_FILE_KEYWORDS.search(text))


def _has_attachment(message: "IncomingMessage") -> bool:
    """Check if the message has any non-image attachments."""
    return any(
        att.kind in ("file", "audio", "video") and att.url
        for att in message.attachments
    )


def _has_image_attachment(message: "IncomingMessage") -> bool:
    """Check if the message has image attachments."""
    return any(
        att.kind == "image" and att.url
        for att in message.attachments
    )


async def wait_for_attachments(
    message: "IncomingMessage",
    text: str,
    *,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> bool:
    """Wait for attachments to arrive if the user text suggests file intent.

    Returns True if attachments arrived, False if timed out.
    """
    # If attachments already present, no need to wait
    if _has_attachment(message) or _has_image_attachment(message):
        return True

    # If no file intent in text, no need to wait
    if not _has_file_intent(text):
        return True

    # Wait for attachments to arrive
    logger.info(
        "[attachment_waiter] file intent detected, waiting up to {}s for attachment",
        wait_seconds,
    )
    elapsed = 0.0
    while elapsed < wait_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        if _has_attachment(message) or _has_image_attachment(message):
            logger.info(
                "[attachment_waiter] attachment arrived after {:.1f}s", elapsed,
            )
            return True

    logger.info("[attachment_waiter] no attachment after {:.1f}s, proceeding", elapsed)
    return False


def get_attachment_summary(message: "IncomingMessage") -> str:
    """Get a summary of attachments for the agent context."""
    attachments = message.attachments
    if not attachments:
        return ""

    parts: list[str] = []
    for att in attachments:
        if att.kind == "image":
            parts.append(f"图片: {att.name or '未命名'}")
        elif att.kind == "file":
            parts.append(f"文件: {att.name or '未命名'} ({att.mime_type or '未知格式'})")
        elif att.kind == "audio":
            parts.append(f"音频: {att.name or '未命名'}")
        elif att.kind == "video":
            parts.append(f"视频: {att.name or '未命名'}")

    return "附件: " + ", ".join(parts) if parts else ""
