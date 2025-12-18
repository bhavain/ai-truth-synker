"""Autonomous Watcher Agent with Tool Use"""

import json
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.tools import tool

from app.config import get_settings
from app.db import get_dolt_client, get_vector_store
from app.models import ConversationThread, ExtractedUpdate, EntityStatus

logger = logging.getLogger(__name__)


class AutonomousWatcher(ABC):
    """
    Base class for autonomous conversation-aware Watchers with tool use.

    Key Features:
    - Processes entire conversation threads, not individual messages
    - Uses tools autonomously (query_db, search_history, etc.)
    - Reasons through multi-step extraction with confidence scoring
    - Channel-specific domain expertise
    """

    def __init__(self, channel: str):
        self.channel = channel
        self.settings = get_settings()
        self.dolt = get_dolt_client()
        self.vector_store = get_vector_store()

        # Track tool usage for debugging
        self.tool_usage_log: List[str] = []

        # Setup agent
        self.agent = self._create_agent()

        logger.info(f"✓ {self.__class__.__name__} initialized for #{channel}")

    @property
    @abstractmethod
    def domain_context(self) -> str:
        """Domain-specific knowledge for this channel"""
        pass

    def _get_tools(self) -> List:
        """Get tools available to the agent"""

        # Define tools with decorator approach
        dolt = self.dolt
        vector_store = self.vector_store
        channel = self.channel
        tool_usage_log = self.tool_usage_log

        @tool
        def query_entity(entity_id: str) -> str:
            """Query database for entity by ID. Input: entity_id (e.g., 'HB900_DRIVER'). Returns: JSON with entity details if found, or error if not found."""
            entity_id_clean = entity_id.strip().strip('"').strip("'").strip()
            tool_usage_log.append(f"query_entity({entity_id_clean})")

            try:
                entity = dolt.get_entity(entity_id_clean)

                if entity:
                    return json.dumps({
                        "found": True,
                        "entity_id": entity.id,
                        "name": entity.name,
                        "type": entity.entity_type.value,
                        "status": entity.status.value,
                        "milestone_date": str(entity.milestone_date),
                        "owner_team": entity.owner_team
                    }, indent=2)
                else:
                    return json.dumps({
                        "found": False,
                        "error": f"Entity '{entity_id}' not found in database"
                    })
            except Exception as e:
                logger.warning(f"Tool query_entity failed for {entity_id_clean}: {e}")
                return json.dumps({
                    "found": False,
                    "error": "Database query failed"
                })

        @tool
        def search_history(query: str) -> str:
            """Search historical conversations for relevant context. Input: query string (e.g., 'HB900 delays'). Returns: List of relevant past conversation summaries."""
            tool_usage_log.append(f"search_history('{query[:30]}...')")

            try:
                channel_normalized = channel.replace("-", "_")

                contexts = vector_store.query_context(
                    channel=channel_normalized,
                    query=query,
                    n_results=3
                )

                if contexts:
                    summaries = [ctx["summary"] for ctx in contexts]
                    return json.dumps({
                        "found": len(summaries),
                        "contexts": summaries
                    }, indent=2)
                else:
                    return json.dumps({
                        "found": 0,
                        "contexts": []
                    })
            except Exception as e:
                logger.warning(f"Tool search_history failed: {e}")
                return json.dumps({
                    "found": 0,
                    "contexts": [],
                    "error": "Search failed"
                })

        @tool
        def check_dependencies(entity_id: str) -> str:
            """Find what entities depend on a given entity. Input: entity_id (e.g., 'HB900_DRIVER'). Returns: List of dependencies."""
            entity_id_clean = entity_id.strip().strip('"').strip("'").strip()
            tool_usage_log.append(f"check_dependencies({entity_id_clean})")

            try:
                dependencies = dolt.get_dependencies_for_entity(entity_id_clean)

                if dependencies:
                    dep_list = [
                        {
                            "parent": dep.parent_id,
                            "child": dep.child_id,
                            "type": dep.dependency_type.value,
                            "confidence": dep.confidence
                        }
                        for dep in dependencies
                    ]
                    return json.dumps({
                        "found": len(dep_list),
                        "dependencies": dep_list
                    }, indent=2)
                else:
                    return json.dumps({
                        "found": 0,
                        "dependencies": []
                    })
            except Exception as e:
                logger.warning(f"Tool check_dependencies failed: {e}")
                return json.dumps({
                    "found": 0,
                    "dependencies": [],
                    "error": "Dependency check failed"
                })

        @tool
        def validate_date(date_str: str) -> str:
            """Parse and validate a date string. Input: date string (e.g., 'November 7, 2023' or '2023-11-07'). Returns: Validated ISO date or error."""
            tool_usage_log.append(f"validate_date('{date_str}')")

            try:
                from dateutil import parser as date_parser

                parsed_date = date_parser.parse(date_str)
                return json.dumps({
                    "valid": True,
                    "iso_date": parsed_date.date().isoformat(),
                    "year": parsed_date.year,
                    "month": parsed_date.month,
                    "day": parsed_date.day
                }, indent=2)
            except Exception as e:
                return json.dumps({
                    "valid": False,
                    "error": f"Could not parse date: {str(e)}"
                })

        return [query_entity, search_history, check_dependencies, validate_date]

    def _create_agent(self):
        """Create agent with tools using new LangChain API"""

        llm = ChatOpenAI(
            model=self.settings.watcher_model,
            temperature=0,
            openai_api_key=self.settings.openai_api_key,
        )

        # Build system prompt with domain context
        system_prompt = f"""You are an autonomous agent analyzing a conversation from #{self.channel}.

{self.domain_context}

Your task is to extract structured entity updates from conversations. Use the available tools to:
1. Query the database to verify entities exist
2. Search historical context for relevant information
3. Check dependencies between entities
4. Validate dates mentioned in conversations

Always return your final answer as valid JSON with the extracted entity updates."""

        # Get tools for this agent
        tools = self._get_tools()

        # Create agent using new API
        agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=system_prompt
        )

        return agent

    def process_conversation(self, thread: ConversationThread) -> List[ExtractedUpdate]:
        """
        Process entire conversation thread to extract entity updates.

        Agent autonomously:
        1. Analyzes conversation flow
        2. Uses tools to validate entities/dates
        3. Searches for historical context
        4. Extracts updates with confidence scores
        5. Provides reasoning for each extraction
        """

        logger.info(f"🤖 {self.__class__.__name__}: Processing {thread.message_count} messages ({thread.duration_minutes}min)")

        # Reset tool usage log
        self.tool_usage_log = []

        # Format conversation
        conversation_text = self._format_conversation(thread)

        # Create agent task
        task = self._create_extraction_task(conversation_text, thread)

        try:
            # Let agent work autonomously using new API
            result = self.agent.invoke(
                {"messages": [{"role": "user", "content": task}]}
            )

            logger.info(f"   Tools used: {', '.join(self.tool_usage_log)}")

            # Parse agent output
            updates = self._parse_agent_output(result, thread)

            logger.info(f"   ✓ Extracted {len(updates)} updates")

            return updates

        except Exception as e:
            logger.error(f"   ✗ Agent failed: {e}")
            return []

    def _format_conversation(self, thread: ConversationThread) -> str:
        """Format conversation as chronological transcript"""
        lines = []
        for msg in thread.messages:
            timestamp = msg.timestamp.strftime("%H:%M")
            user = msg.user[:8]  # Truncate user ID
            lines.append(f"[{timestamp}] {user}: {msg.text}")

        return "\n".join(lines)

    @abstractmethod
    def _create_extraction_task(self, conversation: str, thread: ConversationThread) -> str:
        """Create channel-specific extraction task for agent"""
        pass

    def _parse_agent_output(
        self,
        result: Dict[str, Any],
        thread: ConversationThread
    ) -> List[ExtractedUpdate]:
        """Parse agent's final output into ExtractedUpdate objects"""

        try:
            # New agent API returns messages in result
            # Get the last message content
            messages = result.get("messages", [])
            if not messages:
                logger.warning("   ⚠️  No messages in agent response")
                return []

            # Get the last AI message
            last_message = messages[-1]
            output_text = last_message.content if hasattr(last_message, 'content') else str(last_message)

            # Try to parse JSON
            if isinstance(output_text, str):
                # Extract JSON from text (agent might add extra text)
                import re

                # Try to find JSON array first, then JSON object
                # Use non-greedy matching and balance brackets
                json_match = re.search(r'\[\s*\{.*?\}\s*\]', output_text, re.DOTALL)
                if not json_match:
                    # Try single JSON object
                    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', output_text, re.DOTALL)

                if json_match:
                    output_text = json_match.group(0)
                else:
                    logger.warning(f"   ⚠️  No JSON found in output: {output_text[:200]}")
                    return []

                try:
                    parsed = json.loads(output_text)
                except json.JSONDecodeError as e:
                    logger.error(f"   ✗ JSON parse error: {e}")
                    logger.error(f"   Attempted to parse: {output_text[:500]}")
                    return []
            else:
                parsed = output_text

            # Handle both single object and array
            if isinstance(parsed, dict):
                parsed = [parsed]

            updates = []
            for extraction in parsed:
                # Skip if entity not found (agent should have checked with query_entity)
                if not extraction.get("entity_id"):
                    continue

                # Validate entity exists
                entity_check = self.dolt.get_entity(extraction["entity_id"])
                if not entity_check:
                    logger.warning(f"   ⚠️  Entity {extraction['entity_id']} not in DB, skipping")
                    continue

                # Parse status if present
                status = None
                if extraction.get("status"):
                    try:
                        status = EntityStatus(extraction["status"])
                    except ValueError:
                        logger.warning(f"   ⚠️  Invalid status: {extraction['status']}")

                # Parse date if present
                milestone_date = None
                if extraction.get("milestone_date"):
                    from dateutil import parser as date_parser
                    try:
                        milestone_date = date_parser.parse(extraction["milestone_date"]).date()
                    except:
                        logger.warning(f"   ⚠️  Invalid date: {extraction['milestone_date']}")

                # Create update
                update = ExtractedUpdate(
                    entity_id=extraction["entity_id"],
                    entity_name=extraction.get("entity_name"),
                    status=status,
                    milestone_date=milestone_date,
                    summary=extraction.get("summary", ""),
                    confidence=float(extraction.get("confidence", 0.5)),
                    supporting_messages=extraction.get("supporting_messages", []),
                    reasoning=extraction.get("reasoning", ""),
                    tools_used=self.tool_usage_log.copy()
                )

                updates.append(update)

            return updates

        except Exception as e:
            logger.error(f"   ✗ Failed to parse agent output: {e}")
            logger.debug(f"   Raw output: {result.get('output', 'N/A')}")
            return []
