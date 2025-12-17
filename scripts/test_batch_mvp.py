#!/usr/bin/env python3
"""Interactive Test Script for Truth Engine Scenarios"""

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

# Available scenarios
SCENARIOS = {
    "1": {
        "name": "HB900 Delay (Conflict)",
        "description": "HB900 driver delayed to Nov 7 due to customs - Creates CONFLICT with Q3 integration test",
        "file": "scenario_hb900_delay.json",
        "expected": "CONFLICT issue → Judge suggests cascade date updates → Pending approval"
    },
    "2": {
        "name": "HB900 Fast-Tracked (Opportunity)",
        "description": "HB900 driver expedited to Oct 14 - Creates OPPORTUNITY to unblock dependencies",
        "file": "scenario_hb900_delay_resolved.json",
        "expected": "OPPORTUNITY issue → Judge suggests unblocking entities → Pending approval"
    },
    "3": {
        "name": "Cascade Delay (Complex Conflict)",
        "description": "Multiple cascading delays across dependency tree",
        "file": "scenario_cascade_delay.json",
        "expected": "Multiple CONFLICT issues → Cascade impact analysis → Multiple approvals"
    }
}

def send_message(message):
    """Send a message to the /ingest endpoint"""
    response = httpx.post(f"{API_URL}/ingest_batch", json=message, timeout=60.0)
    response.raise_for_status()
    return response.json()

def display_menu():
    """Display scenario selection menu"""
    print()
    print("=" * 70)
    print("  TRUTH ENGINE - INTERACTIVE TEST SCENARIOS")
    print("=" * 70)
    print()
    print("Select a test scenario:")
    print()

    for key, scenario in SCENARIOS.items():
        print(f"  [{key}] {scenario['name']}")
        print(f"      {scenario['description']}")
        print(f"      Expected: {scenario['expected']}")
        print()

    print("  [q] Quit")
    print()
    print("=" * 70)

def get_user_choice():
    """Get user's scenario choice"""
    while True:
        choice = input("\nEnter your choice (1-3, or q to quit): ").strip().lower()

        if choice == 'q':
            print("\nExiting... Goodbye!")
            sys.exit(0)

        if choice in SCENARIOS:
            return choice

        print("❌ Invalid choice. Please enter 1, 2, 3, or q")

async def run_scenario(scenario_key):
    """Run the selected scenario"""
    scenario = SCENARIOS[scenario_key]

    print()
    print("=" * 70)
    print(f"  RUNNING: {scenario['name']}")
    print("=" * 70)
    print()
    print(f"📋 Description: {scenario['description']}")
    print(f"📁 File: {scenario['file']}")
    print()

    # Load test data
    data_dir = Path(__file__).parent.parent / "data" / "messages"
    test_data_path = data_dir / scenario['file']

    if not test_data_path.exists():
        print(f"❌ ERROR: Test data file not found: {test_data_path}")
        return

    print(f"📂 Loading test data from: {test_data_path}")

    with open(test_data_path, "r") as f:
        data = json.load(f)

    print(f"✅ Loaded {len(data.get('messages', []))} messages")
    print()

    # Show message preview
    print("📨 Message Preview:")
    for i, msg in enumerate(data.get('messages', [])[:3], 1):
        print(f"  {i}. [{msg['channel']}] {msg['user']}: {msg['text'][:60]}...")
    if len(data.get('messages', [])) > 3:
        print(f"  ... and {len(data['messages']) - 3} more messages")
    print()

    # Confirm before sending
    confirm = input("▶️  Ready to send batch to Truth Engine? (y/n): ").strip().lower()
    if confirm != 'y':
        print("⏸️  Cancelled. Returning to menu...")
        return

    print()
    print("🚀 Sending batch to /ingest_batch endpoint...")

    try:
        # Send batch to /ingest_batch endpoint
        response = send_message(data)

        print()
        print("=" * 70)
        print("  PROCESSING RESULTS")
        print("=" * 70)
        print()

        print(f"✅ Status: {response.get('status', 'unknown')}")
        print()
        print(f"📊 Pipeline Metrics:")
        print(f"  • Messages received: {response.get('messages_received', 0)}")
        print(f"  • Signal messages: {response.get('signal_messages', 0)}")
        print(f"  • Issues detected: {response.get('conflicts_detected', 0)}")
        print(f"  • Pending approvals: {response.get('verdicts_issued', 0)}")
        print()

        # Show expected vs actual
        print(f"📋 Expected Outcome: {scenario['expected']}")
        print()

        # Show next steps
        if response.get('verdicts_issued', 0) > 0:
            print("=" * 70)
            print("  NEXT STEPS")
            print("=" * 70)
            print()
            print(f"🔔 {response['verdicts_issued']} approval(s) are now pending!")
            print()
            print("To review and approve:")
            print("  1. Open the dashboard: http://localhost:8501")
            print("  2. Go to the '🔐 Pending Approvals' tab")
            print("  3. Review the verdict details and evidence")
            print("  4. Enter your name and click 'Approve' or 'Reject'")
            print()
        else:
            print("ℹ️  No approvals created. Check logs for details.")
            print()

        print("=" * 70)
        print(f"  SCENARIO COMPLETE: {scenario['name']}")
        print("=" * 70)

    except httpx.HTTPStatusError as e:
        print()
        print("=" * 70)
        print(f"  ❌ HTTP ERROR: {e.response.status_code}")
        print("=" * 70)
        print()
        print(f"Response: {e.response.text}")
        print()
        print("💡 Make sure the backend is running:")
        print("   cd backend && uvicorn app.main:app --reload")

    except httpx.ConnectError:
        print()
        print("=" * 70)
        print(f"  ❌ CONNECTION ERROR")
        print("=" * 70)
        print()
        print(f"Could not connect to {API_URL}")
        print()
        print("💡 Make sure the backend is running:")
        print("   cd backend && uvicorn app.main:app --reload")

    except Exception as e:
        print()
        print("=" * 70)
        print(f"  ❌ ERROR: {e}")
        print("=" * 70)
        import traceback
        traceback.print_exc()

async def main():
    """Main interactive loop"""
    while True:
        display_menu()
        choice = get_user_choice()
        await run_scenario(choice)

        print()
        again = input("\n🔄 Run another scenario? (y/n): ").strip().lower()
        if again != 'y':
            print("\n👋 Goodbye!")
            break

if __name__ == "__main__":
    asyncio.run(main())
