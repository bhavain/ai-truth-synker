"""Watcher Subgraph - Parallel processing per channel"""

import logging
from typing import List

from app.graph.state import BatchGraphState, WatcherSubgraphState
from app.agents.supply_chain_watcher import SupplyChainWatcher
from app.agents.avionics_watcher import AvionicsWatcher
from app.db import get_dolt_client
from app.models import ExtractedUpdate

logger = logging.getLogger(__name__)


class WatcherFactory:
    """Factory for creating channel-specific Watchers"""

    _watchers = {
        "supply-chain": SupplyChainWatcher,
        "supply_chain": SupplyChainWatcher,
        "avionics": AvionicsWatcher,
    }

    @classmethod
    def create(cls, channel: str):
        """Get specialized Watcher for channel"""
        watcher_class = cls._watchers.get(channel)
        if watcher_class:
            return watcher_class()
        else:
            logger.warning(f"No specialized Watcher for #{channel}, skipping")
            return None


def watcher_subgraph_node(state: WatcherSubgraphState) -> WatcherSubgraphState:
    """
    Watcher Subgraph Node (runs in parallel for each channel)

    Processes a single conversation thread using channel-specific ReAct agent.
    Commits updates to Dolt database.

    Args:
        state: Subgraph state with channel and thread

    Returns:
        Updated subgraph state with extracted_updates, tools_used, commit_hash
    """
    channel = state["channel"]
    thread = state["thread"]

    logger.info(f"--- Processing #{channel} ---")
    logger.info(f"🤖 WATCHER: {thread.message_count} messages ({thread.duration_minutes}min)")

    # Get specialized Watcher
    watcher = WatcherFactory.create(channel)
    if not watcher:
        return {
            **state,
            "error": f"No watcher available for #{channel}"
        }

    try:
        # Let agent work autonomously
        updates = watcher.process_conversation(thread)

        if not updates:
            logger.info(f"   No updates extracted from #{channel}")
            return {
                **state,
                "extracted_updates": [],
                "tools_used": [],
                "commit_hash": None
            }

        # Commit to Dolt
        commit_hash = _commit_batch_to_dolt(updates, thread)

        # Track tool usage
        tools_used = updates[0].tools_used if updates else []

        logger.info(f"   ✓ #{channel}: {len(updates)} updates extracted and committed")

        return {
            **state,
            "extracted_updates": updates,
            "tools_used": tools_used,
            "commit_hash": commit_hash,
            "error": None
        }

    except Exception as e:
        logger.error(f"   ✗ Watcher failed for #{channel}: {e}")

        # Retry once
        logger.info(f"   ↻ Retrying #{channel}...")
        try:
            updates = watcher.process_conversation(thread)
            commit_hash = _commit_batch_to_dolt(updates, thread)
            tools_used = updates[0].tools_used if updates else []

            logger.info(f"   ✓ #{channel}: Retry succeeded")

            return {
                **state,
                "extracted_updates": updates,
                "tools_used": tools_used,
                "commit_hash": commit_hash,
                "error": None
            }

        except Exception as retry_error:
            logger.error(f"   ✗ Retry failed for #{channel}: {retry_error}")
            return {
                **state,
                "extracted_updates": [],
                "tools_used": [],
                "commit_hash": None,
                "error": f"Failed after retry: {str(retry_error)}"
            }


def _commit_batch_to_dolt(updates: List[ExtractedUpdate], thread) -> str:
    """
    Commit multiple entity updates in single Dolt transaction.

    Args:
        updates: List of extracted updates
        thread: Conversation thread (for commit message)

    Returns:
        Commit hash or None
    """
    if not updates:
        return None

    dolt = get_dolt_client()

    logger.info(f"💾 Committing {len(updates)} updates for #{thread.channel}...")

    with dolt.get_connection() as conn:
        with conn.cursor() as cursor:
            updated_count = 0

            for update in updates:
                # Build update dict
                update_dict = {}
                if update.status:
                    update_dict["status"] = update.status.value
                if update.milestone_date:
                    update_dict["milestone_date"] = update.milestone_date

                if not update_dict:
                    continue

                # Execute update
                cursor.execute(
                    f"""
                    UPDATE project_entities
                    SET {', '.join(f"{k} = %s" for k in update_dict.keys())}
                    WHERE id = %s
                    """,
                    (*update_dict.values(), update.entity_id)
                )

                if cursor.rowcount > 0:
                    updated_count += 1

            if updated_count == 0:
                logger.info("   No actual changes to commit")
                return None

            # Create commit message
            commit_msg = f"""{thread.channel} conversation ({thread.window_start.strftime('%H:%M')}-{thread.window_end.strftime('%H:%M')})

Updates:
{chr(10).join(f"- {u.entity_id}: {u.summary[:80]}" for u in updates)}

Messages: {thread.message_count}
""".strip()

            try:
                cursor.execute("CALL DOLT_COMMIT('-a', '-m', %s)", (commit_msg,))

                # Get commit hash
                cursor.execute("SELECT HASHOF('HEAD')")
                result = cursor.fetchone()
                commit_hash = result["HASHOF('HEAD')"] if result else None

                logger.info(f"   ✓ Committed: {updated_count} updates → {commit_hash[:8] if commit_hash else 'N/A'}")
                return commit_hash

            except Exception as e:
                if 'nothing to commit' in str(e):
                    logger.info("   No changes to commit (values unchanged)")
                    return None
                raise


def aggregate_watcher_results(state: BatchGraphState) -> BatchGraphState:
    """
    Aggregate results from all parallel watcher subgraphs.

    This node is called after all watcher subgraphs complete.
    It's used as the reducer in LangGraph's map-reduce pattern.

    Args:
        state: Main graph state

    Returns:
        Updated state with aggregated watcher results
    """
    logger.info(f"\n📦 AGGREGATING: Collecting results from all watchers...")

    # Results are already aggregated in state by LangGraph
    total_updates = len(state.get("extracted_updates", []))

    logger.info(f"   ✓ Total updates extracted: {total_updates}")

    return {
        **state,
        "current_node": "watcher_complete"
    }
