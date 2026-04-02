"""
adapters/openclaw_adapter.py - Discord integration adapter

Sends job status updates to a Discord channel via webhook or bot token.
Receiving tasks from Discord (poll_for_tasks) remains a mock stub because
that requires a full bot with Gateway intents; webhook is send-only.

Environment variables:
  DISCORD_WEBHOOK_URL  - Discord webhook URL (send-only, easiest setup)
  DISCORD_BOT_TOKEN    - Bot token (alternative; requires DISCORD_CHANNEL_ID)
  DISCORD_CHANNEL_ID   - Channel ID for bot token sending
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Discord embed colors per job status keyword
_STATUS_COLORS = {
    "COMPLETED": 0x00C853,      # green
    "FAILED": 0xD50000,         # red
    "REWORK": 0xFFAB00,         # amber
    "REWORK_REQUESTED": 0xFFAB00,
    "PLANNING": 0x2196F3,       # blue
    "EXECUTING": 0x2196F3,
    "VALIDATING": 0x2196F3,
    "AUDITING": 0x2196F3,
    "RECEIVED": 0x9E9E9E,       # grey
}
_DEFAULT_COLOR = 0x5865F2       # Discord blurple

DISCORD_API = "https://discord.com/api/v10"


class OpenClawAdapter:
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        bot_token: Optional[str] = None,
        channel_id: Optional[str] = None,
    ):
        self.webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.bot_token = bot_token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self.channel_id = channel_id or os.environ.get("DISCORD_CHANNEL_ID", "")
        self.on_task_received: Optional[Callable[[str], None]] = None

        if self.webhook_url:
            logger.info("OpenClaw adapter: webhook mode")
        elif self.bot_token and self.channel_id:
            logger.info("OpenClaw adapter: bot token mode")
        else:
            logger.warning(
                "OpenClaw adapter: no Discord credentials set — "
                "set DISCORD_WEBHOOK_URL or (DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID)"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_task_callback(self, callback: Callable[[str], None]) -> None:
        """Register callback for when a task is received from Discord."""
        self.on_task_received = callback

    def send_status_update(self, job_id: str, status: str, summary: str = "") -> bool:
        """Send a job status update to Discord.

        Tries webhook first, falls back to bot token, logs if neither configured.
        Returns True on success.
        """
        embed = self._build_embed(job_id, status, summary)

        if self.webhook_url:
            return self._post_webhook({"embeds": [embed]})
        elif self.bot_token and self.channel_id:
            return self._post_bot({"embeds": [embed]})
        else:
            logger.info(
                f"[DISCORD-SKIP] job={job_id[:8]} status={status}"
                + (f" summary={summary[:80]}" if summary else "")
            )
            return True  # no-op when Discord not configured

    def poll_for_tasks(self) -> list[str]:
        """Poll for new tasks.

        Webhook is send-only; real task ingestion requires a Discord bot with
        message intents (discord.py / discord.js). Returns empty list unless
        a test mock is needed.
        """
        logger.debug("poll_for_tasks: webhook is send-only; no tasks polled")
        return []

    def start_listening(self) -> None:
        """No-op. Real listening needs a Gateway-connected bot."""
        logger.info("OpenClaw adapter started (webhook send-only mode)")

    def stop_listening(self) -> None:
        logger.info("OpenClaw adapter stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_embed(self, job_id: str, status: str, summary: str) -> dict:
        color = _STATUS_COLORS.get(status.upper(), _DEFAULT_COLOR)
        embed: dict = {
            "title": f"Robot Orchestrator — {status}",
            "color": color,
            "fields": [{"name": "Job ID", "value": f"`{job_id}`", "inline": False}],
        }
        if summary:
            embed["description"] = summary[:2048]
        return embed

    def _post_webhook(self, payload: dict) -> bool:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                # Discord returns 204 No Content on success
                ok = resp.status in (200, 204)
                if ok:
                    logger.info(f"Discord webhook sent (HTTP {resp.status})")
                else:
                    logger.warning(f"Discord webhook unexpected status {resp.status}")
                return ok
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            logger.error(f"Discord webhook HTTP {e.code}: {body}")
            return False
        except Exception as e:
            logger.error(f"Discord webhook error: {e}")
            return False

    def _post_bot(self, payload: dict) -> bool:
        url = f"{DISCORD_API}/channels/{self.channel_id}/messages"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bot {self.bot_token}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = resp.status in (200, 201)
                if ok:
                    logger.info(f"Discord bot message sent (HTTP {resp.status})")
                else:
                    logger.warning(f"Discord bot unexpected status {resp.status}")
                return ok
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:200]
            logger.error(f"Discord bot HTTP {e.code}: {body}")
            return False
        except Exception as e:
            logger.error(f"Discord bot error: {e}")
            return False
