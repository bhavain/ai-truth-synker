"""
Reset Data Script - Quickly clear and reseed the Truth Engine database

Provides interactive scenario selection for testing different conflict patterns.
"""

import json
import logging
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from app.db.dolt_client import DoltClient
from app.db.vector_store import VectorStore
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Scenario configurations
SCENARIOS = {
    "1": {
        "name": "HB900 Driver Delay",
        "description": "Supply chain delay causes test dependency conflict",
        "message_file": "data/messages/scenario_hb900_delay.json",
    },
    "2": {
        "name": "Cascading Battery Delay",
        "description": "Battery delay cascades through testing to Q4 milestone",
        "message_file": "data/messages/scenario_cascade_delay.json",
    },
}


def clear_dolt_data() -> None:
    """Truncate all tables in Dolt database (preserves schema and commit history)"""
    logger.info("Clearing Dolt data...")
    try:
        dolt_client = DoltClient()

        # Truncate tables
        with dolt_client.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
            cursor.execute("TRUNCATE TABLE dependencies")
            cursor.execute("TRUNCATE TABLE project_entities")
            cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            conn.commit()

        logger.info("✓ Dolt data cleared")
    except Exception as e:
        logger.error(f"Error clearing Dolt data: {e}")
        raise


def clear_chroma_collections() -> None:
    """Delete all documents from ChromaDB collections"""
    logger.info("Clearing ChromaDB collections...")
    try:
        vector_store = VectorStore()

        # Clear all channel collections
        for channel in ["supply_chain", "avionics", "management", "safety", "mgmt"]:
            try:
                collection_name = f"{channel}_context"
                # Delete and recreate collection
                vector_store.client.delete_collection(collection_name)
                logger.info(f"  ✓ Cleared {collection_name}")
            except Exception as e:
                logger.debug(f"  Collection {collection_name} may not exist: {e}")

        logger.info("✓ ChromaDB collections cleared")
    except Exception as e:
        logger.error(f"Error clearing ChromaDB: {e}")
        raise


def reseed_entities() -> None:
    """Load entities and dependencies from seed_entities.json"""
    logger.info("Reseeding entities...")
    try:
        # Load seed data
        seed_path = Path(__file__).parent.parent / "data" / "seed_entities.json"
        with open(seed_path, "r") as f:
            seed_data = json.load(f)

        dolt_client = DoltClient()

        # Insert entities
        for entity in seed_data["entities"]:
            dolt_client.upsert_entity(
                entity_id=entity["id"],
                name=entity["name"],
                entity_type=entity["entity_type"],
                status=entity["status"],
                milestone_date=entity["milestone_date"],
                owner_team=entity["owner_team"],
                metadata=entity["metadata"],
                source="seed_data",
            )

        # Insert dependencies
        for dep in seed_data["dependencies"]:
            dolt_client.add_dependency(
                parent_id=dep["parent_id"],
                child_id=dep["child_id"],
                dependency_type=dep["dependency_type"],
                confidence=dep["confidence"],
                source="seed_data",
            )

        # Commit to Dolt
        dolt_client.commit_changes(
            message="Initial seed: Loaded entities and dependencies",
            author="reset_data_script",
        )

        logger.info(f"✓ Seeded {len(seed_data['entities'])} entities and {len(seed_data['dependencies'])} dependencies")
    except Exception as e:
        logger.error(f"Error seeding entities: {e}")
        raise


def reseed_context() -> None:
    """Load historical context into ChromaDB"""
    logger.info("Reseeding ChromaDB context...")
    try:
        context_path = Path(__file__).parent.parent / "data" / "bootstrap_context.json"
        with open(context_path, "r") as f:
            contexts = json.load(f)

        vector_store = VectorStore()

        for ctx in contexts:
            vector_store.add_context(
                channel=ctx["channel"],
                content=ctx["content"],
                metadata=ctx["metadata"],
            )

        logger.info(f"✓ Seeded {len(contexts)} historical context documents")
    except Exception as e:
        logger.error(f"Error seeding context: {e}")
        raise


def clear_notifications_json() -> None:
    """Clear the notifications.json file"""
    logger.info("Clearing notifications...")
    try:
        notifications_path = Path(__file__).parent.parent / "notifications.json"
        with open(notifications_path, "w") as f:
            json.dump([], f)
        logger.info("✓ Notifications cleared")
    except Exception as e:
        logger.warning(f"Could not clear notifications: {e}")


def copy_scenario_messages(scenario_key: str) -> None:
    """Copy selected scenario message file to batch_test_messages.json"""
    logger.info(f"Loading scenario: {SCENARIOS[scenario_key]['name']}")
    try:
        source_path = Path(__file__).parent.parent / SCENARIOS[scenario_key]["message_file"]
        dest_path = Path(__file__).parent.parent / "data" / "batch_test_messages.json"

        with open(source_path, "r") as f:
            messages = json.load(f)

        with open(dest_path, "w") as f:
            json.dump(messages, f, indent=2)

        logger.info(f"✓ Loaded {len(messages['messages'])} messages from scenario")
    except Exception as e:
        logger.error(f"Error loading scenario messages: {e}")
        raise


def display_menu() -> str:
    """Display scenario selection menu and get user choice"""
    print("\n" + "=" * 60)
    print("  TRUTH ENGINE - DATA RESET")
    print("=" * 60)
    print("\nSelect a scenario to test:\n")

    for key, scenario in SCENARIOS.items():
        print(f"  {key}. {scenario['name']}")
        print(f"     {scenario['description']}\n")

    while True:
        choice = input("Enter scenario number (or 'q' to quit): ").strip()
        if choice.lower() == 'q':
            print("Cancelled.")
            sys.exit(0)
        if choice in SCENARIOS:
            return choice
        print("Invalid choice. Please try again.")


def main():
    """Main reset workflow"""
    print("\n🔄 Truth Engine Data Reset Utility\n")

    # Interactive scenario selection
    scenario_key = display_menu()
    scenario = SCENARIOS[scenario_key]

    print(f"\n📋 Selected: {scenario['name']}")
    print(f"   {scenario['description']}\n")

    confirm = input("This will clear ALL data and reseed. Continue? (y/N): ").strip().lower()
    if confirm != 'y':
        print("Cancelled.")
        return

    print("\n🚀 Starting reset...\n")

    try:
        # Step 1: Clear all data
        clear_dolt_data()
        clear_chroma_collections()
        clear_notifications_json()

        # Step 2: Reseed with fresh data
        reseed_entities()
        reseed_context()

        # Step 3: Load scenario messages
        copy_scenario_messages(scenario_key)

        print("\n" + "=" * 60)
        print("✅ RESET COMPLETE!")
        print("=" * 60)
        print(f"\nScenario loaded: {scenario['name']}")
        print("\nNext steps:")
        print("  1. Start API: cd backend && poetry run uvicorn app.main:app --port 8080")
        print("  2. Run workflow: poetry run python scripts/test_batch_mvp.py")
        print("  3. View results: poetry run streamlit run scripts/dashboard.py")
        print("\n")

    except Exception as e:
        print(f"\n❌ Reset failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
