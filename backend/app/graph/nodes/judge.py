"""Judge Node V2 - Unified Issue Deliberation with Auto-Apply"""

import logging
from typing import List, Dict, Any

from app.graph.state import BatchGraphState
from app.agents.judge_agent import JudgeAgent
from app.models import DependencyIssue, JudgeVerdict, EvidenceReference, EntityStatus
from app.db import get_dolt_client

logger = logging.getLogger(__name__)

# Confidence threshold for auto-apply
AUTO_APPLY_THRESHOLD = 0.85


def judge_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 5: The Judge (Unified Deliberation + Auto-Apply)

    Analyzes all issues (conflicts and opportunities):
    1. Gathers evidence from vector DB and Dolt history
    2. Issues verdicts with confidence scores
    3. Auto-applies high-confidence resolutions (>=0.85)
    4. Commits auto-applied updates to Dolt

    Args:
        state: Current graph state with issues

    Returns:
        Updated state with verdicts and auto_applied_updates
    """
    logger.info("=" * 70)
    logger.info("⚖️  JUDGE: Deliberating on all issues...")
    logger.info("=" * 70)

    issues = state.get("issues", [])

    if not issues:
        logger.info("   No issues to deliberate")
        return {
            **state,
            "verdicts": [],
            "auto_applied_updates": [],
            "current_node": "judge_complete"
        }

    # Create Judge agent
    judge = JudgeAgent()

    verdicts = []
    auto_applied_updates = []

    # Group issues by type for logging
    conflicts = [i for i in issues if i.issue_type == "CONFLICT"]
    opportunities = [i for i in issues if i.issue_type == "OPPORTUNITY"]

    logger.info(f"   Issues: {len(conflicts)} conflicts, {len(opportunities)} opportunities")

    # Process each issue
    for issue in issues:
        logger.info(f"\n--- Analyzing {issue.issue_type}: {issue.issue_id} ---")

        # Deliberate based on issue type
        if issue.issue_type == "CONFLICT":
            verdict = _deliberate_conflict(judge, issue)
        elif issue.issue_type == "OPPORTUNITY":
            verdict = _deliberate_opportunity(judge, issue)
        else:
            logger.warning(f"   Unknown issue type: {issue.issue_type}")
            continue

        if verdict:
            verdicts.append(verdict)
            logger.info(f"   ✓ Verdict: {verdict.verdict} (confidence: {verdict.confidence:.2f})")

            # Auto-apply high-confidence verdicts
            if verdict.confidence >= AUTO_APPLY_THRESHOLD:
                if issue.issue_type == "OPPORTUNITY":
                    # Unblock dependent entity
                    applied = _auto_apply_resolution(issue, verdict)
                    if applied:
                        auto_applied_updates.append(applied)
                        logger.info(f"   🤖 Auto-applied OPPORTUNITY: {applied['entity_id']} → {applied['new_status']}")

                elif issue.issue_type == "CONFLICT":
                    # Block dependent entity
                    applied = _auto_apply_conflict(issue, verdict)
                    if applied:
                        auto_applied_updates.append(applied)
                        logger.info(f"   🤖 Auto-applied CONFLICT: {applied['entity_id']} → {applied['new_status']}")

    # Batch commit all auto-applied updates
    if auto_applied_updates:
        commit_hash = _commit_auto_applied_updates(auto_applied_updates)
        logger.info(f"\n✓ Auto-applied {len(auto_applied_updates)} updates → commit {commit_hash[:8] if commit_hash else 'N/A'}")

    logger.info(f"\n✓ Judge deliberated on {len(issues)} issues")
    logger.info(f"   Verdicts issued: {len(verdicts)}")
    logger.info(f"   Auto-applied: {len(auto_applied_updates)}")

    return {
        **state,
        "verdicts": verdicts,
        "auto_applied_updates": auto_applied_updates,
        "current_node": "judge_complete"
    }


def _deliberate_conflict(judge: JudgeAgent, issue: DependencyIssue) -> JudgeVerdict:
    """
    Deliberate on a CONFLICT issue using Judge's ReAct agent.

    Judge agent now directly accepts DependencyIssue objects.
    """
    # Use Judge deliberation (has evidence gathering built-in)
    verdict = judge.deliberate(issue)

    return verdict


def _deliberate_opportunity(judge: JudgeAgent, issue: DependencyIssue) -> JudgeVerdict:
    """
    Deliberate on an OPPORTUNITY issue using Judge's tools.

    Judge agent now directly accepts DependencyIssue objects and handles evidence gathering.
    """
    # Use Judge deliberation (has evidence gathering built-in)
    verdict = judge.deliberate(issue)

    return verdict


def _auto_apply_resolution(issue: DependencyIssue, verdict: JudgeVerdict) -> Dict[str, Any]:
    """
    Auto-apply a resolution (OPPORTUNITY) by unblocking entity.

    Does NOT commit yet - will be batched later.

    Returns update record for tracking.
    """
    try:
        suggested_status = verdict.suggested_status or EntityStatus.ON_TRACK
        entity_id = issue.affected_entity.id

        logger.info(f"   🤖 Auto-applying: {entity_id} → {suggested_status.value}")

        # Build update record (will be committed in batch)
        update_record = {
            "entity_id": entity_id,
            "entity_name": issue.affected_entity.name,
            "old_status": issue.affected_entity.status.value,
            "new_status": suggested_status.value,
            "trigger": f"{issue.trigger_entity.id} resolved",
            "confidence": verdict.confidence,
            "reasoning": verdict.reasoning
        }

        return update_record

    except Exception as e:
        logger.error(f"   ✗ Auto-apply failed for {issue.affected_entity.id}: {e}")
        return None


def _auto_apply_conflict(issue: DependencyIssue, verdict: JudgeVerdict) -> Dict[str, Any]:
    """
    Auto-apply a conflict by blocking the affected entity.

    Does NOT commit yet - will be batched later.

    Returns update record for tracking.
    """
    try:
        entity_id = issue.affected_entity.id
        new_status = EntityStatus.BLOCKED

        logger.info(f"   🤖 Auto-blocking: {entity_id} → {new_status.value}")

        # Build update record (will be committed in batch)
        update_record = {
            "entity_id": entity_id,
            "entity_name": issue.affected_entity.name,
            "old_status": issue.affected_entity.status.value,
            "new_status": new_status.value,
            "trigger": f"{issue.trigger_entity.id} conflict",
            "confidence": verdict.confidence,
            "reasoning": verdict.reasoning
        }

        return update_record

    except Exception as e:
        logger.error(f"   ✗ Auto-apply conflict failed for {issue.affected_entity.id}: {e}")
        return None


def _commit_auto_applied_updates(updates: List[Dict[str, Any]]) -> str:
    """
    Batch commit all auto-applied updates to Dolt.

    Creates single commit with all resolutions.
    """
    if not updates:
        return None

    dolt = get_dolt_client()

    try:
        with dolt.get_connection() as conn:
            cursor = conn.cursor()

            updated_count = 0

            # Apply all updates
            for update in updates:
                cursor.execute(
                    """
                    UPDATE project_entities
                    SET status = %s
                    WHERE id = %s
                    """,
                    (update["new_status"], update["entity_id"])
                )

                if cursor.rowcount > 0:
                    updated_count += 1
                    logger.info(f"      ✓ Updated {update['entity_id']}: {update['old_status']} → {update['new_status']}")

            if updated_count == 0:
                logger.info("   No actual changes to commit")
                return None

            # Build commit message
            commit_msg = f"""System Reconciliation: Auto-applied {updated_count} resolutions

Updates:
{chr(10).join(f"- {u['entity_id']}: {u['old_status']} → {u['new_status']} (confidence: {u['confidence']:.2f})" for u in updates)}

Trigger: Dependency resolution analysis
Confidence threshold: >= {AUTO_APPLY_THRESHOLD}
"""

            # Add and commit
            cursor.execute("CALL DOLT_ADD('.')")
            cursor.execute("CALL DOLT_COMMIT('-m', %s)", (commit_msg,))

            # Get commit hash
            cursor.execute("SELECT HASHOF('HEAD')")
            result = cursor.fetchone()
            commit_hash = result["HASHOF('HEAD')"] if result else None

            logger.info(f"   ✓ Batched commit: {updated_count} updates → {commit_hash[:8] if commit_hash else 'N/A'}")
            return commit_hash

    except Exception as e:
        logger.error(f"   ✗ Batch commit failed: {e}")
        # Fall back to notification-only
        return None
