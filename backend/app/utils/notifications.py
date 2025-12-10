"""Notification system for team alerts"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

NOTIFICATIONS_FILE = Path("notifications.json")


def notify_team(channel: str, data: dict[str, Any]) -> None:
    """
    Send a notification to a team channel.
    For MVP, writes to notifications.json file.

    Args:
        channel: Target channel (supply-chain, avionics, management)
        data: Notification payload (verdict, reasoning, etc.)
    """
    notification = {
        "channel": channel,
        "timestamp": datetime.now().isoformat(),
        "data": data,
    }

    # Log to console
    logger.info(f"📢 NOTIFICATION to #{channel}")
    logger.info(f"   {json.dumps(data, indent=2)}")

    # Append to notifications file
    try:
        # Read existing notifications
        if NOTIFICATIONS_FILE.exists():
            with open(NOTIFICATIONS_FILE, "r") as f:
                notifications = json.load(f)
        else:
            notifications = []

        # Append new notification
        notifications.append(notification)

        # Write back
        with open(NOTIFICATIONS_FILE, "w") as f:
            json.dump(notifications, f, indent=2, default=str)

        logger.info(f"   ✓ Notification written to {NOTIFICATIONS_FILE}")

    except Exception as e:
        logger.error(f"Failed to write notification: {e}")


def get_notifications(channel: Optional[str] = None) -> list[dict]:
    """
    Retrieve notifications from file.

    Args:
        channel: Optional channel filter

    Returns:
        List of notifications
    """
    if not NOTIFICATIONS_FILE.exists():
        return []

    try:
        with open(NOTIFICATIONS_FILE, "r") as f:
            notifications = json.load(f)

        if channel:
            notifications = [n for n in notifications if n["channel"] == channel]

        return notifications

    except Exception as e:
        logger.error(f"Failed to read notifications: {e}")
        return []


def clear_notifications() -> None:
    """Clear all notifications (for testing)"""
    if NOTIFICATIONS_FILE.exists():
        NOTIFICATIONS_FILE.unlink()
        logger.info("Notifications cleared")
