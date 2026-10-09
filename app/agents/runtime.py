import json
import os
import time
import uuid

from app.agents.catalog import AGENTS, get_agent
from app.agents.environment import (
    DEFAULT_ENVIRONMENT_ID,
    bootstrap_school_profile,
    get_environment_packet,
)
from app.agents.evidence import (
    deterministic_concerns,
    extract_json_object,
    normalise_evidence,
)
from app.agents.knowledge import (
    knowledge_packet,
    remember_agent_note,
)
from app.agents.prompts import build_agent_prompt
from app.agents.schema import ensure_agent_schema
from app.agents.types import EvidenceRequirement
from app.capabilities.registry import agent_is_trusted
from app.ai.router import RouterExhausted, route_request
from app.ai.state import AIState, get_state, selected_model
from app.db.database import execute, fetch_all, fetch_one


def _record_concern(
    *,
    run_id: str,
    source_agent_id: str,
    severity: str,
    category: str,
    message: str,
    evidence_refs: list[str] | None = None,
) -> str:
    concern_id = str(uuid.uuid4())

    execute(
        """
        INSERT INTO agent_concerns (
            id,
            run_id,
            source_agent_id,
            severity,
            category,
            message,
            evidence_refs
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s::jsonb
        )
        """,
        (
            concern_id,
            run_id,
            source_agent_id,
            severity,
            category,
            message[:4000],
            json.dumps(evidence_refs or []),
        ),
    )

    return concern_id


def _sync_runtime_state() -> None:
    ensure_agent_schema()
    bootstrap_school_profile()

    for agent in AGENTS.values():
        trusted = agent_is_trusted(agent.id)
        initial_enabled = bool(agent.enabled and trusted)
        paused_reason = (
            None
            if trusted
            else "unscanned_agent_security_gate"
        )

        execute(
            """
            INSERT INTO agent_runtime_state (
                agent_id,
                enabled,
                persistent,
                mode,
                schedule_expression,
                paused_reason
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (agent_id) DO UPDATE
            SET
                persistent = EXCLUDED.persistent,
                mode = EXCLUDED.mode,
                updated_at = NOW()
            """,
            (
                agent.id,
                initial_enabled,
                agent.persistent,
                agent.mode.value,
                agent.schedule_hint,
                paused_reason,
            ),
        )


def list_agents() -> list[dict]:
    _sync_runtime_state()

    states = fetch_all(
        """
        SELECT *
        FROM agent_runtime_state
        ORDER BY agent_id
        """
    )

    state_by_id = {
        row["agent_id"]: dict(row)
        for row in states
    }

    result = []

    for agent in AGENTS.values():
        state = state_by_id.get(
            agent.id,
            {},
        )

        result.append({
            "id": agent.id,
            "trusted": agent_is_trusted(agent.id),
            "name": agent.name,
            "purpose": agent.purpose,
            "persona": agent.persona,
            "mandate": agent.mandate,
            "authority": agent.authority.value,
            "mode": agent.mode.value,
            "evidence_requirement":
                agent.evidence_requirement.value,
            "allowed_tools":
                list(agent.allowed_tools),
            "skills":
                list(agent.skills),
            "max_runtime_seconds":
                agent.max_runtime_seconds,
            "max_output_tokens":
                agent.max_output_tokens,
            "persistent":
                agent.persistent,
            "enabled":
                bool(
                    state.get(
                        "enabled",
                        agent.enabled,
                    )
                ),
            "schedule_expression":
                state.get(
                    "schedule_expression"
                ),
            "last_run_at":
                state.get("last_run_at"),
            "next_run_at":
                state.get("next_run_at"),
            "consecutive_failures":
                state.get(
                    "consecutive_failures",
                    0,
                ),
            "paused_reason":
                state.get("paused_reason"),
        })

    return result


def set_agent_enabled(
    agent_id: str,
    enabled: bool,
) -> None:
    _sync_runtime_state()
    get_agent(agent_id)

    if enabled and not agent_is_trusted(agent_id):
        raise PermissionError(
            f"Agent '{agent_id}' is quarantined until an approved SkillSpector capability record exists"
        )

    execute(
        """
        UPDATE agent_runtime_state
        SET
            enabled = %s,
            paused_reason = CASE
                WHEN %s THEN NULL
                ELSE 'disabled_by_operator'
            END,
            updated_at = NOW()
        WHERE agent_id = %s
        """,
        (
            enabled,
            enabled,
            agent_id,
        ),
    )


def _runtime_enabled(
    agent_id: str,
) -> bool:
    _sync_runtime_state()

    row = fetch_one(
        """
        SELECT enabled
        FROM agent_runtime_state
        WHERE agent_id = %s
        """,
        (agent_id,),
    )

    return bool(
        row and row["enabled"]
    )


def _combined_evidence(
    *,
    agent_id: str,
    environment_id: str | None,
    evidence: list[dict] | None,
) -> list[dict]:
    combined = []

    if environment_id:
        combined.extend(
            get_environment_packet(
                environment_id
            )
        )

    combined.extend(
        knowledge_packet(
            agent_id=agent_id,
            environment_id=environment_id,
        )
    )

    combined.extend(
        evidence or []
    )

    return normalise_evidence(
        combined
    )


