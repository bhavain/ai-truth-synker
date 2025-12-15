"""Arbiter Node - Unified Change Detection"""

import logging

from app.graph.state import BatchGraphState
from app.agents.arbiter import analyze_all_changes

logger = logging.getLogger(__name__)


def arbiter_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 4: The Arbiter (Unified Change Analysis)

    Performs deterministic analysis to detect:
    1. Conflicts (blocking issues)
    2. Opportunities (potential resolutions)
    3. Cascades (downstream impacts)

    Args:
        state: Current graph state with extracted_updates

    Returns:
        Updated state with issues list (replaces conflicts)
    """
    logger.info("=" * 70)
    logger.info("⚖️  ARBITER: Analyzing dependency changes...")
    logger.info("=" * 70)

    extracted_updates = state.get("extracted_updates", [])

    if not extracted_updates:
        logger.info("   No updates to analyze")
        return {
            **state,
            "issues": [],  # Changed from "conflicts"
            "current_node": "arbiter_complete"
        }

    # Analyze all changes (conflicts + opportunities + cascades)
    issues = analyze_all_changes(extracted_updates)

    if not issues:
        logger.info("   ✓ No issues detected")
    else:
        logger.info(f"   ⚠️  {len(issues)} issue(s) detected")
        logger.info("   Escalating to Judge...")

    return {
        **state,
        "issues": issues,  # Changed from "conflicts"
        "current_node": "arbiter_complete"
    }
