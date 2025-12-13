"""State models for LangGraph batch workflow"""

import operator
from datetime import datetime
from typing import Annotated, Dict, List, Optional
from typing_extensions import TypedDict

from app.models import (
    SlackMessage,
    ConversationThread,
    ExtractedUpdate,
    ConflictAlert,
    JudgeVerdict,
    MessageClass,
)
from app.models.schemas import GraphState  # Keep old state for reference


class BatchGraphState(TypedDict):
    """
    Unified state for the entire batch processing LangGraph workflow.

    Flow:
    1. Input: batch_messages, window_start, window_end
    2. Bouncer: adds classifications, signal_messages
    3. Grouper: adds conversation_threads
    4. Watcher: adds extracted_updates, agent_tool_usage, commit_hashes
    5. Arbiter: adds conflicts
    6. Judge: adds verdicts
    7. Notification: adds notifications_sent

    Note: Fields that receive parallel updates use Annotated with operator.add
    """

    # Input
    batch_messages: List[SlackMessage]
    window_start: datetime
    window_end: datetime

    # After Bouncer
    classifications: Dict[str, MessageClass]  # ts -> SIGNAL/NOISE
    signal_messages: List[SlackMessage]
    noise_count: int

    # After Grouper
    conversation_threads: Dict[str, ConversationThread]  # channel -> thread

    # After Watcher (accumulated from parallel subgraphs)
    # Use Annotated to merge results from parallel watchers
    extracted_updates: Annotated[List[ExtractedUpdate], operator.add]
    agent_tool_usage: Dict[str, List[str]]  # channel -> tools used
    commit_hashes: Dict[str, str]  # channel -> commit hash

    # After Arbiter
    conflicts: List[ConflictAlert]

    # After Judge
    verdicts: List[JudgeVerdict]

    # After Notification
    notifications_sent: int

    # Meta
    current_node: str
    errors: List[str]


class WatcherSubgraphState(TypedDict):
    """
    State for individual Watcher subgraph (one per channel).
    Each subgraph runs in parallel.
    """

    channel: str
    thread: ConversationThread
    extracted_updates: List[ExtractedUpdate]
    tools_used: List[str]
    commit_hash: Optional[str]
    error: Optional[str]


__all__ = ["GraphState", "BatchGraphState", "WatcherSubgraphState"]
