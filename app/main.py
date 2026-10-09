import os
import socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from app.db.database import fetch_all
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.db.conversations import (
    create_conversation,
    get_conversation,
    list_conversations,
    add_message,
    get_messages,
)

load_dotenv("/opt/bound/.env")

# Load/register tools
import app.tools

# AI
from app.ai.client import ask_bound
from app.ai.context import build_conversation_context
from app.ai.router import provider_status
from app.ai.usage import (
    add_usage_feedback,
    ensure_usage_schema,
    recent_usage,
    usage_summary,
    usage_recommendations,
)
from app.ai.state import (
    get_state,
    select_model,
    AIState,
)
from app.ai.models import available_models
from app.ai.billing import check_billing

# Agents
from app.agents.orchestrator import (
    panel_answer_text,
    run_panel,
)
from app.agents.catalog import AGENTS
from app.agents.runtime import (
    list_agents as list_bound_agents,
    open_concerns,
    recent_runs as recent_agent_runs,
    conversation_runs,
    run_agent as run_bound_agent,
    set_agent_enabled,
)
from app.agents.schema import ensure_agent_schema
from app.agents.environment import (
    DEFAULT_ENVIRONMENT_ID,
    add_environment_fact,
    list_environment_facts,
)
from app.agents.knowledge import (
    get_agent_knowledge,
    handover_knowledge,
)
from app.agents.security import (
    propose_action as propose_agent_action,
)
from app.agents.background import (
    background_status,
    enqueue_post_conversation_jobs,
    list_background_jobs,
    set_background_enabled,
    start_background_worker,
    stop_background_worker,
)
from app.agents.documents import (
    get_document,
    list_documents,
)
from app.capabilities.evaluation import record_evaluation
from app.capabilities import (
    BOUND_CAPABILITY_CATALOG,
    onboard_new_agent_definitions,
    approve_capability,
    capability_status,
    get_capability,
    list_capabilities,
    mark_installed,
    register_capability,
    rescan_capability,
)
from app.connectors import (
    EDGE_CAPABILITIES,
    authenticate_edge,
    connector_status,
    ensure_edge_schema,
    get_job as get_edge_job,
    next_job as next_edge_job,
    queue_job as queue_edge_job,
    record_heartbeat as record_edge_heartbeat,
    submit_result as submit_edge_result,
)
from app.inspection.network_inspector import (
    confirm_plan as confirm_inspection_plan,
    execute_plan as execute_inspection_plan,
    list_profiles as list_inspection_profiles,
    preview_plan as preview_inspection_plan,
    recent_results as recent_inspection_results,
)

# Tools
from app.tools.registry import (
    execute_tool,
    TOOLS,
    ToolBlocked,
    ApprovalRequired,
)

# Security
from app.security.approvals import approve


app = FastAPI(
    title="BOUND Operator",
    description="BOUND Core API",
    version="0.1.0",
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)


@app.on_event("startup")
def start_bound_background_worker():
    # New code-defined agents are quarantined and scanned before they can be
    # enabled. Existing bootstrap agents are not re-scanned on every start.
    onboard_new_agent_definitions(
        AGENTS.values()
    )
    ensure_edge_schema()
    start_background_worker()


@app.on_event("shutdown")
def stop_bound_background_worker():
    stop_background_worker()


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):
    message: str = Field(
        min_length=1,
        max_length=20000,
    )

    conversation_id: str | None = None
    use_agents: bool = True
    environment_id: str = DEFAULT_ENVIRONMENT_ID

class ToolRequest(BaseModel):
    tool_name: str
    environment: str
    arguments: dict[str, Any]
    approval_id: str | None = None


class ApprovalRequest(BaseModel):
    approval_id: str


class ModelSelectionRequest(BaseModel):
    model: str


class UsageFeedbackRequest(BaseModel):
    useful: bool
    quality_score: int = Field(
        ge=1,
        le=5,
    )
    note: str | None = Field(
        default=None,
        max_length=2000,
    )


