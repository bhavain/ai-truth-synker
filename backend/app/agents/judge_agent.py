"""Judge Agent - ReAct agent for dependency issue analysis with evidence gathering"""

import json
import logging
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import InMemorySaver

from app.config import get_settings
from app.db import get_vector_store, get_dolt_client
from app.models import DependencyIssue, JudgeVerdict, EvidenceReference

logger = logging.getLogger(__name__)

# Global singleton checkpointer for HITL persistence
_global_checkpointer = InMemorySaver()


class JudgeAgent:
    """
    ReAct agent for dependency issue analysis with evidence gathering.

    Handles both CONFLICT and OPPORTUNITY issues using tools to gather evidence.
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
            """Search historical conversations across all relevant channels for context about entities. Input: search query (e.g., 'HB900_DRIVER delay'). Returns: List of relevant past conversation summaries with timestamps and channels."""
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
        def check_dependencies(entity_id: str) -> str:
            """Check all dependencies for an entity to see if other blockers exist. Input: entity_id (e.g., 'Q3_TEST'). Returns: List of all dependencies and their current status."""
            tool_usage_log.append(f"check_dependencies({entity_id})")

            try:
                # Get entity
                entity = dolt.get_entity(entity_id)
                if not entity:
                    return json.dumps({
                        "found": False,
                        "error": f"Entity {entity_id} not found"
                    })

                # Get all dependencies (what this entity depends on)
                dependencies = dolt.get_dependencies_for_entity(entity_id)

                if dependencies:
                    dep_info = []
                    for dep in dependencies:
                        # Get child entity details
                        child = dolt.get_entity(dep.child_id)
                        dep_info.append({
                            "child_id": dep.child_id,
                            "child_status": child.status.value if child else "UNKNOWN",
                            "child_milestone": str(child.milestone_date) if child else "UNKNOWN",
                            "dependency_type": dep.dependency_type.value,
                            "is_blocker": child.status.value in ["DELAYED", "BLOCKED", "AT_RISK"] if child else False
                        })

                    return json.dumps({
                        "found": True,
                        "entity_id": entity_id,
                        "total_dependencies": len(dep_info),
                        "dependencies": dep_info,
                        "active_blockers": sum(1 for d in dep_info if d["is_blocker"])
                    }, indent=2)
                else:
                    return json.dumps({
                        "found": True,
                        "entity_id": entity_id,
                        "total_dependencies": 0,
                        "dependencies": [],
                        "active_blockers": 0
                    })

            except Exception as e:
                logger.warning(f"Tool check_dependencies failed: {e}")
                return json.dumps({
                    "found": False,
                    "error": str(e)
                })

        @tool
        def validate_dates(input_str: str) -> str:
            """Validate date relationships between entities. Input: parent_id,child_id (e.g., 'Q3_TEST,HB900_DRIVER'). Returns: Date comparison and conflict validation."""
            tool_usage_log.append(f"validate_dates({input_str})")

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
                date_compatible = parent.milestone_date >= child.milestone_date

                return json.dumps({
                    "parent_id": parent_id,
                    "parent_date": str(parent.milestone_date),
                    "child_id": child_id,
                    "child_date": str(child.milestone_date),
                    "date_compatible": date_compatible,
                    "days_difference": (parent.milestone_date - child.milestone_date).days,
                    "conflict": not date_compatible,
                    "reasoning": f"Parent scheduled {parent.milestone_date}, child scheduled {child.milestone_date}. {'OK' if date_compatible else 'CONFLICT: Parent before child!'}"
                }, indent=2)

            except Exception as e:
                logger.warning(f"Tool validate_dates failed: {e}")
                return json.dumps({
                    "valid": False,
                    "error": str(e)
                })

        @tool
        def calculate_suggested_date(entity_id: str, child_date_str: str) -> str:
            """Calculate a smart suggested date for an entity based on child date and entity metadata. Input: entity_id,child_date (e.g., 'Q3_INTEGRATION_TEST,2023-11-07'). Returns: Suggested date with buffer calculation reasoning."""
            tool_usage_log.append(f"calculate_suggested_date({entity_id},{child_date_str[:10]})")

            try:
                from datetime import datetime, timedelta

                # Parse child date
                child_date = datetime.strptime(child_date_str.strip(), "%Y-%m-%d").date()

                # Get entity
                entity = dolt.get_entity(entity_id)
                if not entity:
                    return json.dumps({
                        "success": False,
                        "error": f"Entity {entity_id} not found"
                    })

                # Calculate buffer based on entity type and metadata
                buffer_days = 0
                reasoning_parts = []

                # Check for prep_time_days in metadata
                prep_time = entity.metadata.get("prep_time_days", 0) if entity.metadata else 0
                if prep_time > 0:
                    buffer_days += prep_time
                    reasoning_parts.append(f"{prep_time} days prep time from metadata")

                # Add entity type-based buffer
                entity_type_buffers = {
                    "TEST": 3,  # Tests need setup time
                    "MILESTONE": 5,  # Milestones need review/approval time
                    "REQUIREMENT": 7,  # Requirements need certification time
                    "PART": 1,  # Parts just need receiving/inspection
                }
                type_buffer = entity_type_buffers.get(entity.entity_type.value, 2)
                buffer_days += type_buffer
                reasoning_parts.append(f"{type_buffer} days buffer for {entity.entity_type.value}")

                # Calculate suggested date
                suggested_date = child_date + timedelta(days=buffer_days)

                # Check if current date already has sufficient buffer
                current_buffer = (entity.milestone_date - child_date).days if entity.milestone_date > child_date else 0
                needs_update = suggested_date > entity.milestone_date

                return json.dumps({
                    "success": True,
                    "entity_id": entity_id,
                    "entity_type": entity.entity_type.value,
                    "child_date": str(child_date),
                    "current_date": str(entity.milestone_date),
                    "current_buffer_days": current_buffer,
                    "calculated_buffer_days": buffer_days,
                    "suggested_date": str(suggested_date),
                    "needs_update": needs_update,
                    "reasoning": f"Child date: {child_date}. Buffer calculation: {' + '.join(reasoning_parts)} = {buffer_days} days total. Suggested: {suggested_date}",
                    "buffer_breakdown": {
                        "prep_time": prep_time,
                        "entity_type_buffer": type_buffer,
                        "total": buffer_days
                    }
                }, indent=2)

            except Exception as e:
                logger.warning(f"Tool calculate_suggested_date failed: {e}")
                return json.dumps({
                    "success": False,
                    "error": str(e)
                })

        @tool
        def get_dependency_tree(entity_id: str) -> str:
            """Get complete dependency tree (upstream and downstream) for an entity. Input: entity_id (e.g., 'Q3_INTEGRATION_TEST'). Returns: Full tree showing what this entity depends on (children) and what depends on it (parents), recursively up to root milestones."""
            tool_usage_log.append(f"get_dependency_tree({entity_id})")

            try:
                def get_upstream(eid, visited=None, level=0):
                    """Recursively get all parents (things that depend on this entity)"""
                    if visited is None:
                        visited = set()
                    if eid in visited or level > 10:  # Prevent cycles and infinite loops
                        return []
                    visited.add(eid)

                    with dolt.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT p.*, d.dependency_type, d.confidence
                            FROM project_entities p
                            JOIN dependencies d ON d.parent_id = p.id
                            WHERE d.child_id = %s
                        """, (eid,))

                        parents = []
                        for row in cursor.fetchall():
                            parent_info = {
                                "entity_id": row["id"],
                                "name": row["name"],
                                "entity_type": row["entity_type"],
                                "status": row["status"],
                                "milestone_date": str(row["milestone_date"]),
                                "owner_team": row["owner_team"],
                                "dependency_type": row["dependency_type"],
                                "level": level
                            }
                            parents.append(parent_info)

                            # Recurse to get grandparents
                            parents.extend(get_upstream(row["id"], visited, level + 1))

                        return parents

                def get_downstream(eid, visited=None, level=0):
                    """Recursively get all children (things this entity depends on)"""
                    if visited is None:
                        visited = set()
                    if eid in visited or level > 10:
                        return []
                    visited.add(eid)

                    with dolt.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            SELECT p.*, d.dependency_type, d.confidence
                            FROM project_entities p
                            JOIN dependencies d ON d.child_id = p.id
                            WHERE d.parent_id = %s
                        """, (eid,))

                        children = []
                        for row in cursor.fetchall():
                            child_info = {
                                "entity_id": row["id"],
                                "name": row["name"],
                                "entity_type": row["entity_type"],
                                "status": row["status"],
                                "milestone_date": str(row["milestone_date"]),
                                "owner_team": row["owner_team"],
                                "dependency_type": row["dependency_type"],
                                "level": level
                            }
                            children.append(child_info)

                            # Recurse to get descendants
                            children.extend(get_downstream(row["id"], visited, level + 1))

                        return children

                # Get the entity itself
                entity = dolt.get_entity(entity_id)
                if not entity:
                    return json.dumps({
                        "found": False,
                        "error": f"Entity {entity_id} not found"
                    })

                # Get complete tree
                upstream = get_upstream(entity_id)
                downstream = get_downstream(entity_id)

                return json.dumps({
                    "found": True,
                    "entity": {
                        "entity_id": entity.id,
                        "name": entity.name,
                        "status": entity.status.value,
                        "milestone_date": str(entity.milestone_date),
                        "owner_team": entity.owner_team
                    },
                    "upstream_dependencies": {
                        "count": len(upstream),
                        "entities": upstream
                    },
                    "downstream_dependencies": {
                        "count": len(downstream),
                        "entities": downstream
                    }
                }, indent=2)

            except Exception as e:
                logger.warning(f"Tool get_dependency_tree failed: {e}")
                return json.dumps({
                    "found": False,
                    "error": str(e)
                })

        @tool
        def apply_entity_updates(verdict_json: str) -> str:
            """Apply entity updates from a Judge verdict to the database. Input: JSON string of the complete JudgeVerdict. This tool will be interrupted for human approval, then apply updates to project_entities after approval."""
            tool_usage_log.append(f"apply_entity_updates(verdict_json)")

            try:
                # Parse the verdict
                verdict_dict = json.loads(verdict_json)

                # Apply all entity updates from the verdict to the database
                with dolt.get_connection() as conn:
                    cursor = conn.cursor()

                    updated_count = 0

                    for update in verdict_dict.get("entity_updates", []):
                        entity_id = update["entity_id"]

                        # Build update query dynamically based on what changed
                        update_fields = []
                        update_values = []

                        if update.get("new_status"):
                            update_fields.append("status = %s")
                            update_values.append(update["new_status"])

                        if update.get("new_date"):
                            update_fields.append("milestone_date = %s")
                            update_values.append(update["new_date"])

                        if not update_fields:
                            continue  # No changes for this entity

                        # Add entity_id for WHERE clause
                        update_values.append(entity_id)

                        query = f"""
                            UPDATE project_entities
                            SET {', '.join(update_fields)}
                            WHERE id = %s
                        """

                        cursor.execute(query, tuple(update_values))

                        if cursor.rowcount > 0:
                            updated_count += 1
                            changes = []
                            if update.get("new_status"):
                                changes.append(f"status → {update['new_status']}")
                            if update.get("new_date"):
                                changes.append(f"date → {update['new_date']}")
                            logger.info(f"      ✓ Updated {entity_id}: {', '.join(changes)}")

                    if updated_count == 0:
                        logger.warning("   No actual changes to commit")
                        return json.dumps({
                            "success": False,
                            "message": "No entities were updated"
                        })

                    # Build commit message (handle missing issue_id gracefully)
                    issue_id = verdict_dict.get('issue_id', 'unknown')
                    verdict_type = verdict_dict.get('verdict', 'UNKNOWN')
                    confidence = verdict_dict.get('confidence', 0)

                    commit_msg = f"""Judge Verdict Applied: {issue_id}

