"""Supply Chain Watcher - Specialized for parts, shipping, vendors"""

import logging
from app.agents.autonomous_watcher import AutonomousWatcher
from app.models import ConversationThread

logger = logging.getLogger(__name__)


class SupplyChainWatcher(AutonomousWatcher):
    """
    Autonomous agent specialized in supply chain conversations.

    Domain Expertise:
    - Parts and components (drivers, actuators, sensors)
    - Vendors and suppliers
    - Shipping delays (customs, logistics)
    - Lead times and ETAs
    - Critical blockers vs. acceptable delays
    """

    def __init__(self):
        super().__init__("supply-chain")

    @property
    def domain_context(self) -> str:
        return """
DOMAIN EXPERTISE: Supply Chain & Procurement

You are an expert in supply chain, procurement, and logistics. You understand:

**Key Entities:**
- Parts/Components: HB900_DRIVER, ACTUATOR_TVC_001, etc.
- Vendors: TechSupply Corp, AeroComponents Ltd
- Status: ON_TRACK, DELAYED, BLOCKED, AT_RISK

**Common Scenarios:**
- Shipping delays (customs, logistics issues)
- Vendor updates on ETAs
- Part availability changes
- Reordering and alternate suppliers

**Language Patterns:**
- "stuck in customs" → DELAYED status
- "2 week delay" → milestone_date shifts by 14 days
- "confirmed arrival" → definitive information (high confidence)
- "might be delayed" → tentative (lower confidence, wait for confirmation)

**Critical Thinking:**
- Tentative statements ("might", "possibly") have lower confidence
- Confirmations ("confirmed", "vendor says") have higher confidence
- Track progression: uncertainty → investigation → confirmation
- Ignore corrected/retracted statements
"""

    def _create_extraction_task(self, conversation: str, thread: ConversationThread) -> str:
        return f"""
Analyze this {thread.duration_minutes}-minute conversation from #supply-chain with {thread.message_count} messages.

CONVERSATION:
{conversation}

TASK:
Extract ALL entity state updates discussed in this conversation.

IMPORTANT RULES:
1. Use tools to validate:
   - query_entity: Check if entity exists before extracting
   - validate_date: Verify dates are properly formatted
   - search_history: Look for past context about this entity
   - check_dependencies: Understand what depends on this entity

2. Track conversation progression:
   - "might be delayed" (tentative) < "checking with vendor" < "CONFIRMED: delayed"
   - Only extract FINAL/DEFINITIVE state, not speculation
   - If corrected later in conversation, use corrected information

3. Confidence scoring:
   - 0.9-1.0: Confirmed by vendor, definitive statement
   - 0.7-0.9: Strong indication but not explicitly confirmed
   - 0.5-0.7: Inferred from discussion, needs verification
   - <0.5: Too uncertain, don't extract

4. If entity not found in database (query_entity returns found=false):
   - DO NOT extract it
   - Log warning in your reasoning

5. Return JSON array:
[
  {{
    "entity_id": "HB900_DRIVER",
    "entity_name": "H-Bridge Driver (HB-900)",
    "status": "DELAYED",
    "milestone_date": "2023-11-07",
    "summary": "Confirmed 2-week customs delay, new ETA Nov 7 per vendor",
    "confidence": 0.95,
    "supporting_messages": ["10:15", "10:17"],
    "reasoning": "Initial uncertainty at 10:05 resolved to vendor confirmation at 10:15. High confidence due to explicit ETA."
  }}
]

Begin your analysis. Use tools systematically.
"""
