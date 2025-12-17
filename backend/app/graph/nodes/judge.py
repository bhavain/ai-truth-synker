"""Judge Node V3 - Unified Issue Deliberation with Human-in-the-Loop Approval"""

import logging
import json
import uuid
from typing import List, Dict, Any
from datetime import datetime

from app.graph.state import BatchGraphState
from app.agents.judge_agent import JudgeAgent
from app.models import DependencyIssue, JudgeVerdict, EvidenceReference, EntityStatus
from app.db import get_dolt_client

logger = logging.getLogger(__name__)


def judge_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 5: The Judge (Unified Deliberation + Pending Approval Creation)

    Analyzes all issues (conflicts and opportunities):
    1. Gathers evidence from vector DB and Dolt history
    2. Issues verdicts with confidence scores and date/status suggestions
    3. Creates pending approvals for human review
    4. Saves verdicts to Dolt pending_approvals table

    Args:
        state: Current graph state with issues

    Returns:
        Updated state with verdicts and pending_approvals
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
            "pending_approvals": [],
            "current_node": "judge_complete"
        }

    # Create Judge agent
    judge = JudgeAgent()

    verdicts = []
    pending_approvals = []
    interrupts = []

    # Group issues by type for logging
    conflicts = [i for i in issues if i.issue_type == "CONFLICT"]
    opportunities = [i for i in issues if i.issue_type == "OPPORTUNITY"]

    logger.info(f"   Issues: {len(conflicts)} conflicts, {len(opportunities)} opportunities")

    # Process each issue
    for issue in issues:
        logger.info(f"\n--- Analyzing {issue.issue_type}: {issue.issue_id} ---")

        # Generate approval_id for this issue (will be used as thread_id)
        approval_id = f"approval_{uuid.uuid4().hex[:12]}"
        config = {"configurable": {"thread_id": approval_id}}

        # Deliberate with HITL support
        result = judge.deliberate(issue, config=config)

        if not result:
            logger.warning(f"   ⚠️  No result from Judge for {issue.issue_id}")
            continue

        # Check if interrupted (HITL needed)
        if "__interrupt__" in result:
            logger.info(f"   ⏸️  HITL INTERRUPT: Workflow paused, approval required")
            logger.info(f"   📝 Approval ID: {approval_id}")

            # Extract the verdict JSON from the interrupt data
            # Interrupt data is a list of Interrupt objects
            interrupt_list = result.get("__interrupt__", [])
            logger.info(f"   📝 Interrupt data type: {type(interrupt_list)}")

            verdict_json = None

            # Handle interrupt data (can be list of Interrupt objects or dict)
            if isinstance(interrupt_list, list) and len(interrupt_list) > 0:
                # Get the first interrupt
                interrupt_obj = interrupt_list[0]

                # Access the value attribute of the Interrupt object
                if hasattr(interrupt_obj, 'value'):
                    interrupt_value = interrupt_obj.value
                else:
                    interrupt_value = interrupt_obj

                # Extract action_requests from the interrupt value
                action_requests = interrupt_value.get("action_requests", [])

                for action_request in action_requests:
                    if action_request.get("name") == "apply_entity_updates":
                        # Get the verdict_json argument
                        verdict_json = action_request.get("args", {}).get("verdict_json")
                        logger.info(f"   📝 Extracted verdict_json (length: {len(verdict_json) if verdict_json else 0})")
                        break

            if verdict_json:
                # Create pending approval in database
                dolt = get_dolt_client()
                dolt.create_pending_approval(approval_id, verdict_json)
                logger.info(f"   📝 Created pending approval in database: {approval_id}")
            else:
                logger.warning(f"   ⚠️  Could not extract verdict_json from interrupt")
                logger.warning(f"   Debug: interrupt_list = {interrupt_list[:200] if interrupt_list else None}...")

            # Store interrupt info
            interrupts.append({
                "approval_id": approval_id,
                "issue_id": issue.issue_id,
                "interrupt_data": str(interrupt_list)[:500]  # Convert to string and truncate for storage
            })
            pending_approvals.append(approval_id)

        else:
            # No interrupt - verdict was created but no approval needed (shouldn't happen with our setup)
            verdict = result.get("verdict")
            if verdict:
                verdicts.append(verdict)
                logger.info(f"   ✓ Verdict: {verdict.verdict} (confidence: {verdict.confidence:.2f})")

    logger.info(f"\n✓ Judge processed {len(issues)} issues")
    logger.info(f"   Verdicts issued: {len(verdicts)}")
    logger.info(f"   Pending approvals: {len(pending_approvals)}")
    if interrupts:
        logger.info(f"   ⏸️  Workflows paused, waiting for human review")

    return {
        **state,
        "verdicts": verdicts,
        "pending_approvals": pending_approvals,
        "interrupts": interrupts,
        "current_node": "judge_interrupted" if interrupts else "judge_complete"
    }


# Note: Pending approval creation now happens in judge_node() when interrupt is detected
# The apply_entity_updates tool now directly applies updates after human approval
# No separate apply_approved_verdict() function needed
