"""Arbiter V2 - Simplified Dependency Change Detection"""

import logging
from datetime import datetime
from typing import List, Set, Tuple

from app.models import DependencyIssue, ProjectEntity, Dependency, EntityStatus, ConflictSeverity
from app.db import get_dolt_client

logger = logging.getLogger(__name__)


def analyze_all_changes(extracted_updates) -> List[DependencyIssue]:
    """
    Unified change analyzer: Detects conflicts and resolution opportunities.

    For each update:
    1. Check if it creates conflicts (date inversions)
    2. Check if it resolves blockers (opportunities for unblocking)

    Args:
        extracted_updates: List of ExtractedUpdate objects

    Returns:
        List of DependencyIssue objects
    """
    dolt = get_dolt_client()
    all_issues = []
    seen_pairs = set()  # Track (trigger_id, affected_id) to avoid duplicates

    logger.info(f"🔍 Analyzing {len(extracted_updates)} entity changes...")

    for update in extracted_updates:
        logger.info(f"   Analyzing: {update.entity_id} ({update.status})")

        # Get full entity state from DB
        trigger_entity = dolt.get_entity(update.entity_id)
        if not trigger_entity:
            logger.warning(f"   Entity {update.entity_id} not found in DB")
            continue

        # 1. Check for conflicts (this entity causing problems for parents)
        conflict_issues = _check_conflicts(trigger_entity, seen_pairs)
        all_issues.extend(conflict_issues)

        # 2. Check for opportunities (this entity resolving blockers)
        opportunity_issues = _check_opportunities(trigger_entity, seen_pairs)
        all_issues.extend(opportunity_issues)

    logger.info(f"   ✓ Detected {len(all_issues)} issues: {_count_by_type(all_issues)}")
    return all_issues


def _check_conflicts(entity: ProjectEntity, seen_pairs: Set) -> List[DependencyIssue]:
    """
    Check if this entity creates conflicts with its parents.

    Rule: Parent depends on Child (CRITICAL_BLOCKER)
          Parent.milestone_date < Child.milestone_date → CONFLICT
    """
    dolt = get_dolt_client()
    conflicts = []

    # Find all parents that depend on this entity
    conflict_list = dolt.check_dependency_conflict(entity.id)

    for parent_entity, child_entity, dependency in conflict_list:
        pair_key = (entity.id, parent_entity.id)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        issue_id = f"conflict_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{parent_entity.id}_{entity.id}"

        reason = f"Schedule inversion: {parent_entity.id} scheduled {parent_entity.milestone_date} but depends on {entity.id} @ {entity.milestone_date}"

        issue = DependencyIssue(
            issue_id=issue_id,
            issue_type="CONFLICT",
            trigger_entity=entity,
            affected_entity=parent_entity,
            dependency=dependency,
            reason=reason,
            severity=ConflictSeverity.CRITICAL,
            detected_at=datetime.now()
        )

        conflicts.append(issue)
        logger.warning(f"   🚨 CONFLICT: {parent_entity.id} ← {entity.id}")

    return conflicts


def _check_opportunities(entity: ProjectEntity, seen_pairs: Set) -> List[DependencyIssue]:
    """
    Check if this entity resolves any blockers.

    Rule: Entity status improved (e.g., DELAYED → ON_TRACK)
          Find all dependents that are BLOCKED
          Check if they can be unblocked now
    """
    dolt = get_dolt_client()
    opportunities = []

    # Only check if status is now favorable
    if entity.status not in [EntityStatus.ON_TRACK, EntityStatus.COMPLETED]:
        return opportunities

    # Find all entities that were potentially blocked by this one
    blocked_dependents = _find_blocked_dependents(entity.id)

    for dependent_entity, dependency in blocked_dependents:
        pair_key = (entity.id, dependent_entity.id)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        # Check if dates are now compatible
        if dependent_entity.milestone_date >= entity.milestone_date:
            issue_id = f"opportunity_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{dependent_entity.id}_{entity.id}"

            reason = f"{entity.id} now {entity.status.value} @ {entity.milestone_date}, {dependent_entity.id} (BLOCKED) may proceed @ {dependent_entity.milestone_date}"

            issue = DependencyIssue(
                issue_id=issue_id,
                issue_type="OPPORTUNITY",
                trigger_entity=entity,
                affected_entity=dependent_entity,
                dependency=dependency,
                reason=reason,
                severity=ConflictSeverity.INFO,
                detected_at=datetime.now()
            )

            opportunities.append(issue)
            logger.info(f"   🟢 OPPORTUNITY: {dependent_entity.id} may be unblocked by {entity.id}")

    return opportunities


def _find_blocked_dependents(entity_id: str) -> List[Tuple[ProjectEntity, Dependency]]:
    """Find all dependents that are BLOCKED and depend on this entity"""
    dolt = get_dolt_client()

    with dolt.get_connection() as conn:
        cursor = conn.cursor()

        # Find all entities that depend on this entity and are BLOCKED
        cursor.execute("""
            SELECT p.*, d.dependency_type, d.confidence
            FROM project_entities p
            JOIN dependencies d ON d.parent_id = p.id
            WHERE d.child_id = %s
              AND d.dependency_type = 'CRITICAL_BLOCKER'
              AND p.status = 'BLOCKED'
        """, (entity_id,))

        results = []
        for row in cursor.fetchall():
            entity = ProjectEntity(
                id=row["id"],
                name=row["name"],
                entity_type=row["entity_type"],
                status=row["status"],
                milestone_date=row["milestone_date"],
                owner_team=row["owner_team"],
                metadata={}
            )

            dependency = Dependency(
                parent_id=row["id"],
                child_id=entity_id,
                dependency_type=row["dependency_type"],
                confidence=row["confidence"]
            )

            results.append((entity, dependency))

        return results


def _count_by_type(issues: List[DependencyIssue]) -> str:
    """Helper to count issues by type"""
    counts = {}
    for issue in issues:
        counts[issue.issue_type] = counts.get(issue.issue_type, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in counts.items())