class AgentRunRequest(BaseModel):
    task: str = Field(
        min_length=1,
        max_length=20000,
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
    )
    with_oversight: bool = True


class AgentPanelRequest(BaseModel):
    task: str = Field(
        min_length=1,
        max_length=20000,
    )
    evidence: list[dict[str, Any]] = Field(
        default_factory=list,
    )
    specialist_ids: list[str] | None = None


class AgentEnableRequest(BaseModel):
    enabled: bool


class BackgroundEnableRequest(BaseModel):
    enabled: bool


class CapabilityImportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=40)
    source: str = Field(min_length=1, max_length=2000)
    description: str | None = Field(default=None, max_length=4000)
    provenance: str | None = Field(default=None, max_length=1000)
    requested_capabilities: list[str] = Field(default_factory=list)


class CapabilityApproveRequest(BaseModel):
    approved_by: str = Field(default="operator", min_length=1, max_length=200)


class AgentActionProposalRequest(BaseModel):
    run_id: str
    agent_id: str
    tool_name: str
    environment: str
    arguments: dict[str, Any]
    rationale: str = Field(
        min_length=1,
        max_length=4000,
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
    )


class EnvironmentFactRequest(BaseModel):
    category: str = Field(
        min_length=1,
        max_length=100,
    )
    fact: str = Field(
        min_length=1,
        max_length=8000,
    )
    source: str = Field(
        default="operator",
        max_length=100,
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
    )
    sensitive: bool = False


class AgentHandoverRequest(BaseModel):
    source_agent_id: str
    target_agent_id: str
    knowledge_ids: list[str]


class EdgeHeartbeatRequest(BaseModel):
    connector_id: str = Field(min_length=1, max_length=120)
    environment_id: str = Field(default="school", min_length=1, max_length=120)
    display_name: str = Field(default="School Edge", min_length=1, max_length=200)
    version: str | None = Field(default=None, max_length=80)
    capabilities: list[str] = Field(default_factory=list)


class EdgeResultRequest(BaseModel):
    connector_id: str = Field(min_length=1, max_length=120)
    lease_token: str = Field(min_length=1, max_length=120)
    ok: bool
    result: Any = None
    error: str | None = Field(default=None, max_length=4000)
    observed_at: str | None = None


class EdgeQueueRequest(BaseModel):
    connector_id: str = Field(default="school-edge-01", min_length=1, max_length=120)
    capability: str = Field(min_length=1, max_length=120)
    target: str | None = Field(default=None, max_length=255)
    parameters: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = Field(default="operator", min_length=1, max_length=120)


class InspectorPreviewRequest(BaseModel):
    environment: str = "mbl"
    target: str = Field(
        min_length=1,
        max_length=255,
    )
    transport: str
    profile: str
    operation: str
    ssh_port: int = Field(
        default=22,
        ge=1,
        le=65535,
    )


class InspectorExecuteRequest(BaseModel):
    approval_id: str
    username: str | None = Field(
        default=None,
        max_length=255,
    )
    password: str | None = Field(
        default=None,
        max_length=1000,
    )
    ssh_port: int = Field(
        default=22,
        ge=1,
        le=65535,
    )


# ============================================================
# CORE
# ============================================================

@app.get("/ui", include_in_schema=False)
def operator_ui():
    return FileResponse(
        STATIC_DIR / "index.html"
    )



@app.get("/")
def root():
    state = get_state()

    return {
        "name": "BOUND Operator",
        "version": "0.1.0",
        "status": "online",
        "ai_state": state["state"],
        "model": state["selected_model"],
    }


