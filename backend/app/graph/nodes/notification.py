"""Notification Node - Simplified 2-channel notifications"""

import logging
from app.graph.state import BatchGraphState
from app.utils import notify_team

logger = logging.getLogger(__name__)


def notification_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 6: Notification (Simplified)

    Sends notifications to 2 channels:
    1. sync-alerts: All issues (conflicts + low-confidence opportunities)
    2. auto-applied: What the system fixed automatically

    Args:
        state: Current graph state with verdicts and auto_applied_updates

    Returns:
        Updated state with notifications_sent count
    """
    logger.info("=" * 70)
    logger.info("📢 NOTIFICATION: Sending team alerts...")
    logger.info("=" * 70)

    verdicts = state.get("verdicts", [])
    auto_applied_updates = state.get("auto_applied_updates", [])

    if not verdicts and not auto_applied_updates:
        logger.info("   No notifications to send")
        return {
            **state,
            "notifications_sent": 0,
            "current_node": "notification_complete"
        }

    notifications_sent = 0

    # 1. Send auto-applied notification (if any)
    if auto_applied_updates:
        logger.info(f"\n📢 Notifying about {len(auto_applied_updates)} auto-applied updates...")
        auto_apply_payload = _build_auto_apply_notification(auto_applied_updates)
        try:
            notify_team("auto-applied", auto_apply_payload)
            notifications_sent += 1
            logger.info(f"   ✓ Notified auto-applied channel")
        except Exception as e:
            logger.error(f"   ✗ Failed to send auto-apply notification: {e}")

    # 2. Send sync-alerts for issues requiring attention
    issues_needing_attention = _filter_issues_needing_attention(verdicts, auto_applied_updates)

    if issues_needing_attention:
        logger.info(f"\n📢 Notifying about {len(issues_needing_attention)} issues...")
        for verdict in issues_needing_attention:
            notification_payload = _build_sync_alert_notification(verdict)
            try:
                notify_team("sync-alerts", notification_payload)
                notifications_sent += 1
                affected_teams = ", ".join(verdict.notified_teams)
                logger.info(f"   ✓ Notified sync-alerts ({verdict.issue_type}: {verdict.issue_id[:20]}...)")
            except Exception as e:
                logger.error(f"   ✗ Failed to send sync-alert: {e}")

    logger.info(f"\n✓ Sent {notifications_sent} notifications")

    return {
        **state,
        "notifications_sent": notifications_sent,
        "current_node": "notification_complete"
    }


def _filter_issues_needing_attention(verdicts, auto_applied_updates):
    """
    Filter verdicts to only those requiring manual attention.

    Skip opportunities that were auto-applied (already handled).
    """
    auto_applied_ids = {u["entity_id"] for u in auto_applied_updates}

    needing_attention = []
    for verdict in verdicts:
        # Always include conflicts
        if verdict.issue_type == "CONFLICT":
            needing_attention.append(verdict)
        # Only include opportunities if NOT auto-applied
        elif verdict.issue_type == "OPPORTUNITY":
            # Check if this opportunity was auto-applied
            # (We can infer this from confidence < threshold OR entity not in auto_applied list)
            if verdict.confidence < 0.85:  # Below auto-apply threshold
                needing_attention.append(verdict)

    return needing_attention


def _build_auto_apply_notification(updates) -> dict:
    """Build notification for auto-applied updates"""
    return {
        "type": "AUTO_APPLIED_RESOLUTIONS",
        "count": len(updates),
        "updates": [
            {
                "entity_id": u["entity_id"],
                "entity_name": u["entity_name"],
                "status_change": f"{u['old_status']} → {u['new_status']}",
                "trigger": u["trigger"],
                "confidence": u["confidence"],
                "reasoning": u["reasoning"][:200]  # Truncate for readability
            }
            for u in updates
        ],
        "message": f"🤖 System auto-applied {len(updates)} high-confidence resolution(s)",
        "action_required": False
    }


def _build_sync_alert_notification(verdict: dict) -> dict:
    """
    Build unified sync-alert notification for any issue type.

    Works for both CONFLICT and OPPORTUNITY issues.
    """
    if verdict.issue_type == "CONFLICT":
        return {
            "type": "CONFLICT_ALERT",
            "issue_id": verdict.issue_id,
            "verdict": verdict.verdict,
            "severity": "CRITICAL",
            "reasoning": verdict.reasoning,
            "confidence": verdict.confidence,
            "evidence_count": len(verdict.evidence),
            "recommended_action": verdict.recommended_action,
            "notified_teams": verdict.notified_teams,
            "message": f"🚨 Conflict detected: {verdict.recommended_action}",
            "action_required": True
        }
    elif verdict.issue_type == "OPPORTUNITY":
        return {
            "type": "RESOLUTION_OPPORTUNITY",
            "issue_id": verdict.issue_id,
            "verdict": verdict.verdict,
            "severity": "INFO",
            "reasoning": verdict.reasoning,
            "confidence": verdict.confidence,
            "evidence_count": len(verdict.evidence),
            "recommended_action": verdict.recommended_action,
            "suggested_status": verdict.suggested_status.value if verdict.suggested_status else "ON_TRACK",
            "notified_teams": verdict.notified_teams,
            "message": f"🟢 Resolution opportunity (confidence: {verdict.confidence:.0%}): {verdict.recommended_action}",
            "action_required": True  # Manual review required
        }
    else:
        # Generic fallback
        return {
            "type": "UNKNOWN_ISSUE",
            "issue_id": verdict.issue_id,
            "verdict": verdict.verdict,
            "reasoning": verdict.reasoning,
            "recommended_action": verdict.recommended_action,
            "message": f"Issue detected: {verdict.recommended_action}",
            "action_required": True
        }
