"""Bouncer Node - Batch message classification"""

import json
import logging
from typing import List

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from app.config import get_settings
from app.graph.state import BatchGraphState
from app.models import MessageClass, SlackMessage

logger = logging.getLogger(__name__)


def bouncer_node(state: BatchGraphState) -> BatchGraphState:
    """
    Node 1: Batch Bouncer

    Classifies all messages in a single LLM call as SIGNAL or NOISE.
    50x more efficient than individual message classification.

    Args:
        state: Current graph state with batch_messages

    Returns:
        Updated state with classifications, signal_messages, noise_count
    """
    logger.info("=" * 70)
    logger.info(f"🚪 BOUNCER: Classifying {len(state['batch_messages'])} messages...")
    logger.info("=" * 70)

    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.bouncer_model,
        temperature=0,
        openai_api_key=settings.openai_api_key,
        model_kwargs={"response_format": {"type": "json_object"}}
    )

    # Prepare batch prompt
    messages_data = [
        {
            "id": msg.ts,
            "channel": msg.channel,
            "text": msg.text
        }
        for msg in state["batch_messages"]
    ]

    prompt = f"""
You are a message classifier for a hardware engineering team.

Classify each message as either SIGNAL (project-related) or NOISE (casual conversation).

SIGNAL examples:
- Parts/components status updates
- Test schedules and results
- Delays, blockers, dependencies
- Vendor communications
- Milestone changes

NOISE examples:
- Lunch plans
- Sports talk
- Weekend plans
- General chit-chat
- Personal conversations

MESSAGES TO CLASSIFY:
{json.dumps(messages_data, indent=2)}

Return JSON mapping message ID to classification:
{{
    "message_id_1": "SIGNAL",
    "message_id_2": "NOISE",
    ...
}}

Be conservative: When in doubt, mark as SIGNAL.
"""

    try:
        response = llm.invoke([
            SystemMessage(content="You are an expert message classifier."),
            HumanMessage(content=prompt)
        ])

        classifications_raw = json.loads(response.content)

        # Convert to MessageClass enum
        classifications = {}
        for msg in state["batch_messages"]:
            classification = classifications_raw.get(msg.ts, "NOISE")
            classifications[msg.ts] = (
                MessageClass.SIGNAL if classification == "SIGNAL" else MessageClass.NOISE
            )

        # Filter SIGNAL messages
        signal_messages = [
            msg for msg in state["batch_messages"]
            if classifications.get(msg.ts) == MessageClass.SIGNAL
        ]

        noise_count = len(state["batch_messages"]) - len(signal_messages)

        logger.info(f"   ✓ Results: {len(signal_messages)} SIGNAL, {noise_count} NOISE")

        return {
            **state,
            "classifications": classifications,
            "signal_messages": signal_messages,
            "noise_count": noise_count,
            "current_node": "bouncer_complete"
        }

    except Exception as e:
        logger.error(f"   ✗ Batch classification failed: {e}")
        # Fail open: treat all as SIGNAL
        classifications = {msg.ts: MessageClass.SIGNAL for msg in state["batch_messages"]}

        return {
            **state,
            "classifications": classifications,
            "signal_messages": state["batch_messages"],
            "noise_count": 0,
            "current_node": "bouncer_complete",
            "errors": state.get("errors", []) + [f"Bouncer failed: {str(e)}"]
        }
