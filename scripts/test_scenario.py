#!/usr/bin/env python3
"""Test the 'Delayed Driver' scenario"""

import sys
import json
import time
from pathlib import Path
from datetime import datetime

import httpx

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

API_URL = "http://localhost:8080"


def load_mock_messages():
    """Load mock Slack messages"""
    data_file = Path(__file__).parent.parent / "data" / "mock_slack_messages.json"
    with open(data_file, "r") as f:
        return json.load(f)


def send_message(message):
    """Send a message to the /ingest endpoint"""
    response = httpx.post(f"{API_URL}/ingest", json=message, timeout=60.0)
    response.raise_for_status()
    return response.json()


def get_conflicts():
    """Get all conflicts"""
    response = httpx.get(f"{API_URL}/conflicts")
    response.raise_for_status()
    return response.json()


def get_status():
    """Get project status"""
    response = httpx.get(f"{API_URL}/status")
    response.raise_for_status()
    return response.json()


def main():
    print("=" * 70)
    print("TRUTH ENGINE - DELAYED DRIVER SCENARIO TEST")
    print("=" * 70)
    print()

    # Load messages
    data = load_mock_messages()
    messages = data["messages"]

    print(f"Scenario: {data['scenario']}")
    print(f"Description: {data['description']}")
    print(f"Total messages: {len(messages)}")
    print()

    # Send each message
    for i, msg in enumerate(messages, 1):
        print(f"[{i}/{len(messages)}] Sending message from #{msg['channel']}...")
        print(f"  User: {msg['user']}")
        print(f"  Text: {msg['text'][:80]}...")

        try:
            result = send_message(msg)
            print(f"  ✓ Status: {result['status']}")
            print(f"  Classification: {result.get('message_class', 'N/A')}")

            if result.get('conflict_detected'):
                print(f"  🚨 CONFLICT DETECTED!")
                if result.get('verdict'):
                    print(f"     Verdict: {result['verdict']['verdict']}")

        except Exception as e:
            print(f"  ✗ Error: {e}")

        print()
        time.sleep(1)  # Small delay between messages

    # Check final state
    print("=" * 70)
    print("FINAL PROJECT STATE")
    print("=" * 70)
    print()

    try:
        entities = get_status()
        print(f"Total entities: {len(entities)}")
        for entity in entities:
            print(f"  - {entity['id']}: {entity['status']} (Due: {entity['milestone_date']})")

        print()
        print("=" * 70)
        print("CONFLICTS AND VERDICTS")
        print("=" * 70)
        print()

        conflicts = get_conflicts()
        print(f"Total conflicts: {conflicts['total']}")

        for conflict in conflicts['conflicts']:
            data = conflict['data']
            print()
            print(f"Conflict ID: {data['conflict_id']}")
            print(f"Summary: {data['summary']}")
            print(f"Verdict: {data['verdict']}")
            print(f"Reasoning: {data['reasoning']}")
            print(f"Recommended Action: {data['recommended_action']}")
            print(f"Confidence: {data['confidence']:.2%}")
            print(f"Evidence Threads: {data['evidence_count']}")

    except Exception as e:
        print(f"Error fetching results: {e}")

    print()
    print("=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
    print()
    print("Check notifications.json for full notification history")
    print()


if __name__ == "__main__":
    # Check API health
    try:
        response = httpx.get(f"{API_URL}/health", timeout=5.0)
        response.raise_for_status()
        print("✓ API is running\n")
    except Exception as e:
        print(f"✗ API is not reachable at {API_URL}")
        print(f"  Error: {e}")
        print()
        print("Make sure the API is running:")
        print("  $ cd backend && poetry run uvicorn app.main:app --reload")
        print()
        sys.exit(1)

    main()
