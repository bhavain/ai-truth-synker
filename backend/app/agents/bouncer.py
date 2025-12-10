"""Bouncer Agent - Message Classification"""

import logging
from langchain_openai import ChatOpenAI
from langchain.schema import SystemMessage, HumanMessage

from app.config import get_settings
from app.models import GraphState, MessageClass
from app.agents.prompts import BOUNCER_SYSTEM_PROMPT, format_bouncer_prompt

logger = logging.getLogger(__name__)


def bouncer_node(state: GraphState) -> GraphState:
    """
    Node 1: The Bouncer (Classifier)

    Classifies incoming Slack messages as SIGNAL (project-related) or NOISE (irrelevant).
    Uses GPT-4o-mini for cost efficiency.

    Args:
        state: Current graph state with slack_message

    Returns:
        Updated state with message_class set
    """
    logger.info("🚪 BOUNCER: Classifying message...")

    settings = get_settings()
    slack_msg = state.slack_message

    # Initialize LLM
    llm = ChatOpenAI(
        model=settings.bouncer_model,
        temperature=0,
        openai_api_key=settings.openai_api_key,
    )

    # Build prompt
    user_prompt = format_bouncer_prompt(
        channel=slack_msg.channel,
        text=slack_msg.text
    )

    messages = [
        SystemMessage(content=BOUNCER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt)
    ]

    # Classify
    try:
        response = llm.invoke(messages)
        classification = response.content.strip().upper()

        # Validate response
        if classification not in ["SIGNAL", "NOISE"]:
            logger.warning(f"Unexpected classification: {classification}, defaulting to NOISE")
            classification = "NOISE"

        state.message_class = MessageClass(classification)
        state.current_node = "bouncer_complete"

        logger.info(f"   Classification: {classification}")

        if classification == "NOISE":
            logger.info("   ❌ Dropping message (NOISE)")
        else:
            logger.info("   ✓ Passing to Watcher (SIGNAL)")

    except Exception as e:
        logger.error(f"Bouncer classification failed: {e}")
        state.error = f"Bouncer error: {str(e)}"
        state.message_class = MessageClass.NOISE  # Fail safe

    return state
