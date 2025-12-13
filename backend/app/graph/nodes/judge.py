"""Judge Node - Conflict adjudication with ReAct agent"""

import logging

from app.graph.state import BatchGraphState
from app.agents.judge_agent import JudgeAgent

logger = logging.getLogger(__name__)


def judge_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 5: The Judge (Adjudication)

    Uses ReAct agent to analyze conflicts, gather evidence, and issue verdicts.

    Args:
        state: Current graph state with conflicts

    Returns:
        Updated state with verdicts
    """
    logger.info("=" * 70)
    logger.info("⚖️  JUDGE: Deliberating on conflicts...")
    logger.info("=" * 70)

    conflicts = state.get("conflicts", [])

    if not conflicts:
        logger.info("   No conflicts to adjudicate")
        return {
            **state,
            "verdicts": [],
            "current_node": "judge_complete"
        }

    # Create Judge agent
    judge = JudgeAgent()

    verdicts = []
    for conflict in conflicts:
        logger.info(f"\n--- Analyzing Conflict: {conflict.conflict_id} ---")

        # Let Judge agent deliberate
        verdict = judge.deliberate(conflict)

        if verdict:
            verdicts.append(verdict)
            logger.info(f"   ✓ Verdict issued: {verdict.verdict}")
        else:
            # Retry once
            logger.info("   ↻ Retrying deliberation...")
            verdict = judge.deliberate(conflict)

            if verdict:
                verdicts.append(verdict)
                logger.info(f"   ✓ Retry succeeded: {verdict.verdict}")
            else:
                # Failed after retry - skip notification
                logger.error(f"   ✗ Judge failed after retry for {conflict.conflict_id}")
                logger.error("   Skipping notification for this conflict")

    logger.info(f"\n✓ Judge deliberated on {len(conflicts)} conflicts")
    logger.info(f"   Verdicts issued: {len(verdicts)}")

    return {
        **state,
        "verdicts": verdicts,
        "current_node": "judge_complete"
    }
