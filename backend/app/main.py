"""FastAPI application for Truth Engine"""

import logging
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models import SlackMessage, ProjectEntity, JudgeVerdict
from app.db import get_dolt_client
from app.graph import run_workflow
from app.utils import get_notifications

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the application"""
    logger.info("Starting Truth Engine API...")
    settings = get_settings()
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"LangSmith Tracing: {settings.langchain_tracing_v2}")
    yield
    logger.info("Shutting down Truth Engine API...")


# Create FastAPI app
app = FastAPI(
    title="Truth Engine API",
    description="Hardware Synchronization AI - Evercurrent MVP",
    version="0.1.0",
    lifespan=lifespan
)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "truth-engine",
        "version": "0.1.0"
    }


@app.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_message(message: SlackMessage):
    """
    Ingest a Slack message and process through the Truth Engine workflow.

    This endpoint triggers the full pipeline:
    1. Bouncer (classify)
    2. Watcher (extract & update)
    3. Arbiter (conflict detection)
    4. Judge (adjudication if needed)

    Args:
        message: Slack webhook message payload

    Returns:
        Processing status and workflow ID
    """
    logger.info(f"Received message from #{message.channel}")

    try:
        # Run workflow
        final_state = run_workflow(message)

        response = {
            "status": "processed",
            "workflow_id": final_state.workflow_id,
            "message_class": final_state.message_class.value if final_state.message_class else None,
            "conflict_detected": final_state.conflict_detected,
            "error": final_state.error
        }

        if final_state.judge_verdict:
            response["verdict"] = {
                "conflict_id": final_state.judge_verdict.conflict_id,
                "verdict": final_state.judge_verdict.verdict,
                "confidence": final_state.judge_verdict.confidence
            }

        return response

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Workflow execution failed: {str(e)}"
        )


@app.get("/status", response_model=List[ProjectEntity])
async def get_project_status():
    """
    Get current status of all project entities.

    Returns:
        List of all project entities with current state
    """
    try:
        dolt = get_dolt_client()
        entities = dolt.get_all_entities()
        return entities

    except Exception as e:
        logger.error(f"Error fetching project status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch status: {str(e)}"
        )


@app.get("/conflicts")
async def get_conflicts(channel: str = None):
    """
    Get all conflict alerts and Judge verdicts.

    Query parameters:
        channel: Optional filter by team channel

    Returns:
        List of notifications containing conflict verdicts
    """
    try:
        notifications = get_notifications(channel=channel)

        # Filter for conflict notifications
        conflicts = [
            n for n in notifications
            if n.get("data", {}).get("type") == "CRITICAL_CONFLICT"
        ]

        return {
            "total": len(conflicts),
            "conflicts": conflicts
        }

    except Exception as e:
        logger.error(f"Error fetching conflicts: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch conflicts: {str(e)}"
        )


@app.get("/entity/{entity_id}")
async def get_entity(entity_id: str):
    """
    Get detailed information about a specific entity.

    Args:
        entity_id: Entity identifier (e.g., "HB900_DRIVER")

    Returns:
        Entity details with dependencies
    """
    try:
        dolt = get_dolt_client()
        entity = dolt.get_entity(entity_id)

        if not entity:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entity '{entity_id}' not found"
            )

        # Get dependencies
        dependencies = dolt.get_dependencies_for_entity(entity_id)

        return {
            "entity": entity,
            "dependencies": dependencies
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching entity: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch entity: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )
