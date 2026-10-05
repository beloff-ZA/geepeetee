from enum import Enum

from app.db.database import (
    fetch_one,
    execute,
)


class AIState(str, Enum):
    ENABLED = "enabled"
    DISABLED_MANUAL = "disabled_manual"
    DISABLED_BILLING = "disabled_billing"
    BILLING_RESTORED_PENDING_MODEL = (
        "billing_restored_pending_model"
    )


def get_state() -> dict:
    row = fetch_one(
        """
        SELECT
            state,
            selected_model,
            last_error,
            updated_at
        FROM ai_runtime_state
        WHERE id = 1
        """
    )

    if not row:
        raise RuntimeError(
            "AI runtime state is missing"
        )

    return dict(row)


def set_state(
    state: AIState,
    *,
    error: str | None = None,
) -> None:
    execute(
        """
        UPDATE ai_runtime_state
        SET
            state = %s,
            last_error = %s,
            updated_at = NOW()
        WHERE id = 1
        """,
        (
            state.value,
            error,
        ),
    )


def select_model(model: str) -> None:
    execute(
        """
        UPDATE ai_runtime_state
        SET
            state = %s,
            selected_model = %s,
            last_error = NULL,
            updated_at = NOW()
        WHERE id = 1
        """,
        (
            AIState.ENABLED.value,
            model,
        ),
    )


def selected_model() -> str | None:
    row = fetch_one(
        """
        SELECT selected_model
        FROM ai_runtime_state
        WHERE id = 1
        """
    )

    if not row:
        return None

    return row["selected_model"]


def disable_for_billing(
    error: str,
) -> None:
    execute(
        """
        UPDATE ai_runtime_state
        SET
            state = %s,
            last_error = %s,
            updated_at = NOW()
        WHERE id = 1
        """,
        (
            AIState.DISABLED_BILLING.value,
            error,
        ),
    )


def billing_restored() -> None:
    execute(
        """
        UPDATE ai_runtime_state
        SET
            state = %s,
            selected_model = NULL,
            last_error = NULL,
            updated_at = NOW()
        WHERE id = 1
        """,
        (
            AIState.BILLING_RESTORED_PENDING_MODEL.value,
        ),
    )


def disable_manual() -> None:
    execute(
        """
        UPDATE ai_runtime_state
        SET
            state = %s,
            selected_model = NULL,
            last_error = NULL,
            updated_at = NOW()
        WHERE id = 1
        """,
        (
            AIState.DISABLED_MANUAL.value,
        ),
    )
