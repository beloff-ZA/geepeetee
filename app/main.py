import os
import socket
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from app.db.database import fetch_all
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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
        include_sensitive=True,
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
