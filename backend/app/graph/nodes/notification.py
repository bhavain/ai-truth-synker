"""Notification Node - Send notifications for Judge verdicts"""

import logging

from app.graph.state import BatchGraphState
from app.utils import notify_team

logger = logging.getLogger(__name__)


def notification_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 6: Notification

    Sends structured notifications to teams based on Judge verdicts.

    Args:
        state: Current graph state with verdicts

    Returns:
        Updated state with notifications_sent count
    """
    logger.info("=" * 70)
    logger.info("📢 NOTIFICATION: Sending team alerts...")
    logger.info("=" * 70)

    verdicts = state.get("verdicts", [])

    if not verdicts:
        logger.info("   No verdicts to notify")
        return {
            **state,
            "notifications_sent": 0,
            "current_node": "notification_complete"
        }

    # Get corresponding conflicts for full context
    conflicts = state.get("conflicts", [])
    conflict_map = {c.conflict_id: c for c in conflicts}

    notifications_sent = 0

    for verdict in verdicts:
        conflict = conflict_map.get(verdict.conflict_id)
        if not conflict:
            logger.warning(f"   ⚠️  No conflict found for verdict {verdict.conflict_id}")
            continue

        # Create detailed notification payload
        notification_payload = {
            "type": "CONFLICT_VERDICT",
            "conflict_id": verdict.conflict_id,
            "verdict": verdict.verdict,
            "parent_entity": {
                "id": conflict.parent_entity.id,
                "name": conflict.parent_entity.name,
                "type": conflict.parent_entity.entity_type.value,
                "status": conflict.parent_entity.status.value,
                "milestone_date": str(conflict.parent_entity.milestone_date),
                "owner_team": conflict.parent_entity.owner_team
            },
            "child_entity": {
                "id": conflict.child_entity.id,
                "name": conflict.child_entity.name,
                "type": conflict.child_entity.entity_type.value,
                "status": conflict.child_entity.status.value,
                "milestone_date": str(conflict.child_entity.milestone_date),
                "owner_team": conflict.child_entity.owner_team
            },
            "dependency": {
                "type": conflict.dependency.dependency_type.value,
                "confidence": conflict.dependency.confidence
            },
            "reasoning": verdict.reasoning,
            "confidence": verdict.confidence,
            "evidence_count": len(verdict.evidence),
            "evidence": [
                {
                    "thread_id": ev.thread_id,
                    "relevance": ev.relevance,
                    "summary": ev.summary
                }
                for ev in verdict.evidence
            ],
            "recommended_action": verdict.recommended_action,
            "timestamp": verdict.decided_at.isoformat()
        }

        # Send single general notification (not team-specific)
        # Use a general "conflicts" channel or file
        try:
            notify_team("conflicts", notification_payload)  # General conflict channel
            notifications_sent += 1
            affected_teams = ", ".join(verdict.notified_teams)
            logger.info(f"   ✓ Notified conflicts channel (affects: {affected_teams})")
        except Exception as e:
            logger.error(f"   ✗ Failed to send notification: {e}")

    logger.info(f"\n✓ Sent {notifications_sent} notifications")

    return {
        **state,
        "notifications_sent": notifications_sent,
        "current_node": "notification_complete"
    }
