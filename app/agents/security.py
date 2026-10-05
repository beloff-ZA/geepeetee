import json
import uuid

from app.agents.catalog import get_agent
from app.agents.schema import ensure_agent_schema
from app.agents.types import AgentAuthority
from app.db.database import execute, fetch_one
from app.tools.registry import TOOLS


WRITE_RISKS = {
    "safe_write",
    "privileged_write",
    "destructive",
}


def propose_action(
    *,
    run_id: str,
    agent_id: str,
    tool_name: str,
    environment: str,
    arguments: dict,
    rationale: str,
    evidence_refs: list[str] | None = None,
) -> dict:
    """
    Record an action proposal. This function NEVER executes a tool.
    """

    ensure_agent_schema()

    agent = get_agent(agent_id)

    if (
        agent.authority
        != AgentAuthority.PROPOSE_ACTION
    ):
        raise PermissionError(
            f"Agent '{agent_id}' may not propose actions"
        )

    tool = TOOLS.get(tool_name)

    requested_risk = (
        tool.policy.risk.value
        if tool
        else "unknown"
    )

    proposal_id = str(uuid.uuid4())
    target = arguments.get("target")

    execute(
        """
        INSERT INTO agent_action_proposals (
            id,
            run_id,
            agent_id,
            tool_name,
            environment,
            target,
            arguments,
            rationale,
            evidence_refs,
            requested_risk
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s::jsonb, %s, %s::jsonb, %s
        )
        """,
        (
            proposal_id,
            run_id,
            agent_id,
            tool_name,
            environment,
            target,
            json.dumps(arguments),
            rationale,
            json.dumps(
                evidence_refs or []
            ),
            requested_risk,
        ),
    )

    return review_action_proposal(
        proposal_id
    )


def review_action_proposal(
    proposal_id: str,
) -> dict:
    """
    Deterministic gate. It only evaluates eligibility and never executes.
    """

    ensure_agent_schema()

    proposal = fetch_one(
        """
        SELECT *
        FROM agent_action_proposals
        WHERE id = %s
        """,
        (proposal_id,),
    )

    if not proposal:
        raise KeyError(
            "Action proposal not found"
        )

    agent = get_agent(
        proposal["agent_id"]
    )

    tool = TOOLS.get(
        proposal["tool_name"]
    )

    status = "rejected"
    reason = "Rejected by default"

    if not agent.enabled:
        reason = "Agent is disabled"

    elif (
        agent.authority
        != AgentAuthority.PROPOSE_ACTION
    ):
        reason = (
            "Agent lacks action-proposal authority"
        )

    elif not tool:
        reason = "Unknown tool"

    elif not tool.policy.enabled:
        reason = "Tool is disabled"

    elif (
        agent.allowed_tools
        and tool.name not in agent.allowed_tools
    ):
        reason = (
            "Tool is outside the agent allowlist"
        )

    elif (
        tool.policy.allowed_environments
        and proposal["environment"]
        not in tool.policy.allowed_environments
    ):
        reason = (
            "Environment is outside the tool policy"
        )

    elif (
        tool.policy.risk.value
        in WRITE_RISKS
    ):
        status = "approval_required"
        reason = (
            "Write-capable actions always require "
            "explicit human approval and normal "
            "tool-registry validation."
        )

    else:
        status = "eligible_for_execution"
        reason = (
            "Read-only proposal passed the agent gate. "
            "Execution must still go through the normal "
            "tool registry and audit pipeline."
        )

    execute(
        """
        UPDATE agent_action_proposals
        SET
            gate_status = %s,
            gate_reason = %s,
            reviewed_at = NOW()
        WHERE id = %s
        """,
        (
            status,
            reason,
            proposal_id,
        ),
    )

    return {
        "proposal_id": proposal_id,
        "gate_status": status,
        "gate_reason": reason,
        "tool_name": proposal["tool_name"],
        "risk": proposal["requested_risk"],
        "execution_performed": False,
    }
