from app.capabilities.registry import (
    approve_capability,
    capability_status,
    ensure_capability_schema,
    get_capability,
    list_capabilities,
    mark_installed,
    onboard_new_agent_definitions,
    register_capability,
    rescan_capability,
)
from app.capabilities.catalog import BOUND_CAPABILITY_CATALOG

__all__ = [
    "approve_capability",
    "capability_status",
    "ensure_capability_schema",
    "get_capability",
    "list_capabilities",
    "mark_installed",
    "onboard_new_agent_definitions",
    "register_capability",
    "rescan_capability",
    "BOUND_CAPABILITY_CATALOG",
]
