"""Grouper Node - Group messages by channel into conversation threads"""

import logging
from collections import defaultdict

from app.graph.state import BatchGraphState
from app.models import ConversationThread

logger = logging.getLogger(__name__)


def grouper_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 2: Channel Grouper

    Groups SIGNAL messages by channel into conversation threads.

    Args:
        state: Current graph state with signal_messages

    Returns:
        Updated state with conversation_threads
    """
    logger.info("📊 GROUPER: Grouping messages by channel...")

    if not state["signal_messages"]:
        logger.info("   No SIGNAL messages to group")
        return {
            **state,
            "conversation_threads": {},
            "current_node": "grouper_complete"
        }

    # Group by channel
    grouped = defaultdict(list)
    for msg in state["signal_messages"]:
        grouped[msg.channel].append(msg)

    # Create conversation threads
    threads = {}
    for channel, msgs in grouped.items():
        # Sort chronologically
        sorted_msgs = sorted(msgs, key=lambda m: m.timestamp)

        thread = ConversationThread(
            channel=channel,
            messages=sorted_msgs,
            window_start=sorted_msgs[0].timestamp,
            window_end=sorted_msgs[-1].timestamp
        )

        threads[channel] = thread

        logger.info(
            f"   #{channel}: {len(msgs)} messages "
            f"({thread.duration_minutes}min)"
        )

    logger.info(f"   ✓ Created {len(threads)} conversation threads")

    return {
        **state,
        "conversation_threads": threads,
        "current_node": "grouper_complete"
    }
