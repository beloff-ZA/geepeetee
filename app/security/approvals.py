from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.database import (
    fetch_one,
    execute,
)


def _ensure_approval_schema() -> None:
    execute(
        """
        ALTER TABLE approvals
        ADD COLUMN IF NOT EXISTS consumed_at TIMESTAMPTZ
        """
    )


def create_approval(
    tool_name: str,
    environment: str,
    target: str | None,
    args_hash: str,
    ttl_minutes: int = 5,
):
    _ensure_approval_schema()

    approval_id = str(uuid4())

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=ttl_minutes)
    )

    execute(
        """
        INSERT INTO approvals (
            id,
            tool_name,
            environment,
            target,
            args_hash,
            approved,
            expires_at
        )
        VALUES (
            %s, %s, %s, %s, %s, FALSE, %s
        )
        """,
        (
            approval_id,
            tool_name,
            environment,
            target,
            args_hash,
            expires_at,
        ),
    )

    return {
        "id": approval_id,
        "tool_name": tool_name,
        "environment": environment,
        "target": target,
        "args_hash": args_hash,
        "approved": False,
        "expires_at": expires_at,
    }


def approve(
    approval_id: str,
) -> bool:
    _ensure_approval_schema()

    row = fetch_one(
        """
        SELECT *
        FROM approvals
        WHERE id = %s
        """,
        (approval_id,),
    )

    if not row:
        return False

    if row["expires_at"] < datetime.now(timezone.utc):
        return False

    if row.get("consumed_at") is not None:
        return False

    execute(
        """
        UPDATE approvals
        SET
            approved = TRUE,
            approved_at = NOW()
        WHERE id = %s
          AND consumed_at IS NULL
        """,
        (approval_id,),
    )

    return True


def validate_approval(
    approval_id: str,
    tool_name: str,
    environment: str,
    target: str | None,
    args_hash: str,
) -> bool:
    _ensure_approval_schema()

    row = fetch_one(
        """
        SELECT *
        FROM approvals
        WHERE id = %s
        """,
        (approval_id,),
    )

    if not row:
        return False

    if not row["approved"]:
        return False

    if row["expires_at"] < datetime.now(timezone.utc):
        return False

    if row.get("consumed_at") is not None:
        return False

    if row["tool_name"] != tool_name:
        return False

    if row["environment"] != environment:
        return False

    if row["target"] != target:
        return False

    if row["args_hash"] != args_hash:
        return False

    return True


def consume_approval(
    approval_id: str,
    tool_name: str,
    environment: str,
    target: str | None,
    args_hash: str,
) -> bool:
    """
    Atomically consume an approval immediately before execution.

    A consumed approval cannot be replayed, even if the tool later fails.
    """

    _ensure_approval_schema()

    row = fetch_one(
        """
        UPDATE approvals
        SET consumed_at = NOW()
        WHERE id = %s
          AND approved = TRUE
          AND consumed_at IS NULL
          AND expires_at >= NOW()
          AND tool_name = %s
          AND environment = %s
          AND target IS NOT DISTINCT FROM %s
          AND args_hash = %s
        RETURNING id
        """,
        (
            approval_id,
            tool_name,
            environment,
            target,
            args_hash,
        ),
    )

    return bool(row)