Verdict: {verdict_type}
Confidence: {confidence:.2f}

Updates applied: {updated_count} entities
"""

                    # Commit to Dolt
                    cursor.execute("CALL DOLT_ADD('.')")
                    cursor.execute("CALL DOLT_COMMIT('-m', %s)", (commit_msg,))

                logger.info(f"   ✓ Applied {updated_count} entity updates to database")

                return json.dumps({
                    "success": True,
                    "entity_updates_applied": updated_count,
                    "message": f"Successfully updated {updated_count} entities in database"
                })

            except Exception as e:
                logger.error(f"   ✗ Failed to apply entity updates: {e}")
                import traceback
                logger.error(f"   Traceback: {traceback.format_exc()}")
                return json.dumps({
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc()
                })

        return [query_vector_store, check_entity_history, check_dependencies, validate_dates, calculate_suggested_date, get_dependency_tree, apply_entity_updates]

    def _create_agent(self):
        """Create agent with tools using new LangChain API"""

        llm = ChatOpenAI(
            model=self.settings.judge_model,
            temperature=0.3,
            openai_api_key=self.settings.openai_api_key,
        )

        # Build system prompt for Judge
        system_prompt = """You are the Judge - an expert dependency analyst for hardware project dependencies.

Your role is to analyze dependency issues (conflicts and resolution opportunities) and determine:
1. Is this issue real or a false positive?
2. What is the severity and impact?
3. What evidence supports this conclusion?
4. What action should be taken?
5. What date and status changes should be suggested?

