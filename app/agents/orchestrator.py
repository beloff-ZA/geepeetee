import json

from app.agents.environment import DEFAULT_ENVIRONMENT_ID
from app.agents.runtime import run_agent


NETWORK_TERMS = {
    "vlan", "dhcp", "dns", "wifi", "wi-fi", "wireless", "ssid",
    "switch", "router", "routing", "subnet", "gateway", "firewall",
    "packet", "latency", "uplink", "ethernet", "radius", "nps",
    "access point", "ap ", "network", "bandwidth", "qos", "captive portal",
}

IDENTITY_TERMS = {
    "active directory", "ad ", "domain", "nps", "radius", "authentication",
    "login", "sign in", "account", "password", "group policy", "gpo",
    "google workspace", "2fa", "mfa", "permission", "access",
}

ENDPOINT_TERMS = {
    "ipad", "iphone", "ios", "apple school manager", "asm", "mdm",
    "byod", "endpoint", "device management", "managed device",
    "windows laptop", "chromebook", "enrollment", "profile",
}

HARDWARE_TERMS = {
    "hardware", "disk", "ssd", "nvme", "ram", "cpu", "gpu", "thermal",
    "temperature", "battery", "power", "cable", "port", "nic", "server",
    "ups", "storage", "firmware",
}

SOFTWARE_TERMS = {
    "software", "service", "api", "database", "postgres", "python",
    "fastapi", "windows", "linux", "ubuntu", "driver", "application",
    "code", "script", "config", "configuration", "error", "exception", "log",
}

PHYSICAL_TERMS = {
    "camera", "cctv", "nvr", "biometric", "access control", "retention",
    "surveillance", "hikvision", "recording",
}

SECURITY_TERMS = {
    "security", "password", "credential", "secret", "token", "authentication",
    "authorization", "permission", "firewall", "radius", "nps",
    "active directory", "privilege", "delete", "restart", "disable",
    "enable", "install", "change", "write", "exposure", "breach",
}

ADMIN_TERMS = {
    "admin", "principal", "boss", "coordinator", "meeting", "request",
    "schedule", "calendar", "staff", "teacher", "deadline", "follow up",
    "follow-up", "policy", "procedure", "form", "document", "email",
}

ACCOUNTING_TERMS = {
    "invoice", "quote", "quotation", "expense", "income", "budget", "cost",
    "price", "vat", "statement", "accounting", "markup", "financial",
}

BUSINESS_TERMS = {
    "business", "project", "priority", "workload", "client", "consulting",
    "sponsor", "proposal", "stakeholder", "ownership", "manager", "management",
}

VENDOR_TERMS = {
    "vendor", "supplier", "isp", "contract", "warranty", "renewal",
    "procurement", "quote", "distributor", "support agreement",
}

