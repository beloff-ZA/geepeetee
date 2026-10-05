from app.db.database import execute


def log_tool_event(
    *,
    tool_name: str,
    environment: str,
    target: str | None,
    risk: str,
    status: str,
    args_hash: str | None = None,
    approval_id: str | None = None,
    result_summary: str | None = None,
    error_message: str | None = None,
):
    execute(
        """
        INSERT INTO tool_audit_log (
            tool_name,
            environment,
            target,
            risk,
            approval_id,
            status,
            args_hash,
            result_summary,
            error_message
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        """,
        (
            tool_name,
            environment,
            target,
            risk,
            approval_id,
            status,
            args_hash,
            result_summary,
            error_message,
        ),
    )
