from dataclasses import dataclass, field
from enum import Enum


class AgentAuthority(str, Enum):
    OBSERVE = "observe"
    ADVISE = "advise"
    PROPOSE_ACTION = "propose_action"


class AgentMode(str, Enum):
    INTERACTIVE = "interactive"
    ON_DEMAND = "on_demand"
    SCHEDULED = "scheduled"
    EVENT_DRIVEN = "event_driven"


class EvidenceRequirement(str, Enum):
    NONE = "none"
    PREFERRED = "preferred"
    REQUIRED = "required"


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    purpose: str
    persona: str
    mandate: str
    authority: AgentAuthority = AgentAuthority.ADVISE
    mode: AgentMode = AgentMode.ON_DEMAND
    evidence_requirement: EvidenceRequirement = EvidenceRequirement.PREFERRED
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    max_runtime_seconds: int = 90
    max_output_tokens: int = 4000
    enabled: bool = True
    persistent: bool = False
    schedule_hint: str | None = None
