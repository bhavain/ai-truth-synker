"""FastAPI application for Truth Engine - Batch Processing MVP"""

import logging
import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, status, Body
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from langgraph.types import Command

from app.config import get_settings
from app.models import BatchSlackMessages, PendingApproval
from app.graph.batch_langgraph import process_batch
from app.db import get_dolt_client
from app.agents.judge_agent import JudgeAgent, get_global_checkpointer

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
    logger.info(f"LangSmith Tracing: {settings.langsmith_tracing}")
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


# ============================================================================
# Approval Management API Endpoints
# ============================================================================

class ApprovalActionRequest(BaseModel):
    """Request body for approval actions"""
    reviewed_by: str
    rejection_reason: Optional[str] = None


@app.get("/approvals", status_code=status.HTTP_200_OK)
async def get_pending_approvals(status_filter: Optional[str] = None):
    """
    Get all pending approvals, optionally filtered by status.

    Args:
        status_filter: Optional filter (PENDING, APPROVED, REJECTED)

    Returns:
        List of pending approvals with verdicts
    """
    try:
        dolt = get_dolt_client()
        approvals = dolt.get_all_pending_approvals(status_filter)

        # Parse verdict JSON for each approval
        result = []
        for approval in approvals:
            approval_data = dict(approval)
            approval_data["verdict"] = json.loads(approval["verdict_json"])
            del approval_data["verdict_json"]  # Remove raw JSON
            result.append(approval_data)

        return {
            "status": "success",
            "count": len(result),
            "approvals": result
        }

    except Exception as e:
        logger.error(f"Failed to fetch approvals: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch approvals: {str(e)}"
        )


@app.get("/approvals/{approval_id}", status_code=status.HTTP_200_OK)
async def get_approval(approval_id: str):
    """
    Get a specific pending approval by ID.

    Args:
        approval_id: The approval ID

    Returns:
        Approval details with verdict
    """
    try:
        dolt = get_dolt_client()
        approval = dolt.get_pending_approval(approval_id)

        if not approval:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Approval {approval_id} not found"
            )

        # Parse verdict JSON
        approval_data = dict(approval)
        approval_data["verdict"] = json.loads(approval["verdict_json"])
        del approval_data["verdict_json"]

        return {
            "status": "success",
            "approval": approval_data
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch approval {approval_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch approval: {str(e)}"
        )


@app.post("/approvals/{approval_id}/approve", status_code=status.HTTP_200_OK)
async def approve_verdict(approval_id: str, request: ApprovalActionRequest):
    """
    Approve a pending verdict and resume the Judge agent workflow.

    This endpoint:
    1. Marks approval as APPROVED in database
    2. Recreates Judge agent with global checkpointer
    3. Resumes workflow using Command(resume={"type": "approve"})
    4. The apply_entity_updates tool executes and commits to database

    Args:
        approval_id: The approval ID (also used as thread_id)
        request: Approval action request with reviewed_by

    Returns:
        Success message with applied updates
    """
    try:
        dolt = get_dolt_client()

        # Check if approval exists and is pending
        approval = dolt.get_pending_approval(approval_id)
        if not approval:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Approval {approval_id} not found"
            )

        if approval["status"] != "PENDING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Approval {approval_id} is already {approval['status']}"
            )

        # Mark as approved in database
        dolt.approve_pending_approval(approval_id, request.reviewed_by)
        logger.info(f"✓ Marked approval {approval_id} as APPROVED by {request.reviewed_by}")

        # Resume the workflow using LangChain HITL pattern
        # Recreate Judge agent with same global checkpointer
        judge = JudgeAgent()
        config = {"configurable": {"thread_id": approval_id}}

        logger.info(f"   Resuming workflow for thread_id: {approval_id}")

        # Resume with approval decision - this will execute the apply_entity_updates tool
        result = judge.agent.invoke(
            Command(resume={"decisions": [{"type": "approve"}]}),
            config=config
        )

        logger.info(f"   ✓ Workflow resumed and completed for {approval_id}")
        logger.info(f"   ✓ Entity updates applied to database by apply_entity_updates tool")

        # Parse the verdict from database to get entity updates info
        verdict_dict = json.loads(approval["verdict_json"])
        entity_updates_count = len(verdict_dict.get("entity_updates", []))

        return {
            "status": "success",
            "message": f"Approval {approval_id} approved and {entity_updates_count} entity updates applied",
            "approval_id": approval_id,
            "reviewed_by": request.reviewed_by,
            "entity_updates_applied": entity_updates_count
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to approve {approval_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to approve: {str(e)}"
        )


@app.post("/approvals/{approval_id}/reject", status_code=status.HTTP_200_OK)
async def reject_verdict(approval_id: str, request: ApprovalActionRequest):
    """
    Reject a pending verdict and end the workflow.

    This endpoint:
    1. Marks approval as REJECTED in database
    2. Recreates Judge agent with global checkpointer
    3. Resumes workflow using Command(resume={"type": "reject"})
    4. Workflow ends without applying updates

    Args:
        approval_id: The approval ID (also used as thread_id)
        request: Approval action request with reviewed_by and optional rejection_reason

    Returns:
        Success message
    """
    try:
        dolt = get_dolt_client()

        # Check if approval exists and is pending
        approval = dolt.get_pending_approval(approval_id)
        if not approval:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Approval {approval_id} not found"
            )

        if approval["status"] != "PENDING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Approval {approval_id} is already {approval['status']}"
            )

        # Mark as rejected in database
        dolt.reject_pending_approval(
            approval_id,
            request.reviewed_by,
            request.rejection_reason
        )
        logger.info(f"✗ Marked approval {approval_id} as REJECTED by {request.reviewed_by}")
        if request.rejection_reason:
            logger.info(f"   Reason: {request.rejection_reason}")

        # Resume the workflow with rejection decision
        # Recreate Judge agent with same global checkpointer
        judge = JudgeAgent()
        config = {"configurable": {"thread_id": approval_id}}

        logger.info(f"   Resuming workflow for thread_id: {approval_id} with rejection")

        # Resume with rejection decision
        result = judge.agent.invoke(
            Command(resume={"decisions": [
                {
                    "type": "reject",
                    "message": request.rejection_reason or "Rejected by human reviewer"
                }
            ]}),
            config=config
        )

        logger.info(f"   ✓ Workflow resumed and ended for {approval_id} (rejected)")

        # TODO: Send notification about rejection to affected teams

        return {
            "status": "success",
            "message": f"Approval {approval_id} rejected, no updates applied",
            "approval_id": approval_id,
            "reviewed_by": request.reviewed_by,
            "rejection_reason": request.rejection_reason,
            "note": "Workflow ended, no database changes made"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reject {approval_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reject: {str(e)}"
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
