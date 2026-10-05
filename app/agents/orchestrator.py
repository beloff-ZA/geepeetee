import json

from app.agents.runtime import run_agent


HARDWARE_TERMS = {
    "hardware",
    "disk",
    "ssd",
    "nvme",
    "ram",
    "memory module",
    "cpu",
    "gpu",
    "thermal",
    "temperature",
    "battery",
    "power",
    "cable",
    "port",
    "switch",
    "router",
    "access point",
    "ap ",
    "nic",
    "ethernet",
    "wifi",
    "camera",
    "nvr",
    "server",
}

SOFTWARE_TERMS = {
    "software",
    "service",
    "api",
    "database",
    "postgres",
    "python",
    "fastapi",
    "windows",
    "linux",
    "ubuntu",
    "driver",
    "application",
    "code",
    "script",
    "config",
    "configuration",
    "error",
    "exception",
    "log",
}

SECURITY_TERMS = {
    "password",
    "credential",
    "secret",
    "token",
    "authentication",
    "authorization",
    "permission",
    "firewall",
    "radius",
    "nps",
    "active directory",
    "privilege",
    "delete",
    "restart",
    "disable",
    "enable",
    "install",
    "change",
    "write",
}


def _contains_any(
    text: str,
    terms: set[str],
) -> bool:
    lowered = text.lower()

    return any(
        term in lowered
        for term in terms
    )


def select_specialists(
    *,
    task: str,
    has_evidence: bool,
) -> list[str]:
    """
    Deterministic specialist selection.

    This deliberately does not use an LLM. Agent selection is policy,
    not a creative-writing problem.
    """

    selected = [
        "reasoning",
        "problem_solver",
    ]

    if _contains_any(
        task,
        HARDWARE_TERMS,
    ):
        selected.append("hardware")

    if _contains_any(
        task,
        SOFTWARE_TERMS,
    ):
        selected.append("software")

    if (
        has_evidence
        and _contains_any(
            task,
            SECURITY_TERMS,
        )
    ):
        selected.append("security")

    if has_evidence:
        selected.append("evidence")

    # Keep the panel bounded. Duplicate removal preserves order.
    return list(
        dict.fromkeys(selected)
    )[:6]


def run_panel(
    *,
    task: str,
    evidence: list[dict] | None = None,
    specialist_ids: list[str] | None = None,
) -> dict:
    evidence = evidence or []

    selected = (
        specialist_ids
        if specialist_ids is not None
        else select_specialists(
            task=task,
            has_evidence=bool(evidence),
        )
    )

    specialist_runs = []

    for agent_id in selected:
        if agent_id in {
            "operator",
            "sentinel",
        }:
            continue

        result = run_agent(
            agent_id=agent_id,
            task=task,
            evidence=evidence,
            trigger_type="panel",
            with_oversight=True,
        )

        specialist_runs.append(
            result
        )

    synthesis_evidence = list(evidence)

    for item in specialist_runs:
        synthesis_evidence.append({
            "ref": (
                f"agent-run-{item['run_id']}"
            ),
            "source": item["agent_id"],
            "content": json.dumps(
                {
                    "status": item["status"],
                    "output": item.get("output"),
                    "concerns": item.get(
                        "concerns"
                    ),
                    "sentinel": item.get(
                        "sentinel"
                    ),
                },
                ensure_ascii=False,
            ),
        })

    operator_task = (
        "Synthesize the specialist panel for the original task. "
        "Preserve disagreements and uncertainty. Prefer the safest "
        "test that reduces uncertainty before any write action. "
        "Do not claim any recommendation has been executed.\n\n"
        f"Original task: {task}"
    )

    operator = run_agent(
        agent_id="operator",
        task=operator_task,
        evidence=synthesis_evidence,
        trigger_type="panel_synthesis",
        with_oversight=True,
    )

    return {
        "task": task,
        "selected_specialists": selected,
        "specialist_runs": specialist_runs,
        "operator": operator,
        "execution_performed": False,
    }