def _build_task_message(
    *,
    task: str,
    evidence: list[dict],
) -> str:
    evidence_text = json.dumps(
        evidence,
        ensure_ascii=False,
        indent=2,
        default=str,
    )

    return (
        "TASK\n"
        f"{task}\n\n"
        "CONTEXT AND EVIDENCE\n"
        f"{evidence_text}\n\n"
        "Environment context describes the managed environment but is not "
        "proof of current live state. Persistent agent memory may be unverified. "
        "Ask focused questions when missing information could materially change "
        "the diagnosis or recommendation. Never fill those gaps by invention. "
        "Do not claim that recommendations were executed."
    )


def _run_single_agent(
    *,
    agent_id: str,
    task: str,
    evidence: list[dict] | None,
    trigger_type: str,
    parent_run_id: str | None = None,
    conversation_id: str | None = None,
    environment_id: str | None = DEFAULT_ENVIRONMENT_ID,
) -> dict:
    ensure_agent_schema()

    agent = get_agent(agent_id)

    if not _runtime_enabled(agent_id):
        raise PermissionError(
            f"Agent '{agent_id}' is disabled"
        )

    cleaned_evidence = _combined_evidence(
        agent_id=agent_id,
        environment_id=environment_id,
        evidence=evidence,
    )

    run_id = str(uuid.uuid4())

    execute(
        """
        INSERT INTO agent_runs (
            id,
            parent_run_id,
            conversation_id,
            environment_id,
            agent_id,
            status,
            trigger_type,
            task,
            evidence_json
        )
        VALUES (
            %s, %s, %s, %s, %s,
            'running', %s, %s, %s::jsonb
        )
        """,
        (
            run_id,
            parent_run_id,
            conversation_id,
            environment_id,
            agent_id,
            trigger_type,
            task,
            json.dumps(cleaned_evidence),
        ),
    )

    if (
        agent.evidence_requirement
        == EvidenceRequirement.REQUIRED
        and not cleaned_evidence
    ):
        message = (
            "Agent requires evidence but no evidence "
            "packet was supplied."
        )

        _record_concern(
            run_id=run_id,
            source_agent_id=agent_id,
            severity="critical",
            category="missing_required_evidence",
            message=message,
        )

        execute(
            """
            UPDATE agent_runs
            SET
                status = 'blocked',
                finished_at = NOW(),
                error_message = %s
            WHERE id = %s
            """,
            (
                message,
                run_id,
            ),
        )

        return {
            "run_id": run_id,
            "agent_id": agent_id,
            "status": "blocked",
            "output": None,
            "concerns": [
                {
                    "severity": "critical",
                    "category":
                        "missing_required_evidence",
                    "message": message,
                    "evidence_refs": [],
                }
            ],
        }

    state = get_state()
    model = selected_model()

    allow_openai = (
        state["state"]
        == AIState.ENABLED.value
        and bool(model)
    )

    started = time.monotonic()

    try:
        routed = route_request(
            message=_build_task_message(
                task=task,
                evidence=cleaned_evidence,
            ),
            conversation_context=None,
            conversation_id=conversation_id,
            openai_model=model,
            allow_openai=allow_openai,
            system_prompt=build_agent_prompt(
                agent
            ),
        )

        output = extract_json_object(
            routed["text"]
        )

        concerns = deterministic_concerns(
            output=output,
            evidence=cleaned_evidence,
        )

        for concern in concerns:
            _record_concern(
                run_id=run_id,
                source_agent_id=agent_id,
                severity=concern["severity"],
                category=concern["category"],
                message=concern["message"],
                evidence_refs=concern.get(
                    "evidence_refs"
                ),
            )

        duration_ms = int(
            (time.monotonic() - started)
            * 1000
        )

        status = (
            "success_with_concerns"
            if concerns
            else "success"
        )

        execute(
            """
            UPDATE agent_runs
            SET
                status = %s,
                output_json = %s::jsonb,
                provider = %s,
                model = %s,
                usage_event_id = %s,
                route_metadata = %s::jsonb,
                finished_at = NOW(),
                duration_ms = %s
            WHERE id = %s
            """,
            (
                status,
                json.dumps(output),
                routed.get("provider"),
                routed.get("model"),
                routed.get("usage_event_id"),
                json.dumps(
                    routed.get("provider_metadata") or {}
                ),
                duration_ms,
                run_id,
            ),
        )

        execute(
            """
            UPDATE agent_runtime_state
            SET
                last_run_at = NOW(),
                consecutive_failures = 0,
                updated_at = NOW()
            WHERE agent_id = %s
            """,
            (agent_id,),
        )

        summary = str(
            output.get("summary") or ""
        ).strip()

        if summary:
            remember_agent_note(
                agent_id=agent_id,
                content=summary,
                environment_id=environment_id,
                kind="run_summary",
                source_run_id=run_id,
                source_agent_id=agent_id,
                verified=False,
            )

        return {
            "run_id": run_id,
            "agent_id": agent_id,
            "status": status,
            "provider":
                routed.get("provider"),
            "model":
                routed.get("model"),
            "usage_event_id":
                routed.get("usage_event_id"),
            "provider_metadata":
                routed.get("provider_metadata") or {},
            "output": output,
            "concerns": concerns,
        }

    except RouterExhausted as exc:
        error = json.dumps(
            exc.errors
        )[:4000]

    except Exception as exc:
        error = str(exc)[:4000]

    duration_ms = int(
        (time.monotonic() - started)
        * 1000
    )

    _record_concern(
        run_id=run_id,
        source_agent_id=agent_id,
        severity="critical",
        category="agent_execution_failure",
        message=error,
    )

    execute(
        """
        UPDATE agent_runs
        SET
            status = 'failed',
            finished_at = NOW(),
            duration_ms = %s,
            error_message = %s
        WHERE id = %s
        """,
        (
            duration_ms,
            error,
            run_id,
        ),
    )

    execute(
        """
        UPDATE agent_runtime_state
        SET
            last_run_at = NOW(),
            consecutive_failures =
                consecutive_failures + 1,
            updated_at = NOW()
        WHERE agent_id = %s
        """,
        (agent_id,),
    )

    return {
        "run_id": run_id,
        "agent_id": agent_id,
        "status": "failed",
        "output": None,
        "concerns": [
            {
                "severity": "critical",
                "category":
                    "agent_execution_failure",
                "message": error,
                "evidence_refs": [],
            }
        ],
    }


