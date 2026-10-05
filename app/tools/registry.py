import hashlib
import json
import logging
from typing import Any

from app.security.policy import ToolPolicy
from app.security.approvals import (
    create_approval,
    validate_approval,
)
from app.security.audit import log_tool_event
from app.tools.base import ToolDefinition


logger = logging.getLogger("bound.tools")


TOOLS: dict[str, ToolDefinition] = {}


class ToolBlocked(Exception):
    pass


class ApprovalRequired(Exception):
    def __init__(self, approval_id: str):
        self.approval_id = approval_id
        super().__init__(
            f"Approval required: {approval_id}"
        )


def register_tool(
    *,
    name: str,
    description: str,
    policy: ToolPolicy,
):
    def decorator(func):
        if name in TOOLS:
            raise RuntimeError(
                f"Tool already registered: {name}"
            )

        TOOLS[name] = ToolDefinition(
            name=name,
            handler=func,
            policy=policy,
            description=description,
        )

        return func

    return decorator


def hash_arguments(arguments: dict) -> str:
    serialized = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        serialized.encode()
    ).hexdigest()


def _safe_summary(result: Any) -> str:
    """
    Produce a short audit summary without dumping
    entire tool output into PostgreSQL.
    """

    try:
        text = json.dumps(
            result,
            default=str,
            ensure_ascii=False,
        )
    except Exception:
        text = str(result)

    return text[:2000]


def execute_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    environment: str,
    approval_id: str | None = None,
):
    tool = TOOLS.get(tool_name)

    if not tool:
        log_tool_event(
            tool_name=tool_name,
            environment=environment,
            target=arguments.get("target"),
            risk="unknown",
            status="blocked",
            error_message="Unknown tool",
        )

        raise ToolBlocked(
            f"Unknown tool: {tool_name}"
        )

    policy = tool.policy
    target = arguments.get("target")
    args_hash = hash_arguments(arguments)

    if not policy.enabled:
        log_tool_event(
            tool_name=tool_name,
            environment=environment,
            target=target,
            risk=policy.risk.value,
            status="blocked",
            args_hash=args_hash,
            error_message="Tool disabled",
        )

        raise ToolBlocked(
            f"Tool disabled: {tool_name}"
        )

    if (
        policy.allowed_environments
        and environment not in policy.allowed_environments
    ):
        log_tool_event(
            tool_name=tool_name,
            environment=environment,
            target=target,
            risk=policy.risk.value,
            status="blocked",
            args_hash=args_hash,
            error_message=(
                f"Environment not allowed: {environment}"
            ),
        )

        raise ToolBlocked(
            f"Tool '{tool_name}' not allowed "
            f"in environment '{environment}'"
        )

    if policy.requires_target and not target:
        log_tool_event(
            tool_name=tool_name,
            environment=environment,
            target=None,
            risk=policy.risk.value,
            status="blocked",
            args_hash=args_hash,
            error_message="Target is required",
        )

        raise ToolBlocked(
            "Target is required"
        )

    targets = arguments.get("targets")

    if isinstance(targets, list):
        if len(targets) > policy.max_targets:
            log_tool_event(
                tool_name=tool_name,
                environment=environment,
                target=target,
                risk=policy.risk.value,
                status="blocked",
                args_hash=args_hash,
                error_message=(
                    f"Too many targets: {len(targets)}"
                ),
            )

            raise ToolBlocked(
                f"Too many targets. "
                f"Maximum allowed: {policy.max_targets}"
            )

    if not policy.allow_arbitrary_input:
        forbidden_keys = {
            "command",
            "shell",
            "powershell",
            "bash",
            "python",
            "script",
            "sql_raw",
        }

        supplied = forbidden_keys.intersection(
            arguments.keys()
        )

        if supplied:
            message = (
                "Arbitrary execution input blocked: "
                + ", ".join(sorted(supplied))
            )

            log_tool_event(
                tool_name=tool_name,
                environment=environment,
                target=target,
                risk=policy.risk.value,
                status="blocked",
                args_hash=args_hash,
                error_message=message,
            )

            raise ToolBlocked(message)

    if policy.approval_required:
        if not approval_id:
            approval = create_approval(
                tool_name=tool_name,
                environment=environment,
                target=target,
                args_hash=args_hash,
            )

            approval_value = (
                approval["id"]
                if isinstance(approval, dict)
                else approval.id
            )

            log_tool_event(
                tool_name=tool_name,
                environment=environment,
                target=target,
                risk=policy.risk.value,
                status="approval_required",
                args_hash=args_hash,
                approval_id=approval_value,
            )

            raise ApprovalRequired(
                approval_value
            )

        if not validate_approval(
            approval_id=approval_id,
            tool_name=tool_name,
            environment=environment,
            target=target,
            args_hash=args_hash,
        ):
            log_tool_event(
                tool_name=tool_name,
                environment=environment,
                target=target,
                risk=policy.risk.value,
                status="blocked",
                args_hash=args_hash,
                approval_id=approval_id,
                error_message=(
                    "Invalid or expired approval"
                ),
            )

            raise ToolBlocked(
                "Invalid or expired approval"
            )

    log_tool_event(
        tool_name=tool_name,
        environment=environment,
        target=target,
        risk=policy.risk.value,
        status="started",
        args_hash=args_hash,
        approval_id=approval_id,
    )

    logger.info(
        "Executing tool=%s environment=%s target=%s",
        tool_name,
        environment,
        target,
    )

    try:
        result = tool.handler(**arguments)

    except Exception as exc:
        log_tool_event(
            tool_name=tool_name,
            environment=environment,
            target=target,
            risk=policy.risk.value,
            status="failed",
            args_hash=args_hash,
            approval_id=approval_id,
            error_message=str(exc)[:2000],
        )

        logger.exception(
            "Tool failed tool=%s",
            tool_name,
        )

        raise

    log_tool_event(
        tool_name=tool_name,
        environment=environment,
        target=target,
        risk=policy.risk.value,
        status="success",
        args_hash=args_hash,
        approval_id=approval_id,
        result_summary=_safe_summary(result),
    )

    logger.info(
        "Tool completed tool=%s",
        tool_name,
    )

    return result
