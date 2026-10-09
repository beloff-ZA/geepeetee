from typing import Any

from app.connectors.school_edge import queue_job
from app.security.policy import RiskLevel, ToolPolicy
from app.tools.registry import register_tool


@register_tool(
    name="school_edge_read",
    description=(
        "Queue one typed, read-only inspection job for the school edge connector. "
        "No arbitrary shell, script, SQL or credential material is accepted."
    ),
    policy=ToolPolicy(
        name="school_edge_read",
        risk=RiskLevel.READ,
        enabled=True,
        approval_required=False,
        allowed_environments=["school"],
        allow_arbitrary_input=False,
        requires_target=False,
        max_targets=1,
    ),
)
def school_edge_read(
    *,
    connector_id: str = "school-edge-01",
    capability: str,
    target: str | None = None,
    parameters: dict[str, Any] | None = None,
    requested_by: str = "operator",
) -> dict:
    job = queue_job(
        connector_id=connector_id,
        environment_id="school",
        capability=capability,
        target=target,
        parameters=parameters or {},
        requested_by=requested_by,
    )

    return {
        "queued": True,
        "job_id": str(job["id"]),
        "connector_id": job["connector_id"],
        "capability": job["capability"],
        "target": job.get("target"),
        "execution_performed": False,
        "note": (
            "The edge connector will execute the typed read-only job locally "
            "using school-side credentials and return evidence asynchronously."
        ),
    }
