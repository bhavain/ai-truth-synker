"""Dolt database client for version-controlled factual state"""

import json
import logging
from datetime import date
from typing import Optional, Any
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

from app.config import get_settings
from app.models import ProjectEntity, Dependency, EntityStatus

logger = logging.getLogger(__name__)


class DoltClient:
    """Client for interacting with Dolt version-controlled database"""

    def __init__(self) -> None:
        self.settings = get_settings()

        # Base connection parameters
        self.connection_params = {
            "host": self.settings.dolt_host,
            "port": self.settings.dolt_port,
            "user": self.settings.dolt_user,
            "password": self.settings.dolt_password,
            "database": self.settings.dolt_database,
            "cursorclass": DictCursor,
        }

        # Add SSL configuration for caching_sha2_password support
        ssl_ca_path = Path(__file__).parent.parent.parent.parent / "certs" / "dolt" / "ca-cert.pem"
        if ssl_ca_path.exists():
            self.connection_params["ssl"] = {
                "ca": str(ssl_ca_path)
            }
            # For self-signed certificates in development
            self.connection_params["ssl_verify_cert"] = False
            self.connection_params["ssl_verify_identity"] = False
            logger.info("SSL enabled for Dolt connection")
        else:
            logger.warning(f"SSL CA certificate not found at {ssl_ca_path}. Proceeding without SSL.")

    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = pymysql.connect(**self.connection_params)
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            conn.close()

    def initialize_database(self) -> None:
        """Create database and tables if they don't exist"""
        logger.info("Initializing Dolt database...")

        # First connect without database to create it
        conn_params = self.connection_params.copy()
        conn_params.pop("database")

        conn = pymysql.connect(**conn_params)
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS {self.settings.dolt_database}")
            conn.commit()
        finally:
            conn.close()

        # Now create tables
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Create project_entities table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS project_entities (
                        id VARCHAR(50) PRIMARY KEY,
                        name VARCHAR(200) NOT NULL,
                        entity_type VARCHAR(20) NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        milestone_date DATE NOT NULL,
                        owner_team VARCHAR(50) NOT NULL,
                        last_commit_id VARCHAR(100),
                        metadata JSON,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)

                # Create dependencies table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS dependencies (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        parent_id VARCHAR(50) NOT NULL,
                        child_id VARCHAR(50) NOT NULL,
                        dependency_type VARCHAR(20) NOT NULL,
                        confidence FLOAT DEFAULT 1.0,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (parent_id) REFERENCES project_entities(id),
                        FOREIGN KEY (child_id) REFERENCES project_entities(id),
                        UNIQUE KEY unique_dependency (parent_id, child_id)
                    )
                """)

                # Create initial Dolt commit (only if there are changes)
                try:
                    cursor.execute("CALL DOLT_COMMIT('-a', '-m', 'Initialize Truth Engine schema')")
                except pymysql.err.OperationalError as e:
                    if 'nothing to commit' not in str(e):
                        raise

        logger.info("Database initialized successfully")

    def insert_entity(self, entity: ProjectEntity, commit_message: str) -> None:
        """Insert a new project entity"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
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

                # Dolt commit - add tables first to handle new tables
                cursor.execute("CALL DOLT_ADD('.')")
                cursor.execute("CALL DOLT_COMMIT('-m', %s)", (commit_message,))

        logger.info(f"Inserted entity {entity.id}")

    def update_entity(
        self,
        entity_id: str,
        updates: dict[str, Any],
        commit_message: str,
        source_thread_id: str,
    ) -> Optional[str]:
        """Update an existing entity and create a Dolt commit"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Build UPDATE query dynamically
                set_clauses = []
                values = []

                for key, value in updates.items():
                    set_clauses.append(f"{key} = %s")
                    values.append(value)

                values.append(entity_id)

                query = f"""
                    UPDATE project_entities
                    SET {', '.join(set_clauses)}
                    WHERE id = %s
                """

                cursor.execute(query, values)

                # Create Dolt commit
                full_commit_msg = f"{commit_message} | Source: {source_thread_id}"
                cursor.execute("CALL DOLT_COMMIT('-a', '-m', %s)", (full_commit_msg,))

                # Get commit hash
                cursor.execute("SELECT HASHOF('HEAD')")
                result = cursor.fetchone()
                commit_hash = result["HASHOF('HEAD')"] if result else None

        logger.info(f"Updated entity {entity_id}, commit: {commit_hash}")
        return commit_hash

    def get_entity(self, entity_id: str) -> Optional[ProjectEntity]:
        """Retrieve an entity by ID"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM project_entities WHERE id = %s",
                    (entity_id,)
                )
                row = cursor.fetchone()

                if not row:
                    return None

                return ProjectEntity(
                    id=row["id"],
                    name=row["name"],
                    entity_type=row["entity_type"],
                    status=row["status"],
                    milestone_date=row["milestone_date"],
                    owner_team=row["owner_team"],
                    last_commit_id=row.get("last_commit_id"),
                    metadata=json.loads(row.get("metadata", "{}")) if row.get("metadata") else {},
                )

    def get_all_entities(self) -> list[ProjectEntity]:
        """Retrieve all entities"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT * FROM project_entities")
                rows = cursor.fetchall()

                return [
                    ProjectEntity(
                        id=row["id"],
                        name=row["name"],
                        entity_type=row["entity_type"],
                        status=row["status"],
                        milestone_date=row["milestone_date"],
                        owner_team=row["owner_team"],
                        last_commit_id=row.get("last_commit_id"),
                        metadata=json.loads(row.get("metadata", "{}")) if row.get("metadata") else {},
                    )
                    for row in rows
                ]

    def insert_dependency(self, dependency: Dependency) -> None:
        """Insert a new dependency relationship"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
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

        logger.info(f"Inserted dependency {dependency.parent_id} -> {dependency.child_id}")

    def get_dependencies_for_entity(self, entity_id: str) -> list[Dependency]:
        """Get all dependencies where entity is the parent"""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT parent_id, child_id, dependency_type, confidence
                    FROM dependencies
                    WHERE parent_id = %s
                    """,
                    (entity_id,),
                )
                rows = cursor.fetchall()

                return [
                    Dependency(
                        parent_id=row["parent_id"],
                        child_id=row["child_id"],
                        dependency_type=row["dependency_type"],
                        confidence=row["confidence"],
                    )
                    for row in rows
                ]

    def check_dependency_conflict(
        self, entity_id: str
    ) -> list[tuple[ProjectEntity, ProjectEntity, Dependency]]:
        """
        Check if an entity update creates a dependency conflict.
        Returns list of (parent_entity, child_entity, dependency) tuples where conflict exists.
        """
        conflicts = []

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Find all entities that depend on this entity (parents)
                cursor.execute(
                    """
                    SELECT p.*, d.dependency_type, d.confidence
                    FROM project_entities p
                    JOIN dependencies d ON d.parent_id = p.id
                    WHERE d.child_id = %s AND d.dependency_type = 'CRITICAL_BLOCKER'
                    """,
                    (entity_id,),
                )
                parent_rows = cursor.fetchall()

                # Get the child entity
                child_entity = self.get_entity(entity_id)
                if not child_entity:
                    return conflicts

                for parent_row in parent_rows:
                    parent_entity = ProjectEntity(
                        id=parent_row["id"],
                        name=parent_row["name"],
                        entity_type=parent_row["entity_type"],
                        status=parent_row["status"],
                        milestone_date=parent_row["milestone_date"],
                        owner_team=parent_row["owner_team"],
                        last_commit_id=parent_row.get("last_commit_id"),
                        metadata={},
                    )

                    dependency = Dependency(
                        parent_id=parent_row["id"],
                        child_id=entity_id,
                        dependency_type=parent_row["dependency_type"],
                        confidence=parent_row["confidence"],
                    )

                    # Logic check: parent milestone must be >= child milestone
                    if parent_entity.milestone_date < child_entity.milestone_date:
                        conflicts.append((parent_entity, child_entity, dependency))

        return conflicts


@lru_cache()
def get_dolt_client() -> DoltClient:
    """Get cached Dolt client instance"""
    return DoltClient()