@app.get("/auth/session")
def auth_session(request: Request):
    """Expose the identity Cloudflare Access authenticated for the UI.

    Cloudflare Access sits in front of the entire public hostname. The origin
    remains bound to localhost, so this endpoint does not implement a second
    authentication system. It only reflects the trusted Access identity header
    when present.
    """

    email = request.headers.get(
        "cf-access-authenticated-user-email"
    )

    return {
        "authenticated": bool(email),
        "email": email,
        "identity_provider": (
            "Google via Cloudflare Access"
            if email
            else "Local origin session"
        ),
        "access_managed": bool(email),
        "logout_url": (
            "/cdn-cgi/access/logout"
            if email
            else None
        ),
    }


@app.get("/health")
def health():
    state = get_state()

    return {
        "status": "healthy",
        "hostname": socket.gethostname(),
        "time": datetime.now(timezone.utc).isoformat(),
        "ai": {
            "state": state["state"],
            "model": state["selected_model"],
            "last_error": state["last_error"],
        },
    }


# ============================================================
# CHAT
# ============================================================

@app.post("/chat")
def chat(request: ChatRequest):
    try:
        conversation_id = request.conversation_id

        # Create a new conversation when none was provided.
        if not conversation_id:

            title = request.message.strip()

            if len(title) > 80:
                title = title[:77] + "..."

            conversation = create_conversation(
                title=title,
            )

            conversation_id = str(
                conversation["id"]
            )

        else:
            conversation = get_conversation(
                conversation_id
            )

            if not conversation:
                raise HTTPException(
                    status_code=404,
                    detail="Conversation not found",
                )

        # Build context before storing the current message so the
        # latest user input is not sent to the model twice.
        conversation_context = build_conversation_context(
            conversation_id
        )

        # Store user message.
        add_message(
            conversation_id=conversation_id,
            role="user",
            content=request.message,
        )

        if request.use_agents:
            conversation_evidence = []

            for index, item in enumerate(
                conversation_context[-10:],
                start=1,
            ):
                conversation_evidence.append({
                    "ref": f"conversation-{index}",
                    "source": (
                        "conversation:"
                        + str(item.get("role"))
                    ),
                    "content": str(
                        item.get("content") or ""
                    ),
                })

            panel = run_panel(
                task=request.message,
                evidence=conversation_evidence,
                conversation_id=conversation_id,
                environment_id=request.environment_id,
            )

            assistant_text = panel_answer_text(
                panel
            )

            operator = (
                panel.get("operator") or {}
            )

            result = {
                "text": assistant_text,
                "provider":
                    operator.get("provider"),
                "model":
                    operator.get("model"),
                "response_id": None,
                "ai_enabled": True,
                "state": "agent_team",
                "agent_team": {
                    "selected_specialists":
                        panel.get(
                            "selected_specialists",
                            [],
                        ),
                    "operator_run_id":
                        operator.get("run_id"),
                    "execution_performed":
                        False,
                },
            }

        else:
            result = ask_bound(
                request.message,
                conversation_context=
                    conversation_context,
                conversation_id=
                    conversation_id,
            )

            assistant_text = result.get(
                "text",
                "",
            )

        # Store BOUND response.
        add_message(
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_text,
            model=result.get("model"),
            response_id=result.get(
                "response_id"
            ),
        )

        if request.use_agents:
            enqueue_post_conversation_jobs(
                conversation_id=conversation_id,
                environment_id=request.environment_id,
            )

        return {
            "ok": True,
            "conversation_id":
                conversation_id,
            **result,
        }

    except HTTPException:
        raise

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )

@app.get("/conversations")
def conversations_list(
    limit: int = 50,
):
    limit = max(
        1,
        min(limit, 200),
    )

    conversations = list_conversations(
        limit
    )

    return {
        "count": len(conversations),
        "conversations": conversations,
    }

@app.get(
    "/conversations/{conversation_id}/agent-activity"
)
def conversation_agent_activity(
    conversation_id: str,
):
    conversation = get_conversation(
        conversation_id
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    runs = conversation_runs(
        conversation_id
    )

    return {
        "conversation_id": conversation_id,
        "count": len(runs),
        "runs": runs,
    }


@app.get(
    "/conversations/{conversation_id}"
)
def conversation_detail(
    conversation_id: str,
):

    conversation = get_conversation(
        conversation_id
    )

    if not conversation:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    messages = get_messages(
        conversation_id
    )

    return {
        "conversation":
            conversation,
        "messages":
            messages,
    }

# ============================================================
# AI STATE
# ============================================================

@app.get("/ai/status")
def ai_status():
    return get_state()


@app.post("/ai/billing/check")
def ai_billing_check():
    """
    Check whether inference billing is available.

    Billing recovery does NOT automatically enable AI.

    Successful recovery moves BOUND into:
    billing_restored_pending_model

    The user must then explicitly select a model.
    """

    try:
        return check_billing()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.get("/ai/models")
def ai_models():
    """
    Return approved models available to the configured
    OpenAI API key, including affordability information.

    Only available after billing has been restored and
    before AI runtime is re-enabled.
    """

    state = get_state()

    if (
        state["state"]
        != AIState.BILLING_RESTORED_PENDING_MODEL.value
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    "Model selection is not currently available."
                ),
                "state": state["state"],
                "required_state":
                    AIState.BILLING_RESTORED_PENDING_MODEL.value,
            },
        )

    try:
        models = available_models()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )

    return {
        "count": len(models),
        "models": models,
    }


@app.post("/ai/model/select")
def ai_model_select(
    request: ModelSelectionRequest,
):
    """
    Explicitly select the model BOUND will use.

    This endpoint only works after billing has been
    verified and BOUND is waiting for model selection.
    """

    state = get_state()

    if (
        state["state"]
        != AIState.BILLING_RESTORED_PENDING_MODEL.value
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message":
                    "BOUND is not awaiting model selection.",
                "state": state["state"],
            },
        )

    try:
        models = available_models()

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )

    allowed_models = {
        model["id"]
        for model in models
    }

    if request.model not in allowed_models:
        raise HTTPException(
            status_code=400,
            detail={
                "message":
                    "Model is unavailable or not approved.",
                "requested_model": request.model,
                "allowed_models":
                    sorted(allowed_models),
            },
        )

    select_model(request.model)

    return {
        "ok": True,
        "state": AIState.ENABLED.value,
        "model": request.model,
    }


# ============================================================
# PROVIDERS / USAGE
# ============================================================

@app.get("/ai/providers")
def ai_providers():
    state = get_state()

    return {
        "provider_order": [
            item["provider"]
            for item in provider_status(
                openai_model=state["selected_model"]
            )
        ],
        "providers": provider_status(
            openai_model=state["selected_model"]
        ),
        "openai_runtime_state": state["state"],
    }


@app.get("/ai/usage")
def ai_usage(
    days: int = 30,
):
    days = max(
        1,
        min(days, 3650),
    )

    ensure_usage_schema()

    return {
        "days": days,
        "shadow_pricing": {
            "currency": "USD",
            "real_billing": False,
            "description": (
                "BOUND comparison-only fictional pricing. "
                "It is not vendor billing."
            ),
        },
        "providers": usage_summary(days),
    }


@app.get("/ai/usage/recommendations")
def ai_usage_recommendations(
    days: int = 30,
):
    days = max(
        1,
        min(days, 3650),
    )

    ensure_usage_schema()

    return usage_recommendations(days)


@app.get("/ai/usage/recent")
def ai_usage_recent(
    limit: int = 100,
):
    limit = max(
        1,
        min(limit, 1000),
    )

    ensure_usage_schema()

    events = recent_usage(limit)

    return {
        "count": len(events),
        "events": events,
    }


@app.post("/ai/usage/{event_id}/feedback")
def ai_usage_feedback(
    event_id: str,
    request: UsageFeedbackRequest,
):
    ensure_usage_schema()

    updated = add_usage_feedback(
        event_id=event_id,
        useful=request.useful,
        quality_score=request.quality_score,
        note=request.note,
    )

    if not updated:
        raise HTTPException(
            status_code=404,
            detail="Usage event not found",
        )

    return {
        "ok": True,
        "event_id": event_id,
    }


