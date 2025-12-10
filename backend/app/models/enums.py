"""Enumerations for Truth Engine domain models"""

from enum import Enum


class EntityType(str, Enum):
    """Hardware project entity types"""

    PART = "PART"
    TEST = "TEST"
    ASSEMBLY = "ASSEMBLY"
    MILESTONE = "MILESTONE"


class EntityStatus(str, Enum):
    """Status values for project entities"""

    ON_TRACK = "ON_TRACK"
    DELAYED = "DELAYED"
    CRITICAL = "CRITICAL"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class DependencyType(str, Enum):
    """Dependency relationship types"""

    CRITICAL_BLOCKER = "CRITICAL_BLOCKER"
    SOFT_DEPENDENCY = "SOFT_DEPENDENCY"
    INFORMATIONAL = "INFORMATIONAL"


class MessageClass(str, Enum):
    """Slack message classification"""

    SIGNAL = "SIGNAL"  # Project-related, actionable
    NOISE = "NOISE"  # Social, administrative, irrelevant


class ConflictSeverity(str, Enum):
    """Severity levels for detected conflicts"""

    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"
