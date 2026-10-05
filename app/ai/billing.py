import os

from openai import OpenAI, RateLimitError

from app.ai.state import (
    billing_restored,
    disable_for_billing,
)


PROBE_MODEL = "gpt-5.5"


def check_billing() -> dict:
    key = os.getenv("OPENAI_API_KEY")

    if not key:
        return {
            "ok": False,
            "billing_available": False,
            "reason": "missing_api_key",
        }

    client = OpenAI(api_key=key)

    try:
        response = client.responses.create(
            model=PROBE_MODEL,
            input="Reply only with: OK",
            max_output_tokens=8,
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
                "ok": True,
                "billing_available": False,
                "state": "disabled_billing",
            }

        raise

    billing_restored()

    return {
        "ok": True,
        "billing_available": True,
        "state":
            "billing_restored_pending_model",
    }
