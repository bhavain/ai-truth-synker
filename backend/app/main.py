"""FastAPI application for Truth Engine - Batch Processing MVP"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models import BatchSlackMessages
from app.graph.batch_langgraph import process_batch

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle management for the application"""
    logger.info("Starting Truth Engine Batch Processing MVP...")
    settings = get_settings()
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"LangSmith Tracing: {settings.langsmith_tracing_v2}")
    yield
    logger.info("Shutting down Truth Engine API...")


# Create FastAPI app
app = FastAPI(
    title="Truth Engine Batch Processing MVP",
    description="Autonomous agents processing conversation batches",
    version="0.2.0-mvp",
    lifespan=lifespan
)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "truth-engine-batch-mvp",
        "version": "0.2.0"
    }


@app.post("/ingest_batch", status_code=status.HTTP_200_OK)
async def ingest_batch(batch: BatchSlackMessages):
    """
    Ingest a batch of messages and process through autonomous agents.

    This MVP endpoint:
    1. Bouncer: Batch classify SIGNAL vs NOISE
    2. Group: Organize into conversation threads by channel
    3. Watchers: Channel-specific autonomous agents process conversations
    4. Arbiter: Detect conflicts across all updates
    5. Judge: Adjudicate conflicts (if any)

    Args:
        batch: Batch of Slack messages with time window

    Returns:
        Simple acknowledgment
    """
    logger.info(f"Received batch: {len(batch.messages)} messages")

    try:
        # Process batch through LangGraph workflow
        result = await process_batch(batch)

        # Simple response
        return {
            "status": "accepted",
            "messages_received": len(batch.messages),
            "signal_messages": len(result.get("signal_messages", [])),
            "conflicts_detected": len(result.get("conflicts", [])),
            "verdicts_issued": len(result.get("verdicts", [])),
            "notifications_sent": result.get("notifications_sent", 0)
        }

    except Exception as e:
        logger.error(f"Batch processing failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch processing failed: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main_batch:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )
