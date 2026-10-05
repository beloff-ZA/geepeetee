import os

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

from app.ai.prompt import SYSTEM_PROMPT
from app.ai.state import (
    AIState,
    get_state,
    selected_model,
    disable_for_billing,
)


load_dotenv("/opt/bound/.env")


def ask_bound(message: str) -> dict:
    state = get_state()

    if state["state"] != AIState.ENABLED.value:
        return {
            "response_id": None,
            "model": state["selected_model"],
            "text": (
                "AI inference is not currently enabled. "
                f"Current state: {state['state']}."
            ),
            "ai_enabled": False,
            "state": state["state"],
        }

    model = selected_model()

    if not model:
        return {
            "response_id": None,
            "model": None,
            "text": (
                "AI inference requires a model selection."
            ),
            "ai_enabled": False,
            "state":
                "billing_restored_pending_model",
        }

    key = os.getenv("OPENAI_API_KEY")

    if not key:
        return {
            "response_id": None,
            "model": model,
            "text": "OpenAI API key is missing.",
            "ai_enabled": False,
        }

    client = OpenAI(api_key=key)

    try:
        response = client.responses.create(
            model=model,
            instructions=SYSTEM_PROMPT,
            input=message,
        )

    except RateLimitError as exc:
        error = str(exc)

        if (
            "insufficient_quota" in error
            or "credit_balance_exhausted" in error
            or "no credits remaining" in error.lower()
        ):
            disable_for_billing(error)

            return {
                "response_id": None,
                "model": model,
                "text": (
                    "OpenAI billing is unavailable. "
                    "BOUND disabled AI inference."
                ),
                "ai_enabled": False,
                "state": "disabled_billing",
            }

        raise

    return {
        "response_id": response.id,
        "model": model,
        "text": response.output_text,
        "ai_enabled": True,
        "state": "enabled",
    }
