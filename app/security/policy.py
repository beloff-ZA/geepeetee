from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class RiskLevel(str, Enum):
    READ = "read"
    SAFE_WRITE = "safe_write"
    PRIVILEGED_WRITE = "privileged_write"
    DESTRUCTIVE = "destructive"


@dataclass
class ToolPolicy:
    name: str

    risk: RiskLevel

    enabled: bool = True

    approval_required: bool = False

    allowed_environments: list[str] = field(default_factory=list)

    description: str = ""

    allow_arbitrary_input: bool = False

    requires_target: bool = False

    max_targets: int = 1

    audit_required: bool = True


POLICIES: dict[str, ToolPolicy] = {}
