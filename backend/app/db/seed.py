"""Database seeding utilities"""

import json
import logging
from pathlib import Path
from datetime import datetime

from app.db.dolt_client import get_dolt_client
from app.db.vector_store import get_vector_store
from app.models import ProjectEntity, Dependency

logger = logging.getLogger(__name__)


def load_seed_entities(data_dir: Path = Path("data")) -> None:
    """Load initial project entities and dependencies into Dolt"""
    logger.info("Loading seed entities...")

    dolt = get_dolt_client()

    # Load seed data
    seed_file = data_dir / "seed_entities.json"
    with open(seed_file, "r") as f:
        seed_data = json.load(f)

    # Insert entities
    for entity_data in seed_data["entities"]:
        entity = ProjectEntity(**entity_data)
        try:
            dolt.insert_entity(entity, f"Seed: Add {entity.name}")
        except Exception as e:
            logger.warning(f"Entity {entity.id} may already exist: {e}")

    # Insert dependencies
    for dep_data in seed_data["dependencies"]:
        dependency = Dependency(**dep_data)
        try:
            dolt.insert_dependency(dependency)
        except Exception as e:
            logger.warning(f"Dependency may already exist: {e}")

    logger.info(f"Loaded {len(seed_data['entities'])} entities and {len(seed_data['dependencies'])} dependencies")


def load_bootstrap_context(data_dir: Path = Path("data")) -> None:
    """Load historical context summaries into ChromaDB"""
    logger.info("Loading bootstrap context...")

    vector_store = get_vector_store()

    # Load bootstrap data
    bootstrap_file = data_dir / "bootstrap_context.json"
    with open(bootstrap_file, "r") as f:
        bootstrap_data = json.load(f)

    # Insert historical summaries
    for item in bootstrap_data["historical_summaries"]:
        channel = item["channel"].replace("-", "_")  # supply-chain -> supply_chain

        metadata = {
            "topic": item["topic"],
            "timestamp": item["timestamp"],
            "participants": ",".join(item.get("participants", [])),
        }

        try:
            vector_store.add_context(
                channel=channel,
                summary=item["summary"],
                thread_id=item["thread_id"],
                metadata=metadata,
            )
        except Exception as e:
            logger.error(f"Error loading context for {item['thread_id']}: {e}")

    logger.info(f"Loaded {len(bootstrap_data['historical_summaries'])} historical contexts")


def bootstrap_database(data_dir: Path = Path("data")) -> None:
    """Complete database initialization and seeding"""
    logger.info("=" * 60)
    logger.info("BOOTSTRAPPING TRUTH ENGINE DATABASE")
    logger.info("=" * 60)

    # Initialize Dolt schema
    dolt = get_dolt_client()
    dolt.initialize_database()

    # Load seed data
    load_seed_entities(data_dir)

    # Load historical context
    load_bootstrap_context(data_dir)

    logger.info("=" * 60)
    logger.info("BOOTSTRAP COMPLETE")
    logger.info("=" * 60)


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run bootstrap
    bootstrap_database()
