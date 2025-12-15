"""Pydantic schemas for Truth Engine data models"""

from datetime import date, datetime
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field
from .enums import (
    EntityType,
    EntityStatus,
    DependencyType,
    MessageClass,
    ConflictSeverity,
)


# ============================================================================
# Database Models
# ============================================================================


class ProjectEntity(BaseModel):
    """Represents a hardware project entity (part, test, milestone)"""

    id: str = Field(..., description="Unique identifier (e.g., 'HB900_DRIVER')")
    name: str = Field(..., description="Human-readable name")
    entity_type: EntityType
    status: EntityStatus
    milestone_date: date = Field(..., description="Expected arrival or completion date")
    owner_team: str = Field(..., description="Team responsible (e.g., 'supply-chain')")
    last_commit_id: Optional[str] = Field(None, description="Dolt commit hash for audit trail")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional context")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "HB900_DRIVER",
                "name": "H-Bridge Driver",
                "entity_type": "PART",
                "status": "ON_TRACK",
                "milestone_date": "2023-10-15",
                "owner_team": "supply-chain",
                "metadata": {"vendor": "TechSupply Corp", "part_number": "HB-900-REV3"},
            }
        }


class Dependency(BaseModel):
    """Represents a dependency relationship between entities"""

    parent_id: str = Field(..., description="Entity that depends on the child")
    child_id: str = Field(..., description="Entity that must complete first")
    dependency_type: DependencyType
    confidence: float = Field(
        1.0, ge=0.0, le=1.0, description="Confidence score (1.0 = manual, <1.0 = AI-inferred)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "parent_id": "Q3_INTEGRATION_TEST",
                "child_id": "HB900_DRIVER",
                "dependency_type": "CRITICAL_BLOCKER",
                "confidence": 1.0,
            }
        }


# ============================================================================
# Slack Message Models
# ============================================================================


class SlackMessage(BaseModel):
    """Realistic Slack webhook message format"""

    channel: str = Field(..., description="Channel name (e.g., 'supply-chain')")
    ts: str = Field(..., description="Slack timestamp (unique message ID)")
    user: str = Field(..., description="Slack user ID")
    text: str = Field(..., description="Message content")
    thread_ts: Optional[str] = Field(None, description="Thread parent timestamp")
    timestamp: datetime = Field(default_factory=datetime.now, description="ISO timestamp")

    @property
    def thread_id(self) -> str:
        """Generate a unique thread identifier"""
        return f"slack://{self.channel}/{self.thread_ts or self.ts}"

    class Config:
        json_schema_extra = {
            "example": {
                "channel": "supply-chain",
                "ts": "1698163200.123456",
                "user": "U123ABC",
                "text": "Vendor says the H-Bridge Driver (HB-900) is stuck in customs. 2 week delay.",
                "thread_ts": None,
            }
        }


# ============================================================================
# Agent Processing Models
# ============================================================================


class ExtractedData(BaseModel):
    """Data extracted by Watcher agent from Slack message"""

    entity_id: Optional[str] = Field(None, description="Extracted entity ID")
    entity_name: Optional[str] = Field(None, description="Entity name mentioned")
    status: Optional[EntityStatus] = Field(None, description="Updated status")
    milestone_date: Optional[date] = Field(None, description="Updated date")
    summary: str = Field(..., description="Concise summary for vector storage")
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Extraction confidence")
    source_thread_id: str = Field(..., description="Reference to source Slack thread")


class SQLAction(BaseModel):
    """Structured SQL action (never raw SQL strings)"""

    action: Literal["INSERT", "UPDATE"] = Field(..., description="SQL operation type")
    table: Literal["project_entities", "dependencies"] = Field(..., description="Target table")
    entity_id: str = Field(..., description="Primary key value")
    updates: dict[str, Any] = Field(..., description="Column updates as key-value pairs")
    source_thread_id: str = Field(..., description="Slack message provenance")
    commit_message: str = Field(..., description="Dolt commit message")

    class Config:
        json_schema_extra = {
            "example": {
                "action": "UPDATE",
                "table": "project_entities",
                "entity_id": "HB900_DRIVER",
                "updates": {"status": "DELAYED", "milestone_date": "2023-10-25"},
                "source_thread_id": "slack://supply-chain/1698163200.123456",
                "commit_message": "Supply chain reports HB900 customs delay",
            }
        }


# ============================================================================
# Dependency Issue Detection Models
# ============================================================================


class DependencyIssue(BaseModel):
    """Dependency issue detected by Arbiter (conflict or resolution opportunity)"""

    issue_id: str = Field(..., description="Unique issue identifier")
    issue_type: Literal["CONFLICT", "OPPORTUNITY"] = Field(
        ..., description="CONFLICT: blocking issue, OPPORTUNITY: potential resolution"
    )
    trigger_entity: ProjectEntity = Field(..., description="Entity that changed")
    affected_entity: ProjectEntity = Field(..., description="Entity impacted by the change")
    dependency: Dependency
    reason: str = Field(..., description="Why this issue exists")
    severity: ConflictSeverity = Field(..., description="Impact level")
    detected_at: datetime = Field(default_factory=datetime.now)


class EvidenceReference(BaseModel):
    """Citation to a Slack thread used in Judge reasoning"""

    thread_id: str
    relevance: Literal["primary", "supporting", "contradictory"]
    summary: str = Field(..., description="What this thread contributed to the verdict")