def _sentinel_mode() -> str:
    value = os.getenv(
        "BOUND_SENTINEL_MODE",
        "always",
    ).strip().lower()

    if value not in {
        "always",
        "concerns",
        "off",
    }:
        return "always"

    return value


def run_agent(
    *,
    agent_id: str,
    task: str,
    evidence: list[dict] | None = None,
    trigger_type: str = "manual",
    with_oversight: bool = True,
    conversation_id: str | None = None,
    environment_id: str | None = DEFAULT_ENVIRONMENT_ID,
) -> dict:
    result = _run_single_agent(
        agent_id=agent_id,
        task=task,
        evidence=evidence,
        trigger_type=trigger_type,
        conversation_id=conversation_id,
        environment_id=environment_id,
    )

    if (
        not with_oversight
        or agent_id == "sentinel"
        or result["status"]
        in {"blocked", "failed"}
    ):
        result["sentinel"] = None
        return result

    mode = _sentinel_mode()

    should_review = (
        mode == "always"
        or (
            mode == "concerns"
            and bool(result["concerns"])
        )
    )

    if not should_review:
        result["sentinel"] = None
        return result

    sentinel_evidence = normalise_evidence(
        evidence
    )

    sentinel_evidence.append({
        "ref": "specialist-output",
        "source": agent_id,
        "content": json.dumps(
            result["output"],
            ensure_ascii=False,
        ),
    })

    sentinel_task = (
        "Review the specialist output for unsupported "
        "claims, contradictions, unsafe certainty, "
        "missing evidence, weak causal reasoning and "
        "recommendations that exceed the evidence. "
        "Identify questions that should be asked before "
        "BOUND becomes more confident."
    )

    sentinel = _run_single_agent(
        agent_id="sentinel",
        task=sentinel_task,
        evidence=sentinel_evidence,
        trigger_type="oversight",
        parent_run_id=result["run_id"],
        conversation_id=conversation_id,
        environment_id=environment_id,
    )

    result["sentinel"] = sentinel

    if sentinel.get("output"):
        for message in (
            sentinel["output"].get(
                "concerns"
            ) or []
        ):
            _record_concern(
                run_id=result["run_id"],
                source_agent_id="sentinel",
                severity="warning",
                category="sentinel_review",
                message=str(message),
                evidence_refs=[
                    "specialist-output"
                ],
            )

    return result


def recent_runs(
    limit: int = 100,
) -> list[dict]:
    ensure_agent_schema()

    rows = fetch_all(
        """
        SELECT *
        FROM agent_runs
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    return [
        dict(row)
        for row in rows
    ]


def conversation_runs(
    conversation_id: str,
    limit: int = 200,
) -> list[dict]:
    ensure_agent_schema()

    rows = fetch_all(
        """
        SELECT *
        FROM agent_runs
        WHERE conversation_id = %s
        ORDER BY started_at ASC
        LIMIT %s
        """,
        (
            conversation_id,
            limit,
        ),
    )

    return [
        dict(row)
        for row in rows
    ]


def open_concerns(
    limit: int = 100,
) -> list[dict]:
    ensure_agent_schema()

    rows = fetch_all(
        """
        SELECT *
        FROM agent_concerns
        WHERE resolved = FALSE
        ORDER BY
            CASE severity
                WHEN 'critical' THEN 0
                WHEN 'warning' THEN 1
                ELSE 2
            END,
            created_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    return [
        dict(row)
        for row in rows
    ]
