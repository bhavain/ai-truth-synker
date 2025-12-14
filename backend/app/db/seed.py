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

    # Insert all entities without individual commits
    with dolt.get_connection() as conn:
        cursor = conn.cursor()

        # Insert entities
        for entity_data in seed_data["entities"]:
            entity = ProjectEntity(**entity_data)
            try:
                cursor.execute(
                    """
                    INSERT INTO project_entities
                    (id, name, entity_type, status, milestone_date, owner_team, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        entity.id,
                        entity.name,
                        entity.entity_type.value,
                        entity.status.value,
                        entity.milestone_date,
                        entity.owner_team,
                        json.dumps(entity.metadata) if entity.metadata else None,
                    ),
                )
            except Exception as e:
                logger.warning(f"Entity {entity.id} may already exist: {e}")

        # Insert dependencies
        for dep_data in seed_data["dependencies"]:
            dependency = Dependency(**dep_data)
            try:
                cursor.execute(
                    """
                    INSERT INTO dependencies
                    (parent_id, child_id, dependency_type, confidence)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        dependency_type = VALUES(dependency_type),
                        confidence = VALUES(confidence)
                    """,
                    (
                        dependency.parent_id,
                        dependency.child_id,
                        dependency.dependency_type.value,
                        dependency.confidence,
                    ),
                )
            except Exception as e:
                logger.warning(f"Dependency may already exist: {e}")

        # Single commit for all seed data
        try:
            cursor.execute("CALL DOLT_ADD('.')")
            cursor.execute("CALL DOLT_COMMIT('-m', 'Seed: Initial entity and dependency data')")
            logger.info(f"✓ Committed {len(seed_data['entities'])} entities and {len(seed_data['dependencies'])} dependencies")
        except Exception as e:
            if 'nothing to commit' not in str(e):
                raise
            logger.info("Data already committed")

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