Use the available tools to:
- Search historical conversations for context
- Check entity update history in the database
- Verify dependency relationships
- Check for other blockers
- Validate date relationships
- Calculate smart date suggestions using calculate_suggested_date tool

IMPORTANT: Your final answer must be a JSON object with this structure:
{
    "verdict": "CRITICAL_CONFLICT" | "RESOLUTION_RECOMMENDED" | "NEEDS_MANUAL_REVIEW" | "FALSE_POSITIVE",
    "reasoning": "detailed explanation of your decision",
    "confidence": 0.0-1.0,
    "evidence": [
        {
            "thread_id": "slack://channel/timestamp (or tool:source for non-Slack evidence)",
            "relevance": "primary" | "supporting" | "contradictory",
            "summary": "what this evidence shows and why it matters"
        }
    ],
    "recommended_action": "what teams should do",
    "entity_updates": [
        {
            "entity_id": "ENTITY_ID",
            "entity_name": "ENTITY_NAME",
            "current_status": "CURRENT_STATUS",
            "new_status": "BLOCKED" | "ON_TRACK" | "AT_RISK" | "DELAYED" | null,  // null if no status change needed
            "current_date": "YYYY-MM-DD",
            "new_date": "YYYY-MM-DD" | null,  // null if no date change needed
            "reasoning": "why this entity needs these updates",
            "cascade_level": 0  // 0=direct entity, 1=first level cascade, 2+=further levels
        }
    ]
}

