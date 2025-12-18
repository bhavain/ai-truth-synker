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
            """Query Dolt database for entity update history with actual date/status changes. Input: entity_id (e.g., 'HB900_DRIVER'). Returns: Historical status and milestone_date changes showing WHAT changed (from → to)."""
            tool_usage_log.append(f"check_entity_history({entity_id})")

            try:
                # Query Dolt diff to see actual changes
                with dolt.get_connection() as conn:
                    with conn.cursor() as cursor:
                        # Get actual changes from dolt_diff
                        cursor.execute("""
                            SELECT
                                from_status,
                                to_status,
                                from_milestone_date,
                                to_milestone_date,
                                from_commit,
                                to_commit,
                                to_commit_date
                            FROM dolt_diff_project_entities
                            WHERE to_id = %s OR from_id = %s
                            ORDER BY to_commit_date DESC
                            LIMIT 5
                        """, (entity_id, entity_id))

                        changes = cursor.fetchall()

                        if changes:
                            history_entries = []
                            for change in changes:
                                entry = {
                                    "commit": change["to_commit"][:8] if change["to_commit"] else "unknown",
                                    "date": str(change["to_commit_date"]) if change["to_commit_date"] else "unknown"
                                }

                                # Show what changed
                                changes_list = []
                                if change["from_status"] != change["to_status"]:
                                    changes_list.append(f"status: {change['from_status']} → {change['to_status']}")
                                if change["from_milestone_date"] != change["to_milestone_date"]:
                                    changes_list.append(f"date: {change['from_milestone_date']} → {change['to_milestone_date']}")

                                entry["changes"] = ", ".join(changes_list) if changes_list else "no changes"

                                # Get commit message for context
                                cursor.execute("""
                                    SELECT message FROM dolt_log
                                    WHERE commit_hash = %s
                                """, (change["to_commit"],))
                                commit_info = cursor.fetchone()
                                if commit_info:
                                    entry["message"] = commit_info["message"][:100]  # First 100 chars

                                history_entries.append(entry)

                            return json.dumps({
                                "found": len(history_entries),
                                "entity_id": entity_id,
                                "history": history_entries,
                                "note": "Use this to understand WHY current dates exist and what were ORIGINAL dates before conflicts pushed them out"
                            }, indent=2)
                        else:
                            return json.dumps({
                                "found": 0,
                                "entity_id": entity_id,
                                "history": [],
                                "note": "No history found - entity may not have been updated since initial load"
                            })

            except Exception as e:
                logger.warning(f"Tool check_entity_history failed: {e}")
                return json.dumps({
                    "found": 0,
                    "history": [],
                    "error": f"Could not retrieve history: {str(e)}"
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

IMPORTANT WORKFLOW:
1. First, gather evidence and analyze the issue using the tools above
2. Create your verdict as a JSON object with this structure:
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
- For CONFLICTS: When a child delays, ALL upstream dates must be checked and updated if needed
  * STEP 1: Update the direct entity using calculate_suggested_date
  * STEP 2: For EACH upstream entity, compare its current date to the NEW date of its dependency
  * STEP 3: If parent.current_date < child.new_date → MUST update parent date!
  * Use calculate_suggested_date for each entity that needs updating
  * Don't just mark as AT_RISK - actually UPDATE THE DATES!
- For OPPORTUNITIES: When child recovers, suggest rolling back dates IF no other blockers exist
- IMPORTANT: "No date conflict" is NOT an excuse to skip cascade entities
  * Check date conflicts AFTER applying the direct entity's new date, not before!

CONFIDENCE GUIDELINES:
- 0.85-1.0: Very high confidence, can auto-apply (for opportunities)
- 0.70-0.84: High confidence, recommend manual review
- 0.50-0.69: Medium confidence, manual review required
- <0.50: Low confidence, more investigation needed

EVIDENCE FORMAT RULES:
- thread_id: For Slack threads use "slack://channel/ts", for tools use "tool:check_dependencies", "tool:check_entity_history", etc.
- relevance: MUST be one of: "primary" (main evidence), "supporting" (additional context), "contradictory" (conflicts with other evidence)
- summary: Describe what the evidence shows and its importance to the verdict

3. CRITICAL FINAL STEP: After creating your verdict JSON, you MUST call the apply_entity_updates tool
   with your complete verdict JSON as a string parameter. This submits it for human approval.

   Example: apply_entity_updates(verdict_json='{"verdict": "CRITICAL_CONFLICT", "reasoning": "...", ...}')

   DO NOT just return the JSON - you MUST call the apply_entity_updates tool or your verdict will be lost!"""

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

            # With HITL, we always expect an interrupt
            if "__interrupt__" in result:
                logger.info(f"   ⏸️  HITL INTERRUPT: Approval required")
                return result  # Return interrupt info
            else:
                # This shouldn't happen with HITL middleware configured
                logger.warning(f"   ⚠️  No interrupt received - this is unexpected with HITL enabled")
                return result

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

CRITICAL: You must think FORWARD - imagine AFTER you update {issue.affected_entity.id}, will its parents still work?

STEP 1: Update the directly affected entity
- {issue.affected_entity.id} is cascade_level=0 (direct impact)
- Use calculate_suggested_date to get its new date
- This entity will be BLOCKED or DELAYED

STEP 2: Analyze CASCADE impact on ALL upstream dependencies
Use get_dependency_tree({issue.affected_entity.id}) to get ALL entities that depend on it.

For EACH upstream entity (parents, grandparents, etc.):
   a. ASSUME {issue.affected_entity.id} now has its NEW date (from step 1)
   b. Check: Is the upstream entity's current date BEFORE the new date of {issue.affected_entity.id}?
      - YES → Date conflict! This entity's date MUST be updated
      - NO → Check if buffer is sufficient (7+ days)

   c. If date needs updating:
      - Call calculate_suggested_date for this entity using the NEW date of its dependency
      - Use the suggested_date as new_date
      - Set appropriate status (BLOCKED, AT_RISK, DELAYED)

   d. If no date update needed but dependency is delayed:
      - Consider setting status to AT_RISK (depends on delayed entity)

EXAMPLE - THINK THROUGH THE CASCADE:
If {issue.affected_entity.id} is Q3_INTEGRATION_TEST moving from Oct 18 → Nov 13:

1. Q3_DELIVERY_MILESTONE (parent, currently Oct 25):
   - Oct 25 < Nov 13? YES! ❌
   - Milestone is BEFORE test completion!
   - MUST update: Call calculate_suggested_date(Q3_DELIVERY_MILESTONE, Nov 13)
   - Result: Nov 13 + 7 days buffer = Nov 20
   - Set new_date: "2023-11-20", new_status: "AT_RISK", cascade_level: 1

2. SYSTEM_FLIGHT_TEST (grandparent, currently Nov 20):
   - Depends on Q3_DELIVERY_MILESTONE which now moved to Nov 20
   - Nov 20 (flight test) vs Nov 20 (delivery)? Same day! ❌
   - No buffer! MUST update
   - Calculate: Nov 20 + 3 days prep = Nov 23
   - Set new_date: "2023-11-23", cascade_level: 2

3. Use query_vector_store to check for any additional context about entities

OUTPUT REQUIREMENTS:
- Include {issue.affected_entity.id} as cascade_level=0 (direct impact)
- Include ALL upstream entities that need status OR date changes
- For entities with date conflicts, populate new_date with calculate_suggested_date result
- Set cascade_level based on distance from direct entity (1, 2, 3...)
- Be comprehensive - a manager needs to see the full blast radius

CRITICAL FINAL STEP - MANDATORY:
You MUST call the apply_entity_updates tool with your complete verdict JSON.
This is NOT optional - without this tool call, your verdict will be lost!

Steps:
1. Create your complete verdict JSON with all fields above
2. Convert it to a JSON string
3. Call: apply_entity_updates(verdict_json='<your complete JSON here>')

DO NOT just output the JSON and stop - you MUST make the tool call!
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

TASK - OPPORTUNITY ANALYSIS WITH HISTORICAL CONTEXT:

CRITICAL: This is an OPPORTUNITY to ROLL BACK inflated dates that were pushed out due to the blocker!

STEP 1: Understand WHY current dates exist (Historical Context)
Use check_entity_history for {issue.affected_entity.id} and its upstream dependencies to find:
   a. What were the ORIGINAL dates before the blocker caused delays?
   b. When were dates PUSHED OUT due to {issue.trigger_entity.id} delay?
   c. Look for commit messages like "Judge Verdict Applied" or "CASCADE" that show date changes

Example history analysis:
- Q3_INTEGRATION_TEST original: Oct 18
- Changed to Nov 13 on 2023-10-15 (due to HB900_DRIVER delay to Nov 7)
- Now HB900_DRIVER recovered to Oct 14
- Can we go back to Oct 18? (Oct 18 > Oct 14 + 3 days prep = Oct 17) YES!

STEP 2: Determine what dates CAN be rolled back
For {issue.affected_entity.id} and EACH upstream entity:
   a. Check current date vs original date (from history)
   b. Check if original date is still feasible given NEW {issue.trigger_entity.id} date
   c. Use calculate_suggested_date to verify date compatibility
   d. If original date is feasible → ROLL BACK to original
   e. If not feasible but can be earlier than current → Calculate new earlier date

STEP 3: Check for OTHER blockers
Use check_dependencies for each entity - can only roll back if this was the ONLY blocker

STEP 4: Get full dependency tree
Use get_dependency_tree({issue.affected_entity.id}) to see ALL upstream entities
Each upstream entity might also have inflated dates that can be rolled back

EXAMPLE ROLLBACK LOGIC:
Current state (after conflict):
- HB900_DRIVER: Nov 7 → Oct 14 (OPPORTUNITY!)
- Q3_INTEGRATION_TEST: Nov 13 (was Oct 18 originally)
- Q3_DELIVERY_MILESTONE: Nov 20 (was Oct 25 originally)

Analysis:
1. Q3_INTEGRATION_TEST history shows: Oct 18 → Nov 13 (pushed due to HB900 delay)
   - Can roll back to Oct 18? Check: Oct 18 vs Oct 14 + 3 days = Oct 17? YES!
   - new_date: "2023-10-18" (ROLLBACK to original)

2. Q3_DELIVERY_MILESTONE history shows: Oct 25 → Nov 20 (cascaded from test delay)
   - Can roll back to Oct 25? Check: Oct 25 vs Oct 18 + 7 days = Oct 25? YES!
   - new_date: "2023-10-25" (ROLLBACK to original)

OUTPUT REQUIREMENTS:
- Include {issue.affected_entity.id} as cascade_level=0 (direct unblock)
- Include ALL upstream entities with date rollbacks
- Use historical dates from check_entity_history where possible
- Explain in reasoning: "Rolling back from X to original date Y"
- Set cascade_level based on distance from direct entity (1, 2, 3...)

CRITICAL FINAL STEP - MANDATORY:
You MUST call the apply_entity_updates tool with your complete verdict JSON.
This is NOT optional - without this tool call, your verdict will be lost!

Steps:
1. Create your complete verdict JSON with all fields above
2. Convert it to a JSON string
3. Call: apply_entity_updates(verdict_json='<your complete JSON here>')

DO NOT just output the JSON and stop - you MUST make the tool call!
"""

    # NOTE: _parse_agent_output is no longer used with HITL enabled
    # The verdict JSON is stored directly in pending_approvals table
    # and parsed when needed by the dashboard or API endpoints


def get_global_checkpointer():
    """Get the global checkpointer instance for API endpoints to resume workflows"""
    return _global_checkpointer
