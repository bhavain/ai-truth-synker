"""LangGraph Multi-Agent Batch Processing Workflow"""

import logging
from typing import Literal
from datetime import datetime

from langgraph.graph import StateGraph, END, START
from langgraph.types import Send

from app.graph.state import BatchGraphState, WatcherSubgraphState
from app.graph.nodes import (
    bouncer_node,
    grouper_node,
    arbiter_node,
    judge_node,
    notification_node,
)
from app.graph.nodes.watcher import watcher_subgraph_node, aggregate_watcher_results
from app.models import BatchSlackMessages

logger = logging.getLogger(__name__)


def create_watcher_subgraph() -> StateGraph:
    """
    Create Watcher subgraph for parallel execution.

    Each channel gets its own subgraph instance that runs in parallel.
    """
    subgraph = StateGraph(WatcherSubgraphState)

    # Single node in subgraph: process conversation with ReAct agent
    subgraph.add_node("process", watcher_subgraph_node)

    # Linear flow: START -> process -> END
    subgraph.add_edge(START, "process")
    subgraph.add_edge("process", END)

    return subgraph.compile()


def dispatch_watchers(state: BatchGraphState) -> list[Send]:
    """
    Dispatcher function to create parallel watcher tasks.

    Creates a Send command for each conversation thread,
    enabling LangGraph to process all channels in parallel.
    """
    conversation_threads = state.get("conversation_threads", {})

    if not conversation_threads:
        return []

    # Create Send command for each channel
    sends = []
    for channel, thread in conversation_threads.items():
        sends.append(
            Send(
                "watcher_subgraph",
                {
                    "channel": channel,
                    "thread": thread,
                    "extracted_updates": [],
                    "tools_used": [],
                    "commit_hash": None,
                    "error": None
                }
            )
        )

    logger.info(f"   Dispatching {len(sends)} parallel watcher subgraphs")
    return sends


def should_invoke_judge(state: BatchGraphState) -> Literal["judge", "end"]:
    """
    Conditional edge: only invoke Judge if conflicts were detected.
    """
    conflicts = state.get("conflicts", [])

    if conflicts:
        logger.info(f"   → Routing to Judge ({len(conflicts)} conflicts)")
        return "judge"
    else:
        logger.info("   → No conflicts, skipping Judge")
        return "end"


def create_batch_workflow() -> StateGraph:
    """
    Create the complete batch processing LangGraph workflow.

    Flow:
    1. Bouncer: Classify SIGNAL/NOISE
    2. Grouper: Group by channel
    3. Watcher Dispatcher → Parallel Watcher Subgraphs → Aggregator
    4. Arbiter: Check conflicts
    5. Judge: Adjudicate (if conflicts)
    6. Notification: Send alerts
    """
    logger.info("Building LangGraph batch workflow...")

    workflow = StateGraph(BatchGraphState)

    # Add nodes
    workflow.add_node("bouncer", bouncer_node)
    workflow.add_node("grouper", grouper_node)
    workflow.add_node("watcher_subgraph", create_watcher_subgraph())
    workflow.add_node("aggregate", aggregate_watcher_results)
    workflow.add_node("arbiter", arbiter_node)
    workflow.add_node("judge", judge_node)
    workflow.add_node("notification", notification_node)

    # Build graph edges
    # START -> Bouncer
    workflow.add_edge(START, "bouncer")

    # Bouncer -> Grouper
    workflow.add_edge("bouncer", "grouper")

    # Grouper -> Watcher Dispatcher (parallel execution)
    workflow.add_conditional_edges(
        "grouper",
        dispatch_watchers,
        ["watcher_subgraph"]
    )

    # Watcher Subgraphs -> Aggregator
    workflow.add_edge("watcher_subgraph", "aggregate")

    # Aggregator -> Arbiter
    workflow.add_edge("aggregate", "arbiter")

    # Arbiter -> Judge (conditional) or END
    workflow.add_conditional_edges(
        "arbiter",
        should_invoke_judge,
        {
            "judge": "judge",
            "end": END
        }
    )

    # Judge -> Notification
    workflow.add_edge("judge", "notification")

    # Notification -> END
    workflow.add_edge("notification", END)

    logger.info("✓ LangGraph workflow assembled")

    return workflow.compile()


# Create singleton workflow instance
_workflow = None


def get_workflow() -> StateGraph:
    """Get or create the compiled workflow"""
    global _workflow
    if _workflow is None:
        _workflow = create_batch_workflow()
    return _workflow


async def process_batch(batch: BatchSlackMessages) -> dict:
    """
    Main entry point for batch processing.

    Args:
        batch: BatchSlackMessages with messages and window times

    Returns:
        Final state dict with results
    """
    logger.info("=" * 70)
    logger.info(f"BATCH PROCESSING START: {len(batch.messages)} messages")
    logger.info(f"Window: {batch.window_start} → {batch.window_end}")
    logger.info("=" * 70)

    # Initialize state
    initial_state: BatchGraphState = {
        "batch_messages": batch.messages,
        "window_start": batch.window_start,
        "window_end": batch.window_end,
        "classifications": {},
        "signal_messages": [],
        "noise_count": 0,
        "conversation_threads": {},
        "extracted_updates": [],
        "agent_tool_usage": {},
        "commit_hashes": {},
        "conflicts": [],
        "verdicts": [],
        "notifications_sent": 0,
        "current_node": "start",
        "errors": []
    }

    # Get workflow
    workflow = get_workflow()

    # Execute workflow
    try:
        final_state = await workflow.ainvoke(initial_state)

        logger.info("=" * 70)
        logger.info("BATCH PROCESSING COMPLETE")
        logger.info("=" * 70)
        logger.info(f"✓ Total messages: {len(batch.messages)}")
        logger.info(f"✓ SIGNAL messages: {len(final_state.get('signal_messages', []))}")
        logger.info(f"✓ NOISE messages: {final_state.get('noise_count', 0)}")
        logger.info(f"✓ Conversation threads: {len(final_state.get('conversation_threads', {}))}")
        logger.info(f"✓ Updates extracted: {len(final_state.get('extracted_updates', []))}")
        logger.info(f"✓ Conflicts detected: {len(final_state.get('conflicts', []))}")
        logger.info(f"✓ Verdicts issued: {len(final_state.get('verdicts', []))}")
        logger.info(f"✓ Notifications sent: {final_state.get('notifications_sent', 0)}")

        if final_state.get("errors"):
            logger.warning(f"⚠️  Errors encountered: {len(final_state['errors'])}")
            for error in final_state["errors"]:
                logger.warning(f"   - {error}")

        logger.info("=" * 70)

        return final_state

    except Exception as e:
        logger.error(f"✗ Batch processing failed: {e}", exc_info=True)
        raise
