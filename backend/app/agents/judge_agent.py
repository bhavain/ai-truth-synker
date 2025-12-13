"""Judge Agent - ReAct agent for conflict adjudication with evidence gathering"""

import json
import logging
from typing import List, Optional
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.tools import tool

from app.config import get_settings
from app.db import get_vector_store, get_dolt_client
from app.models import ConflictAlert, JudgeVerdict, EvidenceReference

logger = logging.getLogger(__name__)


class JudgeAgent:
    """
    ReAct agent for conflict adjudication with evidence gathering.

    Similar to AutonomousWatcher but specialized for conflict analysis.
    Uses tools to gather evidence before making a verdict.
    """

    def __init__(self):
        self.settings = get_settings()
        self.vector_store = get_vector_store()
        self.dolt = get_dolt_client()

        # Track tool usage for debugging
        self.tool_usage_log: List[str] = []

        # Setup agent
        self.agent = self._create_agent()

        logger.info("✓ JudgeAgent initialized")

    def _get_tools(self) -> List:
        """Get tools available to the Judge agent"""

        # Capture self references for use in closures
        vector_store = self.vector_store
        dolt = self.dolt
        tool_usage_log = self.tool_usage_log

        @tool
        def query_vector_store(query: str) -> str:
            """Search historical conversations across all relevant channels for context about entities in conflict. Input: search query (e.g., 'HB900_DRIVER delay'). Returns: List of relevant past conversation summaries with timestamps and channels."""
            tool_usage_log.append(f"query_vector_store('{query[:30]}...')")

            try:
                # Search across all channels (supply-chain, avionics, etc.)
                all_contexts = []

                for channel in ["supply_chain", "avionics"]:
                    contexts = vector_store.query_context(
                        channel=channel,
                        query=query,
                        n_results=2
                    )
                    all_contexts.extend(contexts)

                if all_contexts:
                    summaries = [
                        {
                            "channel": ctx.get("channel", "unknown"),
                            "summary": ctx["summary"],
                            "timestamp": ctx.get("timestamp", ""),
                            "thread_id": ctx.get("thread_id", "")
                        }
                        for ctx in all_contexts
                    ]
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
                logger.warning(f"Tool query_vector_store failed: {e}")
                return json.dumps({
                    "found": 0,
                    "contexts": [],
                    "error": str(e)
                })

        @tool
        def check_entity_history(entity_id: str) -> str:
            """Query Dolt database for entity update history. Input: entity_id (e.g., 'HB900_DRIVER'). Returns: Historical status and milestone_date changes with commit info."""
            tool_usage_log.append(f"check_entity_history({entity_id})")

            try:
                # Query Dolt history
                with dolt.get_connection() as conn:
                    with conn.cursor() as cursor:
                        # Get commit log for this entity
                        cursor.execute("""
                            SELECT
                                commit_hash,
                                committer,
                                date,
                                message
                            FROM dolt_log
                            WHERE commit_hash IN (
                                SELECT DISTINCT commit_hash
                                FROM dolt_diff_project_entities
                                WHERE to_id = %s OR from_id = %s
                            )
                            ORDER BY date DESC
                            LIMIT 5
                        """, (entity_id, entity_id))

                        history = cursor.fetchall()

                        if history:
                            return json.dumps({
                                "found": len(history),
                                "history": [
                                    {
                                        "commit": h["commit_hash"][:8],
                                        "date": str(h["date"]),
                                        "message": h["message"]
                                    }
                                    for h in history
                                ]
                            }, indent=2)
                        else:
                            return json.dumps({"found": 0, "history": []})

            except Exception as e:
                logger.warning(f"Tool check_entity_history failed: {e}")
                return json.dumps({
                    "found": 0,
                    "history": [],
                    "error": "Could not retrieve history"
                })

        @tool
        def get_dependency_details(input_str: str) -> str:
            """Get full details about a dependency relationship. Input: parent_id,child_id (e.g., 'Q3_TEST,HB900_DRIVER'). Returns: Dependency type, confidence score, and metadata."""
            tool_usage_log.append(f"get_dependency_details({input_str})")

            try:
                parent_id, child_id = input_str.split(",")
                parent_id = parent_id.strip()
                child_id = child_id.strip()

                # Get dependency
                dependencies = dolt.get_dependencies_for_entity(child_id)

                for dep in dependencies:
                    if dep.parent_id == parent_id:
                        return json.dumps({
                            "found": True,
                            "parent_id": dep.parent_id,
                            "child_id": dep.child_id,
                            "type": dep.dependency_type.value,
                            "confidence": dep.confidence
                        }, indent=2)

                return json.dumps({
                    "found": False,
                    "error": f"No dependency found between {parent_id} and {child_id}"
                })

            except Exception as e:
                logger.warning(f"Tool get_dependency_details failed: {e}")
                return json.dumps({
                    "found": False,
                    "error": str(e)
                })

        @tool
        def validate_conflict(input_str: str) -> str:
            """Re-validate the conflict logic to ensure it's not an edge case. Input: parent_id,child_id (e.g., 'Q3_TEST,HB900_DRIVER'). Returns: Confirmation of conflict with detailed reasoning."""
            tool_usage_log.append(f"validate_conflict({input_str})")

            try:
                parent_id, child_id = input_str.split(",")
                parent_id = parent_id.strip()
                child_id = child_id.strip()

                # Get entities
                parent = dolt.get_entity(parent_id)
                child = dolt.get_entity(child_id)

                if not parent or not child:
                    return json.dumps({
                        "valid": False,
                        "error": "One or both entities not found"
                    })

                # Check date logic
                if parent.milestone_date < child.milestone_date:
                    return json.dumps({
                        "valid": True,
                        "conflict_type": "critical_blocker_schedule_inversion",
                        "reasoning": f"Parent '{parent.name}' scheduled for {parent.milestone_date}, but depends on child '{child.name}' available {child.milestone_date}. Parent date is BEFORE child availability."
                    }, indent=2)
                else:
                    return json.dumps({
                        "valid": False,
                        "reasoning": "No date conflict detected on re-validation"
                    })

            except Exception as e:
                logger.warning(f"Tool validate_conflict failed: {e}")
                return json.dumps({
                    "valid": False,
                    "error": str(e)
                })

        return [query_vector_store, check_entity_history, get_dependency_details, validate_conflict]

    def _create_agent(self):
        """Create agent with tools using new LangChain API"""

        llm = ChatOpenAI(
            model=self.settings.judge_model,
            temperature=0.3,
            openai_api_key=self.settings.openai_api_key,
        )

        # Build system prompt for Judge
        system_prompt = """You are the Judge - an expert conflict analyst for hardware project dependencies.

Your role is to analyze conflicts detected by the Arbiter and determine:
1. Is this a real conflict or a false positive?
2. What is the severity and impact?
3. What evidence supports this conclusion?
4. What action should be taken?

Use the available tools to:
- Search historical conversations for context
- Check entity update history in the database
- Verify dependency relationships
- Re-validate the conflict logic

IMPORTANT: Your final answer must be a JSON object with this structure:
{
    "verdict": "CRITICAL_CONFLICT" | "RESOLVED" | "FALSE_POSITIVE",
    "reasoning": "detailed explanation of your decision",
    "confidence": 0.0-1.0,
    "evidence": [
        {
            "thread_id": "slack://channel/timestamp (or tool:source for non-Slack evidence)",
            "relevance": "primary" | "supporting" | "contradictory",
            "summary": "what this evidence shows and why it matters"
        }
    ],
    "recommended_action": "what teams should do"
}

EVIDENCE FORMAT RULES:
- thread_id: For Slack threads use "slack://channel/ts", for tools use "tool:validate_conflict", "tool:entity_history", etc.
- relevance: MUST be one of: "primary" (main evidence), "supporting" (additional context), "contradictory" (conflicts with other evidence)
- summary: Describe what the evidence shows and its importance to the verdict"""

        # Get tools for this agent
        tools = self._get_tools()

        # Create agent using new API
        agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=system_prompt
        )

        return agent

    def deliberate(self, conflict: ConflictAlert) -> Optional[JudgeVerdict]:
        """
        Analyze conflict and issue verdict.

        Agent autonomously:
        1. Gathers evidence from vector store
        2. Checks entity history in Dolt
        3. Validates conflict logic
        4. Issues verdict with reasoning

        Args:
            conflict: ConflictAlert to analyze

        Returns:
            JudgeVerdict or None if deliberation fails
        """
        logger.info(f"⚖️  JUDGE: Analyzing conflict {conflict.conflict_id}...")

        # Reset tool usage log
        self.tool_usage_log = []

        # Create analysis task
        task = f"""
Analyze this dependency conflict:

CONFLICT:
- ID: {conflict.conflict_id}
- Rule Violated: {conflict.logic_rule_violated}
- Severity: {conflict.severity.value}

PARENT ENTITY (depends on child):
- ID: {conflict.parent_entity.id}
- Name: {conflict.parent_entity.name}
- Type: {conflict.parent_entity.entity_type.value}
- Status: {conflict.parent_entity.status.value}
- Milestone Date: {conflict.parent_entity.milestone_date}
- Owner Team: {conflict.parent_entity.owner_team}

CHILD ENTITY (must complete first):
- ID: {conflict.child_entity.id}
- Name: {conflict.child_entity.name}
- Type: {conflict.child_entity.entity_type.value}
- Status: {conflict.child_entity.status.value}
- Milestone Date: {conflict.child_entity.milestone_date}
- Owner Team: {conflict.child_entity.owner_team}

DEPENDENCY:
- Type: {conflict.dependency.dependency_type.value}
- Confidence: {conflict.dependency.confidence}

TASK:
Use your tools to gather evidence and determine:
1. Is this a real conflict?
2. What's the impact?
3. What should teams do?

Provide your verdict in JSON format as specified.
"""

        try:
            # Let agent work autonomously using new API
            result = self.agent.invoke(
                {"messages": [{"role": "user", "content": task}]}
            )

            logger.info(f"   Tools used: {', '.join(self.tool_usage_log)}")

            # Parse agent output
            verdict = self._parse_agent_output(result, conflict)

            if verdict:
                logger.info(f"   ⚖️  VERDICT: {verdict.verdict}")
                logger.info(f"   Confidence: {verdict.confidence:.2f}")

            return verdict

        except Exception as e:
            logger.error(f"   ✗ Judge deliberation failed: {e}")
            return None

    def _parse_agent_output(self, result: dict, conflict: ConflictAlert) -> Optional[JudgeVerdict]:
        """Parse agent's final output into JudgeVerdict"""

        try:
            # New agent API returns messages in result
            messages = result.get("messages", [])
            if not messages:
                logger.warning("   ⚠️  No messages in agent response")
                return None

            # Get the last AI message
            last_message = messages[-1]
            output_text = last_message.content if hasattr(last_message, 'content') else str(last_message)

            # Try to parse JSON
            if isinstance(output_text, str):
                # Extract JSON from text
                import re
                json_match = re.search(r'\{[\s\S]*\}', output_text)
                if json_match:
                    output_text = json_match.group(0)

                parsed = json.loads(output_text)
            else:
                parsed = output_text

            # Parse evidence references with correct schema
            evidence_refs = [
                EvidenceReference(
                    thread_id=ev.get("thread_id", "unknown"),
                    relevance=ev.get("relevance", "supporting"),  # Must be enum value
                    summary=ev.get("summary", ev.get("content", "No summary provided"))
                )
                for ev in parsed.get("evidence", [])
            ]

            verdict = JudgeVerdict(
                conflict_id=conflict.conflict_id,
                verdict=parsed["verdict"],
                reasoning=parsed["reasoning"],
                evidence=evidence_refs,
                recommended_action=parsed["recommended_action"],
                logic_rule_violated=conflict.logic_rule_violated,
                confidence=float(parsed.get("confidence", 0.5)),
                decided_at=datetime.now(),
                notified_teams=[
                    conflict.parent_entity.owner_team,
                    conflict.child_entity.owner_team
                ]
            )

            return verdict

        except Exception as e:
            logger.error(f"   ✗ Failed to parse Judge output: {e}")
            logger.debug(f"   Raw output: {result.get('output', 'N/A')}")
            return None
