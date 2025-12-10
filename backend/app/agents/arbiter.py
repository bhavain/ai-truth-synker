"""Arbiter Agent - Deterministic Conflict Detection"""

import logging
from datetime import datetime

from app.models import GraphState, ConflictAlert, ConflictSeverity
from app.db import get_dolt_client

logger = logging.getLogger(__name__)


def arbiter_node(state: GraphState) -> GraphState:
    """
    Node 3: The Arbiter (Logic Check)

    Performs deterministic Python logic to detect dependency conflicts.
    Checks if entity updates violate critical blocker constraints.

    Args:
        state: Current graph state with sql_action applied

    Returns:
        Updated state with conflict_detected and conflict_alert
    """
    logger.info("⚖️  ARBITER: Checking for dependency conflicts...")

    dolt = get_dolt_client()

    # Check if there was an SQL action
    if not state.sql_action:
        logger.info("   No SQL action to check, skipping")
        state.current_node = "arbiter_complete"
        return state

    entity_id = state.sql_action.entity_id

    # Query for conflicts
    conflicts = dolt.check_dependency_conflict(entity_id)

    if not conflicts:
        logger.info("   ✓ No conflicts detected")
        state.conflict_detected = False
        state.current_node = "arbiter_complete"
        return state

    # Conflict found!
    logger.warning(f"   ⚠️  CONFLICT DETECTED: {len(conflicts)} violations found")

    # For MVP, handle the first conflict
    parent_entity, child_entity, dependency = conflicts[0]

    conflict_id = f"conflict_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Determine logic rule violated
    logic_rule = "milestone_dependency_date_violation"
    if parent_entity.milestone_date < child_entity.milestone_date:
        logic_rule = "critical_blocker_schedule_inversion"

    conflict_alert = ConflictAlert(
        conflict_id=conflict_id,
        parent_entity=parent_entity,
        child_entity=child_entity,
        dependency=dependency,
        logic_rule_violated=logic_rule,
        severity=ConflictSeverity.CRITICAL,
        detected_at=datetime.now()
    )

    state.conflict_detected = True
    state.conflict_alert = conflict_alert
    state.current_node = "arbiter_complete"

    logger.info(f"   🚨 CRITICAL: {parent_entity.id} depends on {child_entity.id}")
    logger.info(f"   Problem: Parent scheduled {parent_entity.milestone_date}, child available {child_entity.milestone_date}")
    logger.info(f"   Escalating to Judge...")

    return state