class EntityUpdate(BaseModel):
    """A single entity status update as part of cascade resolution"""

    entity_id: str = Field(..., description="Entity to update")
    entity_name: str = Field(..., description="Human-readable name of the entity")
    new_status: EntityStatus = Field(..., description="New status to apply")
    reasoning: str = Field(..., description="Why this entity needs this update")
    cascade_level: int = Field(0, description="0=direct, 1+=cascade levels")


class JudgeVerdict(BaseModel):
    """Final verdict from Judge agent with evidence provenance and cascade updates"""

    issue_id: str = Field(..., description="References DependencyIssue.issue_id")
    issue_type: Literal["CONFLICT", "OPPORTUNITY"] = Field(..., description="Type of issue deliberated")
    verdict: str = Field(..., description="CRITICAL_CONFLICT, RESOLUTION_RECOMMENDED, FALSE_POSITIVE, etc.")
    reasoning: str = Field(..., description="Detailed explanation of the decision")
    evidence: list[EvidenceReference] = Field(
        ..., description="All Slack threads consulted"
    )
    recommended_action: str = Field(
        ..., description="Suggested remediation for engineering teams"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in this verdict (used for auto-apply threshold)")
    entity_updates: list[EntityUpdate] = Field(
        default_factory=list,
        description="All entities that should be updated (includes cascade impacts)"
    )
    decided_at: datetime = Field(default_factory=datetime.now)
    notified_teams: list[str] = Field(default_factory=list, description="Teams alerted")


    class Config:
        json_schema_extra = {
            "example": {
                "issue_id": "conflict_2023-10-24_001",
                "issue_type": "CONFLICT",
                "verdict": "CRITICAL_CONFLICT",
                "reasoning": "Supply chain reports H-Bridge Driver delayed to Oct 25 due to customs hold. Avionics has Integration Test scheduled Oct 15. Driver is a CRITICAL_BLOCKER for the test.",
                "evidence": [
                    {
                        "thread_id": "slack://supply-chain/1698163200.123456",
                        "relevance": "primary",
                        "summary": "Vendor reports 2-week customs delay for HB-900",
                    }
                ],
                "recommended_action": "Reschedule Integration Test to Oct 26 or later, or expedite customs clearance",
                "logic_rule_violated": "milestone_dependency_date_violation",
                "confidence": 0.95,
            }
        }


# ============================================================================
# Batch Processing Models
# ============================================================================


class BatchSlackMessages(BaseModel):
    """Batch of messages for processing"""

    messages: list[SlackMessage] = Field(..., description="List of Slack messages to process")
    window_start: datetime = Field(..., description="Start of time window")
    window_end: datetime = Field(..., description="End of time window")

    class Config:
        json_schema_extra = {
            "example": {
                "messages": [
                    {
                        "channel": "supply-chain",
                        "ts": "1698163200.123456",
                        "user": "U123ABC",
                        "text": "HB900 delayed 2 weeks",
                    }
                ],
                "window_start": "2023-10-24T10:00:00",
                "window_end": "2023-10-24T10:30:00",
            }
        }


class ConversationThread(BaseModel):
    """Organized conversation thread from a channel"""

    channel: str = Field(..., description="Channel name")
    messages: list[SlackMessage] = Field(..., description="Chronologically sorted messages")
    window_start: datetime = Field(..., description="First message timestamp")
    window_end: datetime = Field(..., description="Last message timestamp")

    @property
    def duration_minutes(self) -> int:
        """Calculate conversation duration in minutes"""
        delta = self.window_end - self.window_start
        return int(delta.total_seconds() / 60)

    @property
    def message_count(self) -> int:
        """Number of messages in thread"""
        return len(self.messages)


class ExtractedUpdate(BaseModel):
    """Single entity update extracted from conversation"""

    entity_id: str = Field(..., description="Entity identifier")
    entity_name: Optional[str] = Field(None, description="Human-readable name")
    status: Optional[EntityStatus] = Field(None, description="Updated status")
    milestone_date: Optional[date] = Field(None, description="Updated milestone date")
    summary: str = Field(..., description="Summary of what was discussed")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Extraction confidence")
    supporting_messages: list[str] = Field(
        default_factory=list, description="Message timestamps that support this extraction"
    )
    reasoning: str = Field(..., description="Agent's reasoning for this extraction")
    tools_used: list[str] = Field(default_factory=list, description="Tools agent used")


class BatchProcessingResult(BaseModel):
    """Result of batch processing"""

    total_messages: int
    signal_messages: int
    noise_messages: int
    threads_by_channel: dict[str, int]
    total_updates: int
    conflicts_detected: int
    agent_tool_usage: dict[str, list[str]] = Field(
        default_factory=dict, description="Tools used by each agent"
    )


# ============================================================================
# LangGraph State
# ============================================================================


class GraphState(BaseModel):
    """State object passed between LangGraph nodes"""

    # Input
    slack_message: SlackMessage

    # Bouncer output
    message_class: Optional[MessageClass] = None

    # Watcher output
    extracted_data: Optional[ExtractedData] = None
    sql_action: Optional[SQLAction] = None
    historical_context: list[str] = Field(default_factory=list)

    # Arbiter output
    conflict_detected: bool = False
    conflict_alert: Optional[DependencyIssue] = None

    # Judge output
    judge_verdict: Optional[JudgeVerdict] = None

    # Workflow metadata
    current_node: str = "bouncer"
    workflow_id: str = Field(default_factory=lambda: f"wf_{datetime.now().isoformat()}")
    error: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True