CRITICAL: Use get_dependency_tree tool to analyze COMPLETE cascade impact. For every issue:
1. Get the full dependency tree for the affected entity
2. Analyze ALL upstream dependencies (entities that depend on this one)
3. For each upstream entity, determine if it should be updated based on:
   - Date compatibility
   - Current status
   - Dependency type
4. Use calculate_suggested_date for entities needing date changes
5. Include ALL affected entities in entity_updates array with current_status, current_date
6. Set appropriate cascade_level for each update

DATE SUGGESTION REQUIREMENTS:
- For CONFLICTS: When a child delays, use calculate_suggested_date to compute smart new dates
  * Tool considers prep_time_days, entity type buffers, and other factors
  * Suggest dates for entities with date conflicts (parent < child)
- For OPPORTUNITIES: When child recovers, suggest rolling back dates IF no other blockers exist
- ONLY populate new_status or new_date if change is needed (use null otherwise)

CONFIDENCE GUIDELINES:
- 0.85-1.0: Very high confidence, can auto-apply (for opportunities)
- 0.70-0.84: High confidence, recommend manual review
- 0.50-0.69: Medium confidence, manual review required
- <0.50: Low confidence, more investigation needed

EVIDENCE FORMAT RULES:
- thread_id: For Slack threads use "slack://channel/ts", for tools use "tool:check_dependencies", "tool:check_entity_history", etc.
- relevance: MUST be one of: "primary" (main evidence), "supporting" (additional context), "contradictory" (conflicts with other evidence)
- summary: Describe what the evidence shows and its importance to the verdict"""

        # Get tools for this agent
        tools = self._get_tools()

        # Create agent using new API with HITL middleware
        agent = create_agent(
            model=llm,
            tools=tools,
            middleware=[
                HumanInTheLoopMiddleware(
                    interrupt_on={
                        "apply_entity_updates": True  # Interrupt for human approval (approve/reject/edit allowed)
                    },
                    description_prefix="Entity updates pending approval"
                )
            ],
            checkpointer=_global_checkpointer,  # Use global singleton for persistence
            system_prompt=system_prompt
        )

        return agent

    def deliberate(self, issue: DependencyIssue, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Analyze dependency issue and issue verdict with HITL support.

        Agent autonomously:
        1. Gathers evidence from vector store
        2. Checks entity history in Dolt
        3. Validates dependencies and dates
        4. Issues verdict with reasoning
        5. Calls apply_entity_updates tool → HITL interrupt may occur

        Args:
            issue: DependencyIssue to analyze
            config: LangGraph config with thread_id for HITL (required for interrupts)

        Returns:
            Dict with 'verdict' or '__interrupt__' key if approval needed
        """
        logger.info(f"⚖️  JUDGE: Analyzing {issue.issue_type} {issue.issue_id}...")

        # Reset tool usage log
        self.tool_usage_log = []

        # Create analysis task based on issue type
        if issue.issue_type == "CONFLICT":
            task = self._build_conflict_task(issue)
        elif issue.issue_type == "OPPORTUNITY":
            task = self._build_opportunity_task(issue)
        else:
            logger.warning(f"Unknown issue type: {issue.issue_type}")
            return None

        try:
            # Let agent work autonomously using new API
            # Pass config for HITL interrupt handling
            result = self.agent.invoke(
                {"messages": [{"role": "user", "content": task}]},
                config=config  # Pass through for HITL
            )

            logger.info(f"   Tools used: {', '.join(self.tool_usage_log)}")

            # Check if interrupted (HITL needed)
            if "__interrupt__" in result:
                logger.info(f"   ⏸️  HITL INTERRUPT: Approval required")
                return result  # Return interrupt info

            # Parse agent output
            verdict = self._parse_agent_output(result, issue)

            if verdict:
                logger.info(f"   ⚖️  VERDICT: {verdict.verdict}")
                logger.info(f"   Confidence: {verdict.confidence:.2f}")

            return {"verdict": verdict}

        except Exception as e:
            logger.error(f"   ✗ Judge deliberation failed: {e}")
            return None

    def _build_conflict_task(self, issue: DependencyIssue) -> str:
        """Build task prompt for CONFLICT analysis with cascade"""
        return f"""
Analyze this CONFLICT and its CASCADE IMPACT:

ISSUE:
- ID: {issue.issue_id}
- Type: CONFLICT (blocking issue)
- Reason: {issue.reason}
- Severity: {issue.severity.value}

TRIGGER ENTITY (changed):
- ID: {issue.trigger_entity.id}
- Name: {issue.trigger_entity.name}
- Type: {issue.trigger_entity.entity_type.value}
- Status: {issue.trigger_entity.status.value}
- Milestone Date: {issue.trigger_entity.milestone_date}
- Owner Team: {issue.trigger_entity.owner_team}

AFFECTED ENTITY (directly impacted):
- ID: {issue.affected_entity.id}
- Name: {issue.affected_entity.name}
- Type: {issue.affected_entity.entity_type.value}
- Status: {issue.affected_entity.status.value}
- Milestone Date: {issue.affected_entity.milestone_date}
- Owner Team: {issue.affected_entity.owner_team}

DEPENDENCY:
- Type: {issue.dependency.dependency_type.value}
- Confidence: {issue.dependency.confidence}

TASK - CASCADE ANALYSIS:
You must analyze the COMPLETE impact of this conflict:

1. Use get_dependency_tree({issue.affected_entity.id}) to get ALL upstream dependencies
2. For EACH entity in the upstream tree, determine:
   - Should it be BLOCKED? (if it depends on a blocked entity)
   - Should it be AT_RISK? (if dates are tight but might work)
   - Can it stay ON_TRACK? (if there's sufficient buffer)
3. Use validate_dates for each upstream entity to check date compatibility
4. Use query_vector_store to check for any additional context about entities

OUTPUT REQUIREMENTS:
- Include {issue.affected_entity.id} as cascade_level=0 (direct impact)
- Include ALL upstream entities that need status changes
- Set cascade_level based on distance from direct entity (1, 2, 3...)
- Be comprehensive - a manager needs to see the full blast radius

CRITICAL FINAL STEP:
After creating your verdict in JSON format, you MUST call the apply_entity_updates tool
with the complete verdict JSON as a string parameter. This will submit the verdict for
human approval before any changes are applied to the database.

Example: apply_entity_updates(verdict_json='{{...complete verdict JSON...}}')
"""

    def _build_opportunity_task(self, issue: DependencyIssue) -> str:
        """Build task prompt for OPPORTUNITY analysis with cascade"""
        return f"""
Analyze this OPPORTUNITY and its CASCADE IMPACT:

ISSUE:
- ID: {issue.issue_id}
- Type: OPPORTUNITY (potential resolution)
- Reason: {issue.reason}

TRIGGER ENTITY (resolved blocker):
- ID: {issue.trigger_entity.id}
- Name: {issue.trigger_entity.name}
- Status: {issue.trigger_entity.status.value}
- Milestone Date: {issue.trigger_entity.milestone_date}
- Owner Team: {issue.trigger_entity.owner_team}

AFFECTED ENTITY (currently BLOCKED):
- ID: {issue.affected_entity.id}
- Name: {issue.affected_entity.name}
- Status: {issue.affected_entity.status.value}
- Milestone Date: {issue.affected_entity.milestone_date}
- Owner Team: {issue.affected_entity.owner_team}

DEPENDENCY:
- Type: {issue.dependency.dependency_type.value}
- {issue.affected_entity.id} depends on {issue.trigger_entity.id}

TASK - CASCADE ANALYSIS:
You must analyze the COMPLETE impact of unblocking this entity:

1. check_dependencies({issue.affected_entity.id}): Are there OTHER blockers?
2. validate_dates for {issue.affected_entity.id}
3. Use get_dependency_tree({issue.affected_entity.id}) to get ALL upstream dependencies
4. For EACH entity in the upstream tree, determine:
   - Can it be UNBLOCKED? (if this was its last blocker)
   - Should it move to ON_TRACK? (if all dependencies resolved)
   - Does it need to stay AT_RISK or BLOCKED? (if other blockers exist)
5. query_vector_store to check for context about entities

OUTPUT REQUIREMENTS:
- Include {issue.affected_entity.id} as cascade_level=0 (direct unblock)
- Include ALL upstream entities that can now proceed
- Set cascade_level based on distance from direct entity (1, 2, 3...)
- Be comprehensive - show full positive cascade

CRITICAL FINAL STEP:
After creating your verdict in JSON format, you MUST call the apply_entity_updates tool
with the complete verdict JSON as a string parameter. This will submit the verdict for
human approval before any changes are applied to the database.

Example: apply_entity_updates(verdict_json='{{...complete verdict JSON...}}')
"""

    def _parse_agent_output(self, result: dict, issue: DependencyIssue) -> Optional[JudgeVerdict]:
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
                    relevance=ev.get("relevance", "supporting"),
                    summary=ev.get("summary", ev.get("content", "No summary provided"))
                )
                for ev in parsed.get("evidence", [])
            ]

            # Parse entity_updates
            from app.models import EntityUpdate, EntityStatus
            from datetime import datetime as dt
            entity_updates = []
            for update in parsed.get("entity_updates", []):
                # Parse dates if present
                current_date = None
                new_date = None
                if "current_date" in update:
                    try:
                        current_date = dt.strptime(update["current_date"], "%Y-%m-%d").date()
                    except:
                        pass

                if update.get("new_date"):
                    try:
                        new_date = dt.strptime(update["new_date"], "%Y-%m-%d").date()
                    except:
                        pass

                # Parse statuses
                current_status = EntityStatus(update["current_status"])
                new_status = EntityStatus(update["new_status"]) if update.get("new_status") else None

                entity_updates.append(EntityUpdate(
                    entity_id=update["entity_id"],
                    entity_name=update["entity_name"],
                    current_status=current_status,
                    new_status=new_status,
                    current_date=current_date,
                    new_date=new_date,
                    reasoning=update.get("reasoning", ""),
                    cascade_level=update.get("cascade_level", 0)
                ))

            verdict = JudgeVerdict(
                issue_id=issue.issue_id,
                issue_type=issue.issue_type,
                verdict=parsed["verdict"],
                reasoning=parsed["reasoning"],
                evidence=evidence_refs,
                recommended_action=parsed["recommended_action"],
                confidence=float(parsed.get("confidence", 0.5)),
                entity_updates=entity_updates,
                decided_at=datetime.now(),
                notified_teams=[
                    issue.affected_entity.owner_team,
                    issue.trigger_entity.owner_team
                ]
            )

            return verdict

        except Exception as e:
            logger.error(f"   ✗ Failed to parse Judge output: {e}")
            logger.debug(f"   Raw output: {result.get('output', 'N/A')}")
            return None


def get_global_checkpointer():
    """Get the global checkpointer instance for API endpoints to resume workflows"""
    return _global_checkpointer
