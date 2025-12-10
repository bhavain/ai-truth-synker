"""Judge Agent - Conflict Adjudication with Evidence Provenance"""

import json
import logging
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain.schema import SystemMessage, HumanMessage

from app.config import get_settings
from app.db import get_vector_store
from app.models import GraphState, JudgeVerdict, EvidenceReference
from app.agents.prompts import JUDGE_SYSTEM_PROMPT, format_judge_prompt
from app.utils import notify_team

logger = logging.getLogger(__name__)


def judge_node(state: GraphState) -> GraphState:
    """
    Node 4: The Judge (Adjudication)

    Uses GPT-4o to analyze conflicts, review evidence, and issue verdicts.
    Includes full evidence provenance and reasoning.

    Args:
        state: Current graph state with conflict_alert

    Returns:
        Updated state with judge_verdict
    """
    logger.info("⚖️  JUDGE: Analyzing conflict with full evidence review...")

    if not state.conflict_alert:
        logger.error("Judge called without conflict_alert!")
        return state

    settings = get_settings()
    vector_store = get_vector_store()
    conflict = state.conflict_alert

    # Step 1: Gather evidence threads
    logger.info("   Step 1: Gathering evidence from Slack threads...")

    evidence_threads = []

    # Primary evidence: the message that triggered the conflict
    primary_thread = {
        "thread_id": state.slack_message.thread_id,
        "channel": state.slack_message.channel,
        "text": state.slack_message.text,
        "timestamp": state.slack_message.timestamp.isoformat()
    }
    evidence_threads.append(primary_thread)

    # Query for related context from both teams
    parent_channel = conflict.parent_entity.owner_team.replace("-", "_")
    child_channel = conflict.child_entity.owner_team.replace("-", "_")

    for channel in [parent_channel, child_channel]:
        contexts = vector_store.query_context(
            channel=channel,
            query=f"{conflict.parent_entity.name} {conflict.child_entity.name}",
            n_results=2
        )

        for ctx in contexts:
            evidence_threads.append({
                "thread_id": ctx["thread_id"],
                "channel": channel,
                "text": ctx["summary"],
                "timestamp": ctx.get("timestamp", "")
            })

    logger.info(f"   Found {len(evidence_threads)} evidence threads")

    # Step 2: Query historical context
    logger.info("   Step 2: Querying historical context...")
    historical_context = state.historical_context or []

    # Step 3: Invoke Judge LLM
    logger.info("   Step 3: Judge deliberation (GPT-4o)...")

    llm = ChatOpenAI(
        model=settings.judge_model,
        temperature=0.3,  # Slight creativity for reasoning
        openai_api_key=settings.openai_api_key,
        model_kwargs={"response_format": {"type": "json_object"}}
    )

    # Format prompt
    user_prompt = format_judge_prompt(
        conflict_id=conflict.conflict_id,
        logic_rule_violated=conflict.logic_rule_violated,
        parent_entity={
            "id": conflict.parent_entity.id,
            "name": conflict.parent_entity.name,
            "entity_type": conflict.parent_entity.entity_type.value,
            "status": conflict.parent_entity.status.value,
            "milestone_date": str(conflict.parent_entity.milestone_date),
            "owner_team": conflict.parent_entity.owner_team
        },
        child_entity={
            "id": conflict.child_entity.id,
            "name": conflict.child_entity.name,
            "entity_type": conflict.child_entity.entity_type.value,
            "status": conflict.child_entity.status.value,
            "milestone_date": str(conflict.child_entity.milestone_date),
            "owner_team": conflict.child_entity.owner_team
        },
        dependency={
            "dependency_type": conflict.dependency.dependency_type.value,
            "confidence": conflict.dependency.confidence
        },
        evidence_threads=evidence_threads,
        historical_context=historical_context
    )

    messages = [
        SystemMessage(content=JUDGE_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt)
    ]

    try:
        response = llm.invoke(messages)
        verdict_data = json.loads(response.content)

        # Parse evidence references
        evidence_refs = [
            EvidenceReference(**ev) for ev in verdict_data.get("evidence", [])
        ]

        verdict = JudgeVerdict(
            conflict_id=conflict.conflict_id,
            verdict=verdict_data["verdict"],
            reasoning=verdict_data["reasoning"],
            evidence=evidence_refs,
            recommended_action=verdict_data["recommended_action"],
            logic_rule_violated=conflict.logic_rule_violated,
            confidence=verdict_data.get("confidence", 1.0),
            decided_at=datetime.now(),
            notified_teams=[
                conflict.parent_entity.owner_team,
                conflict.child_entity.owner_team
            ]
        )

        state.judge_verdict = verdict
        state.current_node = "judge_complete"

        logger.info(f"   ⚖️  VERDICT: {verdict.verdict}")
        logger.info(f"   Reasoning: {verdict.reasoning}")
        logger.info(f"   Confidence: {verdict.confidence:.2f}")

        # Step 4: Notify teams
        logger.info("   Step 4: Notifying teams...")

        notification_data = {
            "type": "CRITICAL_CONFLICT",
            "conflict_id": verdict.conflict_id,
            "summary": f"{conflict.parent_entity.name} depends on {conflict.child_entity.name}",
            "verdict": verdict.verdict,
            "reasoning": verdict.reasoning,
            "recommended_action": verdict.recommended_action,
            "evidence_count": len(verdict.evidence),
            "confidence": verdict.confidence
        }

        # Notify both teams
        for team in verdict.notified_teams:
            notify_team(team, notification_data)

        logger.info("   ✓ Notifications sent")

    except Exception as e:
        logger.error(f"Judge deliberation failed: {e}")
        state.error = f"Judge error: {str(e)}"

    return state
