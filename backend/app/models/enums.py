"""Enumerations for Truth Engine domain models"""

from enum import Enum


class EntityType(str, Enum):
    """Hardware project entity types"""

    PART = "PART"
    TEST = "TEST"
    ASSEMBLY = "ASSEMBLY"
    MILESTONE = "MILESTONE"
    REQUIREMENT = "REQUIREMENT"


class EntityStatus(str, Enum):
    """Status values for project entities"""

    ON_TRACK = "ON_TRACK"  # Progressing normally, on schedule
    AT_RISK = "AT_RISK"    # Potential issues identified
    DELAYED = "DELAYED"    # Behind schedule
    CRITICAL = "CRITICAL"  # Urgent attention needed
    COMPLETED = "COMPLETED"  # Finished
    BLOCKED = "BLOCKED"    # Cannot proceed, waiting on something


class DependencyType(str, Enum):
    """Dependency relationship types"""

    CRITICAL_BLOCKER = "CRITICAL_BLOCKER"  # Hard blocker: parent cannot proceed until child completes
    INFORMATIONAL = "INFORMATIONAL"         # Reference only: no actual blocking dependency, just context


class MessageClass(str, Enum):
    """Slack message classification"""

    SIGNAL = "SIGNAL"  # Project-related, actionable
    NOISE = "NOISE"  # Social, administrative, irrelevant


class ConflictSeverity(str, Enum):
    """Severity levels for detected conflicts"""

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"
