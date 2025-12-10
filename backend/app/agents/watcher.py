"""Watcher Agent - Entity Extraction and State Update"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional
from dateutil import parser as date_parser

from langchain_openai import ChatOpenAI
from langchain.schema import SystemMessage, HumanMessage

from app.config import get_settings
from app.db import get_dolt_client, get_vector_store
from app.models import GraphState, ExtractedData, SQLAction, EntityStatus
from app.agents.prompts import WATCHER_SYSTEM_PROMPT, format_watcher_prompt

logger = logging.getLogger(__name__)


def watcher_node(state: GraphState) -> GraphState:
    """Watcher Agent - extracts entities and updates database"""
    logger.info(f"👁️  WATCHER ({state.slack_message.channel}): Extracting entities...")

    settings = get_settings()
    dolt = get_dolt_client()
    vector_store = get_vector_store()

    slack_msg = state.slack_message
    channel = slack_msg.channel.replace("-", "_")  # Normalize channel name

    # Step 1: RAG - Query historical context
    logger.info("   Step 1: Querying historical context...")
    historical_contexts = vector_store.query_context(
        channel=channel,
        query=slack_msg.text,
        n_results=3
    )

    context_summaries = [ctx["summary"] for ctx in historical_contexts]
    state.historical_context = context_summaries

    logger.info(f"   Found {len(context_summaries)} relevant historical contexts")

    # Step 2: Extract entities using LLM
    logger.info("   Step 2: Extracting structured data...")

    llm = ChatOpenAI(
        model=settings.watcher_model,
        temperature=0,
        openai_api_key=settings.openai_api_key,
        model_kwargs={"response_format": {"type": "json_object"}}
    )

    user_prompt = format_watcher_prompt(
        channel=slack_msg.channel,
        timestamp=slack_msg.timestamp.isoformat(),
        text=slack_msg.text,
        historical_context=context_summaries
    )

    messages = [
        SystemMessage(content=WATCHER_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt)
    ]

    try:
        response = llm.invoke(messages)
        extraction_result = json.loads(response.content)

        # Check if extraction found anything
        if extraction_result is None or not extraction_result.get("entity_id"):
            logger.info("   No project entities detected in message")
            state.current_node = "watcher_complete"
            return state

        # Parse extraction
        extracted = ExtractedData(
            entity_id=extraction_result.get("entity_id"),
            entity_name=extraction_result.get("entity_name"),
            status=extraction_result.get("status"),
            milestone_date=extraction_result.get("milestone_date"),
            summary=extraction_result.get("summary", slack_msg.text[:200]),
            confidence=extraction_result.get("confidence", 1.0),
            source_thread_id=slack_msg.thread_id
        )

        state.extracted_data = extracted
        logger.info(f"   ✓ Extracted: {extracted.entity_id} ({extracted.status})")

        # Step 3: Store conversation summary in vector DB
        logger.info("   Step 3: Storing context in vector DB...")
        vector_store.add_context(
            channel=channel,
            summary=extracted.summary,
            thread_id=slack_msg.thread_id,
            metadata={
                "entity_id": extracted.entity_id,
                "status": extracted.status.value if extracted.status else "UNKNOWN"
            }
        )

        # Step 4: Generate SQL action
        logger.info("   Step 4: Generating SQL update...")

        # Check if entity exists
        existing_entity = dolt.get_entity(extracted.entity_id)

        if existing_entity:
            # UPDATE existing entity
            updates = {}
            if extracted.status:
                updates["status"] = extracted.status.value
            if extracted.milestone_date:
                updates["milestone_date"] = extracted.milestone_date

            sql_action = SQLAction(
                action="UPDATE",
                table="project_entities",
                entity_id=extracted.entity_id,
                updates=updates,
                source_thread_id=slack_msg.thread_id,
                commit_message=f"{slack_msg.channel}: {extracted.summary[:100]}"
            )

            logger.info(f"   SQL Action: UPDATE {extracted.entity_id}")

        else:
            # INSERT new entity (would need more fields, skip for MVP)
            logger.warning(f"   Entity {extracted.entity_id} not found in database, skipping INSERT")
            state.current_node = "watcher_complete"
            return state

        state.sql_action = sql_action

        # Step 5: Execute SQL and commit to Dolt
        logger.info("   Step 5: Committing to Dolt...")

        commit_hash = dolt.update_entity(
            entity_id=sql_action.entity_id,
            updates=sql_action.updates,
            commit_message=sql_action.commit_message,
            source_thread_id=sql_action.source_thread_id
        )

        logger.info(f"   ✓ Dolt commit: {commit_hash[:8] if commit_hash else 'N/A'}")

        state.current_node = "watcher_complete"

    except Exception as e:
        logger.error(f"Watcher extraction failed: {e}")
        state.error = f"Watcher error: {str(e)}"

    return state
