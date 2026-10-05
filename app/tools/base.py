from dataclasses import dataclass
from typing import Callable, Any

from app.security.policy import ToolPolicy


@dataclass
class ToolDefinition:
    name: str
    handler: Callable[..., Any]
    policy: ToolPolicy
    description: str
