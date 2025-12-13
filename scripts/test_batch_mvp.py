#!/usr/bin/env python3
"""Test script for Batch Processing MVP"""

import json
import sys
from pathlib import Path
from datetime import datetime

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.models import BatchSlackMessages, SlackMessage
from app.graph.batch_langgraph import process_batch
import asyncio
import httpx

API_URL = "http://127.0.0.1:8000"

def send_message(message):
    """Send a message to the /ingest endpoint"""
    response = httpx.post(f"{API_URL}/ingest_batch", json=message, timeout=60.0)
    response.raise_for_status()
    return response.json()

async def main():
    print("=" * 70)
    print("TRUTH ENGINE - BATCH PROCESSING MVP TEST")
    print("=" * 70)
    print()

    # Load test data
    test_data_path = Path(__file__).parent.parent / "data" / "batch_test_messages.json"

    print(f"Loading test data from: {test_data_path}")

    with open(test_data_path, "r") as f:
        data = json.load(f)

    # Send batch to /ingest_batch endpoint
    response = send_message(data)
    print(f"Response: {response}")

    print(f"✓ Sent batch to /ingest_batch endpoint")
    print()

    try:
        print(f"  ✓ Status: {response['status']}")

        print(f"  ✓ Messages received: {response['messages_received']}")
        print(f"  ✓ Signal messages: {response['signal_messages']}")
        print(f"  ✓ Conflicts detected: {response['conflicts_detected']}")
        print(f"  ✓ Verdicts issued: {response['verdicts_issued']}")
        print(f"  ✓ Notifications sent: {response['notifications_sent']}")

        # print()
        # print("=" * 70)
        # print("RESULTS")
        # print("=" * 70)
        # print(f"Total messages: {len(batch.messages)}")
        # print(f"SIGNAL messages: {len(result.get('signal_messages', []))}")
        # print(f"NOISE messages: {result.get('noise_count', 0)}")
        # print()
        # print("Threads by channel:")
        # for channel, thread in result.get("conversation_threads", {}).items():
        #     print(f"  #{channel}: {thread.message_count} messages")
        # print()
        # print(f"Total updates extracted: {len(result.get('extracted_updates', []))}")
        # print(f"Conflicts detected: {len(result.get('conflicts', []))}")
        # print(f"Verdicts issued: {len(result.get('verdicts', []))}")
        # print(f"Notifications sent: {result.get('notifications_sent', 0)}")
        # print()
        # print("Agent tool usage:")
        # for channel, tools in result.get("agent_tool_usage", {}).items():
        #     print(f"  #{channel}: {', '.join(tools) if tools else 'none'}")
        # print()

        # # Show conflicts and verdicts if any
        # if result.get("conflicts"):
        #     print("CONFLICTS DETECTED:")
        #     for conflict in result["conflicts"]:
        #         print(f"  - {conflict.parent_entity.id} → {conflict.child_entity.id}")
        #         print(f"    Rule violated: {conflict.logic_rule_violated}")
        #     print()

        # if result.get("verdicts"):
        #     print("JUDGE VERDICTS:")
        #     for verdict in result["verdicts"]:
        #         print(f"  - Conflict: {verdict.conflict_id}")
        #         print(f"    Verdict: {verdict.verdict}")
        #         print(f"    Confidence: {verdict.confidence:.2f}")
        #         print(f"    Reasoning: {verdict.reasoning[:100]}...")
        #     print()

        # print("=" * 70)
        # print("MVP TEST COMPLETE")
        # print("=" * 70)

    except Exception as e:
        print()
        print("=" * 70)
        print(f"ERROR: {e}")
        print("=" * 70)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