# ============================================================
# ENVIRONMENT CONTEXT / PERSISTENT AGENT KNOWLEDGE
# ============================================================

@app.get("/environments/{environment_id}/facts")
def environment_facts(
    environment_id: str,
):
    facts = list_environment_facts(
        environment_id,
        include_sensitive=False,
    )

    return {
        "environment_id": environment_id,
        "count": len(facts),
        "facts": facts,
    }


@app.post("/environments/{environment_id}/facts")
def environment_fact_add(
    environment_id: str,
    request: EnvironmentFactRequest,
):
    try:
        fact = add_environment_fact(
            environment_id=environment_id,
            category=request.category,
            fact=request.fact,
            source=request.source,
            confidence=request.confidence,
            sensitive=request.sensitive,
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    return {
        "ok": True,
        "fact": fact,
    }


@app.get("/agents/{agent_id}/knowledge")
def agent_knowledge(
    agent_id: str,
    environment_id: str = DEFAULT_ENVIRONMENT_ID,
):
    notes = get_agent_knowledge(
        agent_id=agent_id,
        environment_id=environment_id,
    )

    return {
        "agent_id": agent_id,
        "environment_id": environment_id,
        "count": len(notes),
        "knowledge": notes,
    }


@app.post("/agents/handover")
def agent_handover(
    request: AgentHandoverRequest,
):
    handed_over = handover_knowledge(
        source_agent_id=
            request.source_agent_id,
        target_agent_id=
            request.target_agent_id,
        knowledge_ids=
            request.knowledge_ids,
    )

    return {
        "ok": True,
        "source_agent_id":
            request.source_agent_id,
        "target_agent_id":
            request.target_agent_id,
        "count": len(handed_over),
        "knowledge": handed_over,
    }


# ============================================================
# BACKGROUND AGENT WORK / LIVING DOCUMENTATION
# ============================================================

@app.get("/agents/background/status")
def agent_background_status():
    return background_status()


@app.post("/agents/background/enabled")
def agent_background_enabled(
    request: BackgroundEnableRequest,
):
    set_background_enabled(
        request.enabled
    )

    return {
        "ok": True,
        **background_status(),
    }


@app.get("/agents/background/jobs")
def agent_background_jobs(
    limit: int = 100,
):
    limit = max(
        1,
        min(limit, 500),
    )

    jobs = list_background_jobs(
        limit
    )

    return {
        "count": len(jobs),
        "jobs": jobs,
    }


@app.get("/documents")
def documents_list(
    environment_id: str | None = None,
    limit: int = 100,
):
    limit = max(
        1,
        min(limit, 500),
    )

    documents = list_documents(
        environment_id=environment_id,
        limit=limit,
    )

    return {
        "count": len(documents),
        "documents": documents,
    }


@app.get("/documents/{document_id}")
def document_detail(
    document_id: str,
):
    document = get_document(
        document_id
    )

    if not document:
        raise HTTPException(
            status_code=404,
            detail="Document not found",
        )

    return {
        "document": document,
    }


# ============================================================
# SKILLS / AGENTS / MCP CAPABILITY TRUST GATE
# ============================================================

@app.get("/capabilities/status")
def capabilities_status():
    return capability_status()


@app.get("/capabilities/catalog")
def capabilities_catalog():
    return {
        "count": len(BOUND_CAPABILITY_CATALOG),
        "items": BOUND_CAPABILITY_CATALOG,
    }


@app.get("/capabilities")
def capabilities_list(
    kind: str | None = None,
    limit: int = 200,
):
    limit = max(1, min(limit, 500))
    items = list_capabilities(
        kind=kind,
        limit=limit,
    )
    return {
        "count": len(items),
        "items": items,
    }


@app.get("/capabilities/{capability_id}")
def capability_detail(capability_id: str):
    item = get_capability(capability_id)
    if not item:
        raise HTTPException(
            status_code=404,
            detail="Capability not found",
        )
    return {"item": item}


@app.post("/capabilities/import")
def capability_import(
    request: CapabilityImportRequest,
):
    try:
        item = register_capability(
            name=request.name,
            kind=request.kind,
            source=request.source,
            description=request.description,
            provenance=request.provenance,
            requested_capabilities=request.requested_capabilities,
            scan_now=True,
        )
        return {
            "ok": True,
            "item": item,
            "execution_performed": False,
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.post("/capabilities/{capability_id}/rescan")
def capability_rescan(capability_id: str):
    try:
        return {
            "ok": True,
            "item": rescan_capability(capability_id),
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.post("/capabilities/{capability_id}/evaluate")
def capability_evaluate(capability_id: str):
    item = get_capability(capability_id)
    if not item:
        raise HTTPException(
            status_code=404,
            detail="Capability not found",
        )

    if item.get("scan_state") not in {"safe", "caution"}:
        raise HTTPException(
            status_code=409,
            detail=(
                "Capability must complete the SkillSpector gate before "
                "agent evaluation."
            ),
        )

    # Do not feed untrusted source text or scanner excerpts into the model.
    # The evaluation panel sees only declared metadata and deterministic scan facts.
    evidence = [{
        "ref": "capability-metadata",
        "source": "BOUND capability registry",
        "content": (
            f"name={item.get('name')}; kind={item.get('kind')}; "
            f"description={item.get('description') or ''}; "
            f"provenance={item.get('provenance') or ''}; "
            f"requested_capabilities={item.get('requested_capabilities') or []}; "
            f"skillspector_state={item.get('scan_state')}; "
            f"risk_score={item.get('risk_score')}; "
            f"risk_severity={item.get('risk_severity')}; "
            f"recommendation={item.get('recommendation')}; "
            f"analysis_complete={item.get('analysis_complete')}"
        ),
    }]

    task = (
        "Evaluate whether this scanned capability belongs in BOUND Operator. "
        "Assess operational value, overlap with existing agents, least-privilege "
        "tool needs, maintenance burden, failure modes, and the safest integration "
        "boundary. Preserve useful possibilities rather than rejecting novelty. "
        "Recommend exclusion only when the capability is clearly outside BOUND's "
        "IT operations, consulting, infrastructure, research, documentation, "
        "automation or business-support mandate. Do not execute or install anything."
    )

    panel = run_panel(
        task=task,
        evidence=evidence,
        specialist_ids=[
            "security",
            "reasoning",
            "alternative_solutions",
            "business_management",
        ],
        environment_id="core",
    )

    operator = panel.get("operator") or {}
    output = operator.get("output") or {}
    summary = str(output.get("summary") or "").strip() or None
    # Confidence is not a fitness score. Preserve the panel output and leave
    # fitness unset until BOUND has explicit capability eval criteria.
    fit_score = None

    evaluation = record_evaluation(
        capability_id=capability_id,
        status=operator.get("status") or "unknown",
        evaluator_run_id=operator.get("run_id"),
        fit_score=fit_score,
        summary=summary,
        result=panel,
    )

    return {
        "ok": True,
        "evaluation": evaluation,
        "execution_performed": False,
    }


@app.post("/capabilities/{capability_id}/approve")
def capability_approve(
    capability_id: str,
    request: CapabilityApproveRequest,
):
    try:
        return {
            "ok": True,
            "item": approve_capability(
                capability_id,
                approved_by=request.approved_by,
            ),
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )


@app.post("/capabilities/{capability_id}/install")
def capability_install(capability_id: str):
    try:
        return {
            "ok": True,
            "item": mark_installed(capability_id),
            "execution_performed": False,
            "note": (
                "BOUND marks trust state only. Package acquisition and execution "
                "remain separate operator-controlled steps."
            ),
        }
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )


# ============================================================
# AGENTS
# ============================================================

@app.get("/agents")
def agents_list():
    ensure_agent_schema()

    agents = list_bound_agents()

    return {
        "count": len(agents),
        "agents": agents,
        "execution_policy": {
            "agent_direct_execution": False,
            "tool_allowlists": "deny_by_default",
            "write_actions":
                "explicit_human_approval_required",
            "approval_replay":
                "blocked_single_use",
            "sentinel_mode":
                os.getenv(
                    "BOUND_SENTINEL_MODE",
                    "always",
                ),
        },
    }


@app.post("/agents/panel/run")
def agent_panel_run(
    request: AgentPanelRequest,
):
    try:
        return run_panel(
            task=request.task,
            evidence=request.evidence,
            specialist_ids=
                request.specialist_ids,
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.post("/agents/{agent_id}/run")
def agent_run(
    agent_id: str,
    request: AgentRunRequest,
):
    try:
        return run_bound_agent(
            agent_id=agent_id,
            task=request.task,
            evidence=request.evidence,
            trigger_type="manual_api",
            with_oversight=
                request.with_oversight,
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.post("/agents/{agent_id}/enabled")
def agent_enabled(
    agent_id: str,
    request: AgentEnableRequest,
):
    try:
        set_agent_enabled(
            agent_id,
            request.enabled,
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )

    return {
        "ok": True,
        "agent_id": agent_id,
        "enabled": request.enabled,
    }


@app.get("/agents/runs/recent")
def agent_runs_recent(
    limit: int = 100,
):
    limit = max(
        1,
        min(limit, 1000),
    )

    runs = recent_agent_runs(limit)

    return {
        "count": len(runs),
        "runs": runs,
    }


@app.get("/agents/concerns")
def agent_concerns(
    limit: int = 100,
):
    limit = max(
        1,
        min(limit, 1000),
    )

    concerns = open_concerns(limit)

    return {
        "count": len(concerns),
        "concerns": concerns,
    }


@app.post("/agents/actions/propose")
def agent_action_proposal(
    request: AgentActionProposalRequest,
):
    try:
        return propose_agent_action(
            run_id=request.run_id,
            agent_id=request.agent_id,
            tool_name=request.tool_name,
            environment=request.environment,
            arguments=request.arguments,
            rationale=request.rationale,
            evidence_refs=
                request.evidence_refs,
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# ============================================================
# SCHOOL EDGE CONNECTOR
# ============================================================

def _edge_bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    if not value.lower().startswith("bearer "):
        return None
    return value.split(" ", 1)[1].strip()


def _require_edge(request: Request) -> None:
    if not authenticate_edge(_edge_bearer_token(request)):
        raise HTTPException(
            status_code=401,
            detail="Invalid school edge connector token",
        )


@app.get("/connectors/capabilities")
def edge_capabilities():
    return {
        "count": len(EDGE_CAPABILITIES),
        "capabilities": EDGE_CAPABILITIES,
    }


@app.get("/connectors")
def edge_connectors():
    return {
        "count": len(connector_status()),
        "connectors": connector_status(),
    }


@app.post("/connectors/jobs")
def edge_queue_job(request: EdgeQueueRequest):
    try:
        job = queue_edge_job(
            connector_id=request.connector_id,
            environment_id="school",
            capability=request.capability,
            target=request.target,
            parameters=request.parameters,
            requested_by=request.requested_by,
        )
        return {
            "ok": True,
            "job": job,
            "execution_performed": False,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/connectors/jobs/{job_id}")
def edge_job_detail(job_id: str):
    job = get_edge_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Edge job not found")
    return {"job": job}


@app.post("/edge/v1/heartbeat")
def edge_heartbeat(request_body: EdgeHeartbeatRequest, request: Request):
    _require_edge(request)
    return {
        "ok": True,
        "connector": record_edge_heartbeat(
            connector_id=request_body.connector_id,
            environment_id=request_body.environment_id,
            display_name=request_body.display_name,
            version=request_body.version,
            capabilities=request_body.capabilities,
            remote_addr=(request.client.host if request.client else None),
        ),
        "server_capabilities": list(EDGE_CAPABILITIES),
    }


@app.get("/edge/v1/jobs/next")
def edge_next_job(connector_id: str, request: Request):
    _require_edge(request)
    return {
        "job": next_edge_job(connector_id),
    }


@app.post("/edge/v1/jobs/{job_id}/result")
def edge_job_result(
    job_id: str,
    request_body: EdgeResultRequest,
    request: Request,
):
    _require_edge(request)
    try:
        job = submit_edge_result(
            job_id=job_id,
            connector_id=request_body.connector_id,
            lease_token=request_body.lease_token,
            ok=request_body.ok,
            result=request_body.result,
            error=request_body.error,
            observed_at=request_body.observed_at,
        )
        return {"ok": True, "job": job}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))


# ============================================================
# NETWORK INSPECTOR
# ============================================================

@app.get("/inspector/profiles")
def inspector_profiles():
    profiles = list_inspection_profiles()

    return {
        "profiles": profiles,
    }


@app.post("/inspector/preview")
def inspector_preview(
    request: InspectorPreviewRequest,
):
    try:
        return preview_inspection_plan(
            environment=request.environment,
            target=request.target,
            transport=request.transport,
            profile=request.profile,
            operation=request.operation,
            ssh_port=request.ssh_port,
        )

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.post("/inspector/plans/{plan_id}/confirm")
def inspector_confirm(
    plan_id: str,
):
    try:
        return confirm_inspection_plan(
            plan_id
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.post("/inspector/plans/{plan_id}/execute")
def inspector_execute(
    plan_id: str,
    request: InspectorExecuteRequest,
):
    try:
        return execute_inspection_plan(
            plan_id=plan_id,
            approval_id=request.approval_id,
            username=request.username,
            password=request.password,
            ssh_port=request.ssh_port,
        )

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )


@app.get("/inspector/results")
def inspector_results(
    environment: str | None = None,
    limit: int = 50,
):
    limit = max(
        1,
        min(limit, 200),
    )

    results = recent_inspection_results(
        environment=environment,
        limit=limit,
    )

    return {
        "count": len(results),
        "results": results,
    }


# ============================================================
# TOOLS
# ============================================================

@app.get("/tools")
def list_tools():
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "risk": tool.policy.risk.value,
                "enabled": tool.policy.enabled,
                "approval_required":
                    tool.policy.approval_required,
                "allowed_environments":
                    tool.policy.allowed_environments,
            }
            for tool in TOOLS.values()
        ]
    }


@app.post("/tools/execute")
def run_tool(request: ToolRequest):
    try:
        result = execute_tool(
            tool_name=request.tool_name,
            arguments=request.arguments,
            environment=request.environment,
            approval_id=request.approval_id,
        )

        return {
            "ok": True,
            "result": result,
        }

    except ApprovalRequired as exc:
        return {
            "ok": False,
            "status": "approval_required",
            "approval_id": exc.approval_id,
        }

    except ToolBlocked as exc:
        raise HTTPException(
            status_code=403,
            detail=str(exc),
        )

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


@app.post("/tools/approve")
def approve_tool(request: ApprovalRequest):
    success = approve(
        request.approval_id
    )

    if not success:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired approval",
        )

    return {
        "ok": True,
        "approval_id": request.approval_id,
    }

@app.get("/audit")
def audit_log(
    limit: int = 50,
):
    if limit < 1:
        limit = 1

    if limit > 500:
        limit = 500

    rows = fetch_all(
        """
        SELECT
            timestamp,
            tool_name,
            environment,
            target,
            risk,
            approval_id,
            status,
            result_summary,
            error_message
        FROM tool_audit_log
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (limit,),
    )

    return {
        "count": len(rows),
        "events": [
            dict(row)
            for row in rows
        ],
    }
