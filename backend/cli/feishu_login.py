"""Feishu gateway setup CLI.

Usage:
    python -m backend.cli.feishu_login

Interactive setup:
1. Enter App ID and App Secret
2. Test connection
3. Save credentials
4. Start gateway

Similar to weixin_login but for Feishu using WebSocket long-connection mode.
No public IP or port forwarding required.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx


FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
FEISHU_APP_INFO_URL = "https://open.feishu.cn/open-apis/application/v6/applications/underauditlist"


async def get_tenant_token(app_id: str, app_secret: str) -> tuple[str, str]:
    """Get tenant access token. Returns (token, error)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                FEISHU_TOKEN_URL,
                json={"app_id": app_id, "app_secret": app_secret},
            )
            data = resp.json()
            if data.get("code") == 0:
                return data["tenant_access_token"], ""
            return "", f"Error: {data.get('msg', 'unknown')}"
        except Exception as exc:
            return "", f"Connection error: {exc}"


async def test_connection(app_id: str, app_secret: str) -> bool:
    """Test if credentials are valid."""
    token, error = await get_tenant_token(app_id, app_secret)
    if error:
        print(f"  ✗ Connection failed: {error}")
        return False
    print("  ✓ Connection successful!")
    return True


def save_credentials(app_id: str, app_secret: str) -> Path:
    """Save credentials to workspace config file."""
    config_dir = Path("/app/workspace")
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "feishu_config.json"

    import json
    config = {
        "app_id": app_id,
        "app_secret": app_secret,
    }
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path


def main() -> int:
    """Interactive Feishu gateway setup."""
    print("=" * 50)
    print("  Feishu Gateway Setup")
    print("=" * 50)
    print()
    print("This will configure LZAgent to connect to Feishu.")
    print("No public IP or port forwarding required!")
    print()

    # Get credentials
    print("Step 1: Enter your Feishu app credentials")
    print("  (Get them from https://open.feishu.cn/app)")
    print()

    app_id = input("  App ID: ").strip()
    if not app_id:
        print("  ✗ App ID is required")
        return 1

    app_secret = input("  App Secret: ").strip()
    if not app_secret:
        print("  ✗ App Secret is required")
        return 1

    # Test connection
    print()
    print("Step 2: Testing connection...")
    if not asyncio.run(test_connection(app_id, app_secret)):
        return 1

    # Save credentials
    print()
    print("Step 3: Saving credentials...")
    env_path = save_credentials(app_id, app_secret)
    print(f"  ✓ Saved to {env_path.absolute()}")

    # Instructions
    print()
    print("=" * 50)
    print("  Setup Complete!")
    print("=" * 50)
    print()
    print("Credentials saved to workspace/feishu_config.json")
    print()
    print("IMPORTANT: Add these to your .env file on the HOST machine:")
    print(f"  FEISHU_APP_ID={app_id}")
    print(f"  FEISHU_APP_SECRET={app_secret}")
    print()
    print("Then restart LZAgent:")
    print("  docker compose restart lzagent")
    print()
    print("The gateway uses WebSocket long-connection mode.")
    print("No public URL configuration needed!")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
