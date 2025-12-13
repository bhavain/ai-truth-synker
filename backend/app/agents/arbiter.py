"""Arbiter - Deterministic Conflict Detection"""

import logging
from datetime import datetime
from typing import Union

from app.models import ConflictAlert, ConflictSeverity
from app.db import get_dolt_client

logger = logging.getLogger(__name__)


def check_conflicts_for_updates(extracted_updates):
    """
    Check for dependency conflicts across all extracted updates.
    Deduplicates conflicts to avoid processing the same conflict multiple times.

    Args:
        extracted_updates: List of ExtractedUpdate objects

    Returns:
        List of ConflictAlert objects (deduplicated)
    """
    dolt = get_dolt_client()
    all_conflicts = []
    seen_conflicts = set()  # Track (parent_id, child_id) pairs

    for update in extracted_updates:
        # Query for conflicts for this entity
        conflict_list = dolt.check_dependency_conflict(update.entity_id)

        for parent_entity, child_entity, dependency in conflict_list:
            # Create unique key for this conflict
            conflict_key = (parent_entity.id, child_entity.id)

            # Skip if already processed
            if conflict_key in seen_conflicts:
                continue

            seen_conflicts.add(conflict_key)

            conflict_id = f"conflict_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{parent_entity.id}_{child_entity.id}"

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

            all_conflicts.append(conflict_alert)

            logger.warning(f"   🚨 CONFLICT: {parent_entity.id} depends on {child_entity.id}")
            logger.warning(f"      Problem: Parent date {parent_entity.milestone_date} < Child date {child_entity.milestone_date}")

    return all_conflicts