COMMUNICATION_TERMS = {
    "email", "report", "proposal", "message", "letter", "document",
    "executive summary", "presentation", "procedure", "guide",
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

    Agent composition is policy. It is intentionally not delegated to a model.
    """

    selected = []

    if _contains_any(task, NETWORK_TERMS):
        selected.extend([
            "network",
            "security",
            "alternative_solutions",
        ])

    if _contains_any(task, IDENTITY_TERMS):
        selected.extend([
            "identity",
            "security",
        ])

    if _contains_any(task, ENDPOINT_TERMS):
        selected.extend([
            "endpoint_mdm",
            "security",
        ])

    if _contains_any(task, HARDWARE_TERMS):
        selected.append("hardware")

    if _contains_any(task, SOFTWARE_TERMS):
        selected.append("software")

    if _contains_any(task, PHYSICAL_TERMS):
        selected.extend([
            "physical_systems",
            "network",
            "security",
        ])

    if _contains_any(task, ACCOUNTING_TERMS):
        selected.extend([
            "accounting",
            "business_management",
        ])

    if _contains_any(task, ADMIN_TERMS):
        selected.extend([
            "admin",
            "communications",
        ])

    if _contains_any(task, BUSINESS_TERMS):
        selected.append(
            "business_management"
        )

    if _contains_any(task, VENDOR_TERMS):
        selected.extend([
            "vendor_procurement",
            "business_management",
        ])

    if _contains_any(task, COMMUNICATION_TERMS):
        selected.append(
            "communications"
        )

    if not selected:
        selected.extend([
            "reasoning",
            "problem_solver",
        ])
    else:
        selected.append("reasoning")

    if has_evidence:
        selected.append("evidence")

    # Preserve order and keep routine conversations bounded.
    selected = list(
        dict.fromkeys(selected)
    )

    return selected[:6]


def run_panel(
    *,
    task: str,
    evidence: list[dict] | None = None,
    specialist_ids: list[str] | None = None,
    conversation_id: str | None = None,
    environment_id: str | None = DEFAULT_ENVIRONMENT_ID,
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
            with_oversight=False,
            conversation_id=conversation_id,
            environment_id=environment_id,
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
                },
                ensure_ascii=False,
                default=str,
            ),
        })

    operator_task = (
        "Synthesize the specialist panel for the original task. "
        "Preserve meaningful disagreements and uncertainty. "
        "If missing information could change the answer, ask a short ordered "
        "set of questions before proposing risky changes. "
        "Prefer the safest diagnostic step that reduces uncertainty. "
        "Present alternatives when they are materially different. "
        "Do not claim any recommendation has been executed.\n\n"
        f"Original task: {task}"
    )

    operator = run_agent(
        agent_id="operator",
        task=operator_task,
        evidence=synthesis_evidence,
        trigger_type="panel_synthesis",
        with_oversight=True,
        conversation_id=conversation_id,
        environment_id=environment_id,
    )

    return {
        "task": task,
        "environment_id": environment_id,
        "selected_specialists": selected,
        "specialist_runs": specialist_runs,
        "operator": operator,
        "execution_performed": False,
    }


def panel_answer_text(
    panel: dict,
) -> str:
    operator = panel.get("operator") or {}
    output = operator.get("output") or {}

    if not output:
        return (
            "The agent team could not produce a reliable synthesis. "
            "Review the individual agent activity for the failure details."
        )

    parts = []

    summary = str(
        output.get("summary") or ""
    ).strip()

    if summary:
        parts.append(summary)

    questions = output.get(
        "questions"
    ) or []

    if questions:
        lines = [
            "Questions I need answered:"
        ]

        for index, item in enumerate(
            questions,
            start=1,
        ):
            if isinstance(item, dict):
                question = item.get(
                    "question"
                )
                why = item.get(
                    "why_it_matters"
                )

                line = (
                    f"{index}. {question}"
                    if question
                    else ""
                )

                if line and why:
                    line += (
                        f" ({why})"
                    )

                if line:
                    lines.append(line)
            else:
                lines.append(
                    f"{index}. {item}"
                )

        if len(lines) > 1:
            parts.append(
                "\n".join(lines)
            )

    recommendations = output.get(
        "recommendations"
    ) or []

    if recommendations:
        lines = [
            "Recommended next steps:"
        ]

        for index, item in enumerate(
            recommendations,
            start=1,
        ):
            if isinstance(item, dict):
                action = item.get(
                    "action"
                )
                reason = item.get(
                    "reason"
                )

                line = (
                    f"{index}. {action}"
                    if action
                    else ""
                )

                if line and reason:
                    line += (
                        f" — {reason}"
                    )

                if line:
                    lines.append(line)
            else:
                lines.append(
                    f"{index}. {item}"
                )

        if len(lines) > 1:
            parts.append(
                "\n".join(lines)
            )

    concerns = []

    sentinel = operator.get(
        "sentinel"
    ) or {}

    sentinel_output = sentinel.get(
        "output"
    ) or {}

    concerns.extend(
        sentinel_output.get(
            "concerns"
        ) or []
    )

    if concerns:
        lines = [
            "Oversight concerns:"
        ]

        for item in concerns:
            lines.append(
                f"- {item}"
            )

        parts.append(
            "\n".join(lines)
        )

    confidence = output.get(
        "confidence"
    )

    if confidence is not None:
        parts.append(
            f"Team confidence: {confidence}"
        )

    return "\n\n".join(
        part
        for part in parts
        if part
    )
