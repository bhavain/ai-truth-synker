"""Arbiter Node - Deterministic conflict detection"""

import logging

from app.graph.state import BatchGraphState
from app.agents.arbiter import check_conflicts_for_updates

logger = logging.getLogger(__name__)


def arbiter_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 4: The Arbiter (Conflict Detection)

    Performs deterministic Python logic to detect dependency conflicts
    across all extracted updates.

    Args:
        state: Current graph state with extracted_updates

    Returns:
        Updated state with conflicts list
    """
    logger.info("=" * 70)
    logger.info("⚖️  ARBITER: Checking for dependency conflicts...")
    logger.info("=" * 70)

    extracted_updates = state.get("extracted_updates", [])

    if not extracted_updates:
        logger.info("   No updates to check for conflicts")
        return {
            **state,
            "conflicts": [],
            "current_node": "arbiter_complete"
        }

    # Check for conflicts
    conflicts = check_conflicts_for_updates(extracted_updates)

    if not conflicts:
        logger.info("   ✓ No conflicts detected")
    else:
        logger.info(f"   ⚠️  {len(conflicts)} conflict(s) detected")
        logger.info("   Escalating to Judge...")

    return {
        **state,
        "conflicts": conflicts,
        "current_node": "arbiter_complete"
    }
