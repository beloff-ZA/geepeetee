from app.ai.router import (
    RouterExhausted,
    route_request,
)
from app.ai.state import (
    AIState,
    get_state,
    selected_model,
)


def ask_bound(
    message: str,
    conversation_context: list[dict] | None = None,
    conversation_id: str | None = None,
) -> dict:
    state = get_state()

    if state["state"] == AIState.DISABLED_MANUAL.value:
        return {
            "response_id": None,
            "provider": None,
            "model": state["selected_model"],
            "text": (
                "AI inference is manually disabled. "
                "BOUND Core remains online."
            ),
            "ai_enabled": False,
            "state": state["state"],
        }

    model = selected_model()

    allow_openai = (
        state["state"] == AIState.ENABLED.value
        and bool(model)
    )

    try:
        result = route_request(
            message=message,
            conversation_context=conversation_context,
            conversation_id=conversation_id,
            openai_model=model,
            allow_openai=allow_openai,
        )

    except RouterExhausted as exc:
        return {
            "response_id": None,
            "provider": None,
            "model": None,
            "text": (
                "No configured AI provider completed "
                "the request."
            ),
            "ai_enabled": False,
            "state": "providers_unavailable",
            "provider_errors": exc.errors,
        }

    runtime_state = (
        "enabled"
        if (
            result["provider"] == "openai"
            and not result["fallback"]
        )
        else "degraded"
    )

    return {
        **result,
        "ai_enabled": True,
        "state": runtime_state,
    }
