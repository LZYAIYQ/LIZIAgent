"""Feishu (Lark) gateway for LZAgent.

Uses WebSocket long-connection mode - no public IP or port forwarding needed.
Similar to WeChat's iLink protocol approach.

Setup:
1. python -m backend.cli.feishu_login
2. Enter App ID and App Secret
3. Restart LZAgent

Reference: https://open.feishu.cn/document/server-docs/event-subscription-guide/long-connection
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from loguru import logger

from .base import (
    Attachment,
    DeliveryTarget,
    IncomingMessage,
    MessageGateway,
    MessageHandler,
    OutgoingMessage,
)

# Feishu API endpoints
FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"
FEISHU_WS_URL = "wss://open.feishu.cn/event/ws"


class FeishuGateway(MessageGateway):
    """Feishu (Lark) gateway using WebSocket long-connection."""

    platform = "feishu"
    kind = "feishu_ws"

    def __init__(
        self,
        handler: MessageHandler,
        *,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
    ) -> None:
        super().__init__(handler)
        self._app_id = app_id or os.getenv("FEISHU_APP_ID", "")
        self._app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "")
        self._tenant_token: str = ""
        self._token_expires_at: float = 0
        self._client: Optional[httpx.AsyncClient] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._running = False

    def is_configured(self) -> bool:
        return bool(self._app_id and self._app_secret)

    def status_extra(self) -> dict:
        return {
            "app_id": self._app_id[:8] + "..." if self._app_id else "",
            "token_valid": bool(self._tenant_token and time.time() < self._token_expires_at),
            "ws_connected": self._ws_task is not None and not self._ws_task.done(),
        }

    async def start(self) -> None:
        if not self.is_configured():
            logger.warning("feishu: not configured (missing FEISHU_APP_ID or FEISHU_APP_SECRET)")
            return

        self._client = httpx.AsyncClient(timeout=30.0)
        await self._refresh_token()

        # Start WebSocket connection
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        logger.info("feishu gateway started (app_id={}, mode=websocket)", self._app_id[:8])

    async def stop(self) -> None:
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    async def _refresh_token(self) -> None:
        if not self._client:
            return
        try:
            resp = await self._client.post(
                FEISHU_TOKEN_URL,
                json={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            data = resp.json()
            if data.get("code") == 0:
                self._tenant_token = data["tenant_access_token"]
                self._token_expires_at = time.time() + data.get("expire", 7200) - 300
                logger.info("feishu: token refreshed")
            else:
                logger.error("feishu: token refresh failed: {}", data)
        except Exception as exc:
            logger.error("feishu: token refresh error: {}", exc)

    async def _get_headers(self) -> dict[str, str]:
        if time.time() >= self._token_expires_at:
            await self._refresh_token()
        return {"Authorization": f"Bearer {self._tenant_token}"}

    # ------------------------------------------------------------------
    # Long-connection mode (using lark-oapi SDK)
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Long-connection loop using lark-oapi SDK."""
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        except ImportError:
            logger.error("feishu: lark-oapi not installed, long-connection mode unavailable")
            return

        # Event handler
        def on_message(data: P2ImMessageReceiveV1) -> None:
            """Handle incoming message."""
            try:
                event = data.event
                message = event.message
                sender = event.sender

                message_id = message.message_id
                chat_id = message.chat_id
                chat_type = message.chat_type
                msg_type = message.message_type
                sender_id = sender.sender_id.open_id

                if sender.sender_type == "app":
                    return

                content_str = message.content or "{}"
                try:
                    content = json.loads(content_str)
                except json.JSONDecodeError:
                    content = {}

                text = ""
                attachments = []

                if msg_type == "text":
                    text = content.get("text", "")
                    if chat_type == "group":
                        import re
                        text = re.sub(r'@_user_\d+\s*', '', text).strip()

                elif msg_type == "file":
                    file_key = content.get("file_key", "")
                    file_name = content.get("file_name", "unknown")
                    if file_key:
                        attachments.append(Attachment(
                            kind="file",
                            name=file_name,
                            mime_type=self._guess_mime(file_name),
                            url=f"feishu://file/{message_id}/{file_key}",
                        ))

                if chat_type == "p2p":
                    target = DeliveryTarget(
                        platform=self.platform,
                        target_type="user",
                        target_id=sender_id,
                        display_name=sender_id,
                    )
                else:
                    target = DeliveryTarget(
                        platform=self.platform,
                        target_type="group",
                        target_id=chat_id,
                        display_name=chat_id,
                    )

                incoming = IncomingMessage(
                    platform=self.platform,
                    channel_id=chat_id or sender_id,
                    user_id=sender_id,
                    message_id=message_id,
                    text=text,
                    attachments=attachments,
                    reply_target=target,
                    raw={"message_id": message_id},
                )

                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.emit(incoming))
                else:
                    loop.run_until_complete(self.emit(incoming))

            except Exception as exc:
                logger.exception("[feishu] message handler error: {}", exc)

        # Build event handler
        handler = lark.EventDispatcherHandler.builder(
            "",  # verification_token (not needed for long-connection)
            "",  # encrypt_key
        ).register_p2_im_message_receive_v1(on_message).build()

        # Create client with long-connection
        cli = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.WARNING,
        )

        logger.info("feishu: starting long-connection...")
        cli.start()

    async def _handle_ws_message(self, raw: str) -> None:
        """Handle a WebSocket message from Feishu."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return

        # Handle different message types
        msg_type = data.get("type", "")

        if msg_type == "event":
            event = data.get("event", {})
            header = event.get("header", {})
            event_type = header.get("event_type", "")

            if event_type == "im.message.receive_v1":
                await self._handle_message_event(event.get("event", {}))

        elif msg_type == "ping":
            # Respond to keepalive
            pass

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_message_event(self, event: dict) -> None:
        """Handle a message receive event."""
        message = event.get("message", {})
        sender = event.get("sender", {})

        message_id = message.get("message_id", "")
        chat_id = message.get("chat_id", "")
        chat_type = message.get("chat_type", "")
        msg_type = message.get("message_type", "")
        sender_id = sender.get("sender_id", {}).get("open_id", "")

        if not sender_id or not message_id:
            return

        # Skip bot's own messages
        if sender.get("sender_type") == "app":
            return

        content_str = message.get("content", "{}")
        try:
            content = json.loads(content_str)
        except json.JSONDecodeError:
            content = {}

        text = ""
        attachments = []

        if msg_type == "text":
            text = content.get("text", "")
            # Remove @mention in group chat
            if chat_type == "group":
                import re
                text = re.sub(r'@_user_\d+\s*', '', text).strip()

        elif msg_type == "image":
            image_key = content.get("image_key", "")
            if image_key:
                attachments.append(Attachment(
                    kind="image",
                    name=f"{image_key}.jpg",
                    mime_type="image/jpeg",
                    url=f"feishu://image/{message_id}/{image_key}",
                ))

        elif msg_type == "file":
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "unknown")
            if file_key:
                attachments.append(Attachment(
                    kind="file",
                    name=file_name,
                    mime_type=self._guess_mime(file_name),
                    url=f"feishu://file/{message_id}/{file_key}",
                ))

        # Build reply target
        if chat_type == "p2p":
            target = DeliveryTarget(
                platform=self.platform,
                target_type="user",
                target_id=sender_id,
                display_name=sender_id,
            )
        else:
            target = DeliveryTarget(
                platform=self.platform,
                target_type="group",
                target_id=chat_id,
                display_name=chat_id,
            )

        incoming = IncomingMessage(
            platform=self.platform,
            channel_id=chat_id or sender_id,
            user_id=sender_id,
            message_id=message_id,
            text=text,
            attachments=attachments,
            reply_target=target,
            raw=event,
        )

        try:
            await self.emit(incoming)
        except Exception as exc:
            logger.exception("[feishu] handler error: {}", exc)

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------

    async def send(self, message: OutgoingMessage) -> None:
        """Send a message to Feishu."""
        if not self._client or not self._tenant_token:
            logger.warning("feishu: cannot send, not initialized")
            return

        target = message.target
        receive_id = target.target_id
        receive_id_type = "open_id" if target.target_type == "user" else "chat_id"

        content = json.dumps({"text": message.text})
        headers = await self._get_headers()
        headers["Content-Type"] = "application/json; charset=utf-8"

        url = f"{FEISHU_MESSAGE_URL}?receive_id_type={receive_id_type}"
        try:
            resp = await self._client.post(
                url,
                headers=headers,
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": content,
                },
            )
            data = resp.json()
            if data.get("code") == 0:
                logger.info("[feishu send] ok len={}", len(message.text))
            else:
                logger.error("[feishu send] failed: {}", data)
        except Exception as exc:
            logger.error("[feishu send] error: {}", exc)

    @staticmethod
    def _guess_mime(filename: str) -> str:
        ext = Path(filename).suffix.lower()
        mimes = {
            ".pdf": "application/pdf",
            ".doc": "application/msword",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".txt": "text/plain",
            ".csv": "text/csv",
            ".json": "application/json",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }
        return mimes.get(ext, "application/octet-stream")


# ------------------------------------------------------------------
# FastAPI router (for status check only)
# ------------------------------------------------------------------

def build_feishu_router(gateway: FeishuGateway) -> Any:
    """Build FastAPI router for Feishu status."""
    from fastapi import APIRouter

    router = APIRouter(prefix="/api/gateways/feishu", tags=["feishu"])

    @router.get("/status")
    async def status() -> dict:
        return {
            "configured": gateway.is_configured(),
            "platform": gateway.platform,
            "mode": "websocket",
            **gateway.status_extra(),
        }

    return router
