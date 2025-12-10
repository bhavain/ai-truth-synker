"""LangGraph workflow definition and execution"""

import logging
from typing import Literal

from langgraph.graph import StateGraph, END
from langsmith import traceable

from app.models import GraphState, MessageClass, SlackMessage
from app.agents import bouncer_node, watcher_node, arbiter_node, judge_node

logger = logging.getLogger(__name__)


def should_continue_after_bouncer(state: GraphState) -> Literal["watcher", "__end__"]:
    """
    Routing logic after Bouncer node.
    If NOISE, end workflow. If SIGNAL, go to Watcher.
    """
    if state.message_class == MessageClass.NOISE:
        return END
    return "watcher"


def should_continue_after_arbiter(state: GraphState) -> Literal["judge", "__end__"]:
    """
    Routing logic after Arbiter node.
    If conflict detected, escalate to Judge. Otherwise, end.
    """
    if state.conflict_detected:
        return "judge"
    return END


def create_workflow() -> StateGraph:
    """
    Create the Truth Engine LangGraph workflow.

    Workflow:
    1. Bouncer (classify) -> if SIGNAL, continue; if NOISE, end
    2. Watcher (extract & update) -> always continue
    3. Arbiter (check conflicts) -> if conflict, continue; else end
    4. Judge (adjudicate) -> end

    Returns:
        Compiled StateGraph
    """
    logger.info("Building LangGraph workflow...")

    # Initialize graph
    workflow = StateGraph(GraphState)

    # Add nodes
    workflow.add_node("bouncer", bouncer_node)
    workflow.add_node("watcher", watcher_node)
    workflow.add_node("arbiter", arbiter_node)
    workflow.add_node("judge", judge_node)

    # Set entry point
    workflow.set_entry_point("bouncer")

    # Add conditional edges
    workflow.add_conditional_edges(
        "bouncer",
        should_continue_after_bouncer,
        {
            "watcher": "watcher",
            END: END
        }
    )

    # Watcher always proceeds to Arbiter
    workflow.add_edge("watcher", "arbiter")

    # Conditional edge from Arbiter
    workflow.add_conditional_edges(
        "arbiter",
        should_continue_after_arbiter,
        {
            "judge": "judge",
            END: END
        }
    )

    # Judge is terminal
    workflow.add_edge("judge", END)

    # Compile
    app = workflow.compile()

    logger.info("✓ Workflow compiled successfully")
    return app


@traceable(name="TruthEngineWorkflow")
def run_workflow(slack_message: SlackMessage) -> GraphState:
    """
    Execute the Truth Engine workflow for a Slack message.

    Args:
        slack_message: Incoming Slack message

    Returns:
        Final graph state with all processing results
    """
    logger.info("=" * 60)
    logger.info(f"PROCESSING MESSAGE: {slack_message.channel} / {slack_message.ts}")
    logger.info("=" * 60)

    # Create workflow
    app = create_workflow()

    # Initialize state
    initial_state = GraphState(slack_message=slack_message)

    # Execute workflow
    try:
        final_state = app.invoke(initial_state)

        logger.info("=" * 60)
        logger.info("WORKFLOW COMPLETE")
        logger.info(f"  Final Node: {final_state.get('current_node', 'unknown')}")
        logger.info(f"  Classification: {final_state.get('message_class', 'N/A')}")
        logger.info(f"  Conflict Detected: {final_state.get('conflict_detected', False)}")
        if final_state.get("judge_verdict"):
            logger.info(f"  Judge Verdict: {final_state['judge_verdict'].verdict}")
        logger.info("=" * 60)

        return final_state

    except Exception as e:
        logger.error(f"Workflow execution failed: {e}")
        raise
