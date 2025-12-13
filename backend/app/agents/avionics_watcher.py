"""Avionics Watcher - Specialized for tests, integration, hardware testing"""

import logging
from app.agents.autonomous_watcher import AutonomousWatcher
from app.models import ConversationThread

logger = logging.getLogger(__name__)


class AvionicsWatcher(AutonomousWatcher):
    """
    Autonomous agent specialized in avionics and testing conversations.

    Domain Expertise:
    - Integration tests and test cycles
    - Hardware dependencies
    - Test schedules and milestones
    - Blocking vs. non-blocking issues
    - Risk assessment for test delays
    """

    def __init__(self):
        super().__init__("avionics")

    @property
    def domain_context(self) -> str:
        return """
DOMAIN EXPERTISE: Avionics Testing & Integration

You are an expert in avionics testing, hardware integration, and test planning. You understand:

**Key Entities:**
- Tests: Q3_INTEGRATION_TEST, ACTUATOR_TVC_001, etc.
- Test Milestones: Integration, Qualification, Acceptance
- Status: ON_TRACK, DELAYED, BLOCKED, AT_RISK

**Common Scenarios:**
- Test scheduling and rescheduling
- Hardware dependency blockers
- Integration test results
- Risk mitigation discussions

**Language Patterns:**
- "can't run test without X" → BLOCKED status, critical dependency
- "test scheduled for [date]" → milestone_date update
- "we're blocked" → BLOCKED status
- "need to reschedule" → potential milestone_date change (verify new date)
- "false alarm" or "mistaken" → disregard earlier statement

**Critical Thinking:**
- Tests DEPEND on hardware components
- "Blocked" means critical path is affected
- Corrections override earlier statements (e.g., "wait, I was wrong about X")
- Discussion about rescheduling doesn't always mean date changed (need confirmation)
"""

    def _create_extraction_task(self, conversation: str, thread: ConversationThread) -> str:
        return f"""
Analyze this {thread.duration_minutes}-minute conversation from #avionics with {thread.message_count} messages.

CONVERSATION:
{conversation}

TASK:
Extract ALL entity state updates discussed in this conversation.

IMPORTANT RULES:
1. Use tools to validate:
   - query_entity: Check if entity exists before extracting
   - validate_date: Verify dates are properly formatted
   - search_history: Look for past context about this test/component
   - check_dependencies: Understand what this test depends on

2. Handle corrections and false alarms:
   - If someone says "wait, I was wrong" or "false alarm", IGNORE the incorrect statement
   - Example: "HB900 delayed" → "Actually, I meant HB800" → Extract HB800, NOT HB900
   - Track who said what, later statements can override earlier ones

3. Distinguish between discussion and decisions:
   - "We should reschedule" (discussion) ≠ "Test rescheduled to [date]" (decision)
   - Only extract if there's a clear state change

4. Confidence scoring:
   - 0.9-1.0: Explicit decision or confirmed information
   - 0.7-0.9: Strong implication but not explicitly stated
   - 0.5-0.7: Inferred from context
   - <0.5: Too uncertain, don't extract

5. If entity not found in database (query_entity returns found=false):
   - DO NOT extract it
   - Log warning in your reasoning

6. Return JSON array:
[
  {{
    "entity_id": "Q3_INTEGRATION_TEST",
    "entity_name": "Q3 TVC Actuator Integration Test",
    "status": "BLOCKED",
    "milestone_date": null,
    "summary": "Test blocked due to HB900 delay, team discussing rescheduling",
    "confidence": 0.85,
    "supporting_messages": ["10:19", "10:21", "10:23"],
    "reasoning": "Clear indication of blocker at 10:19 and 10:23. No confirmed reschedule date yet."
  }}
]

Begin your analysis. Use tools systematically. Watch for corrections!
"""
