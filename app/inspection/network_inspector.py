import base64
import hashlib
import json
import shlex
import socket
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import paramiko

from app.db.database import execute, fetch_all, fetch_one
from app.security.approvals import (
    approve,
    consume_approval,
    create_approval,
    validate_approval,
)
from app.security.audit import log_tool_event
from app.tools.registry import hash_arguments


TOOL_NAME = "network_inspector"

PROFILES: dict[str, dict[str, Any]] = {
    "bound_linux": {
        "label": "BOUND / Linux host",
        "transport": "local_or_ssh",
        "operations": {
            "interfaces": {
                "label": "Interfaces and addresses",
                "argv": ["ip", "-brief", "address"],
                "ssh": "ip -brief address",
            },
            "routes": {
                "label": "Routing table",
                "argv": ["ip", "route", "show"],
                "ssh": "ip route show",
            },
            "neighbors": {
                "label": "ARP / neighbour table",
                "argv": ["ip", "neigh", "show"],
                "ssh": "ip neigh show",
            },
            "listening": {
                "label": "Listening sockets",
                "argv": ["ss", "-lntup"],
                "ssh": "ss -lntup",
            },
        },
    },
    "mikrotik_routeros": {
        "label": "MikroTik RouterOS",
        "transport": "ssh",
        "operations": {
            "interfaces": {
                "label": "Interfaces",
                "ssh": "/interface print terse",
            },
            "addresses": {
                "label": "IP addresses",
                "ssh": "/ip address print terse",
            },
            "routes": {
                "label": "Routing table",
                "ssh": "/ip route print terse",
            },
            "bridge_vlans": {
                "label": "Bridge VLAN table",
                "ssh": "/interface bridge vlan print terse",
            },
            "bridge_ports": {
                "label": "Bridge ports",
                "ssh": "/interface bridge port print terse",
            },
            "neighbors": {
                "label": "Layer-2 neighbours",
                "ssh": "/ip neighbor print terse",
            },
            "dhcp_leases": {
                "label": "DHCP leases",
                "ssh": "/ip dhcp-server lease print terse",
            },
        },
    },
    "arista_eos": {
        "label": "Arista EOS / Cisco-like CLI",
        "transport": "ssh",
        "operations": {
            "interfaces": {
                "label": "Interface status",
                "ssh": "show interfaces status",
            },
            "vlans": {
                "label": "VLANs",
                "ssh": "show vlan",
            },
            "routes": {
                "label": "IP routing table",
                "ssh": "show ip route",
            },
            "lldp": {
                "label": "LLDP neighbours",
                "ssh": "show lldp neighbors detail",
            },
            "mac_table": {
                "label": "Dynamic MAC table",
                "ssh": "show mac address-table dynamic",
            },
        },
    },
    "generic_network_cli": {
        "label": "Generic read-only network CLI",
        "transport": "ssh",
        "operations": {
            "interfaces": {
                "label": "Interface summary",
                "ssh": "show interfaces",
            },
            "vlans": {
                "label": "VLAN summary",
                "ssh": "show vlan",
            },
            "routes": {
                "label": "Route summary",
                "ssh": "show ip route",
            },
            "neighbors": {
                "label": "LLDP neighbours",
                "ssh": "show lldp neighbors",
            },
        },
    },
}

ALLOWED_ENVIRONMENTS = {
    "core",
    "home",
    "mbl",
    "client",
}


def ensure_inspector_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS network_inspection_plans (
            id UUID PRIMARY KEY,
            environment TEXT NOT NULL,
            target TEXT NOT NULL,
            transport TEXT NOT NULL,
            profile TEXT NOT NULL,
            operation TEXT NOT NULL,
            command_preview TEXT NOT NULL,
            host_key_fingerprint TEXT,
            approval_id UUID,
            status TEXT NOT NULL DEFAULT 'previewed'
                CHECK (
                    status IN (
                        'previewed',
                        'approved',
                        'executed',
                        'failed',
                        'expired'
                    )
                ),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            executed_at TIMESTAMPTZ
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS network_inspection_results (
            id UUID PRIMARY KEY,
            plan_id UUID NOT NULL
                REFERENCES network_inspection_plans(id)
                ON DELETE CASCADE,
            environment TEXT NOT NULL,
            target TEXT NOT NULL,
            profile TEXT NOT NULL,
            operation TEXT NOT NULL,
            command_preview TEXT NOT NULL,
            returncode INTEGER,
            stdout TEXT,
            stderr TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_network_inspection_results_created
        ON network_inspection_results(environment, created_at DESC)
        """
    )


def list_profiles() -> dict:
    return {
        profile_id: {
            "label": profile["label"],
            "transport": profile["transport"],
            "operations": {
                operation_id: {
                    "label": operation["label"],
                }
                for operation_id, operation
                in profile["operations"].items()
            },
        }
        for profile_id, profile
        in PROFILES.items()
    }


def _operation_definition(
    profile: str,
    operation: str,
) -> dict:
    profile_data = PROFILES.get(
        profile
    )

    if not profile_data:
        raise ValueError(
            f"Unknown inspector profile: {profile}"
        )

    operation_data = (
        profile_data["operations"].get(
            operation
        )
    )

    if not operation_data:
        raise ValueError(
            f"Unknown operation '{operation}' "
            f"for profile '{profile}'"
        )

    return operation_data


def _ssh_fingerprint(
    target: str,
    port: int,
    timeout: int = 5,
) -> str:
    sock = socket.create_connection(
        (target, port),
        timeout=timeout,
    )

    transport = paramiko.Transport(
        sock
    )

    try:
        transport.start_client(
            timeout=timeout
        )

        key = (
            transport.get_remote_server_key()
        )

        digest = hashlib.sha256(
            key.asbytes()
        ).digest()

        return (
            "SHA256:"
            + base64.b64encode(
                digest
            ).decode().rstrip("=")
        )

    finally:
        transport.close()
        sock.close()


def preview_plan(
    *,
    environment: str,
    target: str,
    transport: str,
    profile: str,
    operation: str,
    ssh_port: int = 22,
) -> dict:
    ensure_inspector_schema()

    if environment not in ALLOWED_ENVIRONMENTS:
        raise ValueError(
            f"Unsupported environment: {environment}"
        )

    if transport not in {
        "local",
        "ssh",
    }:
        raise ValueError(
            "Transport must be local or ssh"
        )

    operation_data = _operation_definition(
        profile,
        operation,
    )

    profile_data = PROFILES[profile]

    allowed_transport = (
        profile_data["transport"]
    )

    if (
        transport == "local"
        and allowed_transport
        not in {"local", "local_or_ssh"}
    ):
        raise ValueError(
            "Profile does not support local execution"
        )

    if (
        transport == "ssh"
        and allowed_transport
        not in {"ssh", "local_or_ssh"}
    ):
        raise ValueError(
            "Profile does not support SSH"
        )

    if transport == "local":
        command_preview = shlex.join(
            operation_data["argv"]
        )

        if target not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError(
                "Local transport target must be localhost"
            )

        fingerprint = None

    else:
        command_preview = (
            operation_data["ssh"]
        )

        if (
            ssh_port < 1
            or ssh_port > 65535
        ):
            raise ValueError(
                "Invalid SSH port"
            )

        fingerprint = _ssh_fingerprint(
            target,
            ssh_port,
        )

    plan_id = str(uuid.uuid4())

    expires_at = (
        datetime.now(timezone.utc)
        + timedelta(minutes=10)
    )

    execute(
        """
        INSERT INTO network_inspection_plans (
            id,
            environment,
            target,
            transport,
            profile,
            operation,
            command_preview,
            host_key_fingerprint,
            expires_at
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        """,
        (
            plan_id,
            environment,
            target,
            transport,
            profile,
            operation,
            command_preview,
            fingerprint,
            expires_at,
        ),
    )

    return {
        "plan_id": plan_id,
        "environment": environment,
        "target": target,
        "transport": transport,
        "profile": profile,
        "operation": operation,
        "command": command_preview,
        "host_key_fingerprint":
            fingerprint,
        "credential_required":
            transport == "ssh",
        "expires_at": expires_at,
    }


def _plan_arguments(
    plan: dict,
) -> dict:
    return {
        "plan_id": str(plan["id"]),
        "transport": plan["transport"],
        "profile": plan["profile"],
        "operation": plan["operation"],
        "command": plan[
            "command_preview"
        ],
        "host_key_fingerprint":
            plan.get(
                "host_key_fingerprint"
            ),
    }


def confirm_plan(
    plan_id: str,
) -> dict:
    ensure_inspector_schema()

    plan = fetch_one(
        """
        SELECT *
        FROM network_inspection_plans
        WHERE id = %s
        """,
        (plan_id,),
    )

    if not plan:
        raise KeyError(
            "Inspection plan not found"
        )

    if (
        plan["expires_at"]
        < datetime.now(timezone.utc)
    ):
        execute(
            """
            UPDATE network_inspection_plans
            SET status = 'expired'
            WHERE id = %s
            """,
            (plan_id,),
        )

        raise ValueError(
            "Inspection plan expired"
        )

    if plan["status"] != "previewed":
        raise ValueError(
            "Inspection plan is not awaiting confirmation"
        )

    args_hash = hash_arguments(
        _plan_arguments(
            dict(plan)
        )
    )

    approval = create_approval(
        tool_name=TOOL_NAME,
        environment=plan["environment"],
        target=plan["target"],
        args_hash=args_hash,
        ttl_minutes=5,
    )

    approval_id = str(
        approval["id"]
    )

    if not approve(
        approval_id
    ):
        raise RuntimeError(
            "Could not approve inspection plan"
        )

    execute(
        """
        UPDATE network_inspection_plans
        SET
            approval_id = %s,
            status = 'approved'
        WHERE id = %s
        """,
        (
            approval_id,
            plan_id,
        ),
    )

    log_tool_event(
        tool_name=TOOL_NAME,
        environment=plan["environment"],
        target=plan["target"],
        risk="read",
        status="approval_required",
        args_hash=args_hash,
        approval_id=approval_id,
        result_summary=(
            "Operator confirmed read-only command preview"
        ),
    )

    return {
        "plan_id": plan_id,
        "approval_id": approval_id,
        "status": "approved",
        "credential_required":
            plan["transport"] == "ssh",
    }


def _execute_local(
    plan: dict,
) -> dict:
    operation_data = _operation_definition(
        plan["profile"],
        plan["operation"],
    )

    result = subprocess.run(
        operation_data["argv"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    return {
        "returncode":
            result.returncode,
        "stdout":
            result.stdout[-30000:],
        "stderr":
            result.stderr[-8000:],
    }


def _execute_ssh(
    plan: dict,
    *,
    username: str,
    password: str,
    ssh_port: int,
) -> dict:
    observed_fingerprint = (
        _ssh_fingerprint(
            plan["target"],
            ssh_port,
        )
    )

    expected_fingerprint = (
        plan.get(
            "host_key_fingerprint"
        )
    )

    if (
        not expected_fingerprint
        or observed_fingerprint
        != expected_fingerprint
    ):
        raise RuntimeError(
            "SSH host key changed since preview; execution blocked"
        )

    operation_data = _operation_definition(
        plan["profile"],
        plan["operation"],
    )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(
        paramiko.RejectPolicy()
    )

    class _PinnedHostKeys:
        pass

    # We verify the key ourselves immediately above, then connect with
    # a transport pinned to that host for this single execution.
    sock = socket.create_connection(
        (plan["target"], ssh_port),
        timeout=8,
    )

    transport = paramiko.Transport(
        sock
    )

    try:
        transport.start_client(
            timeout=8
        )

        actual_key = (
            transport.get_remote_server_key()
        )

        actual_digest = (
            "SHA256:"
            + base64.b64encode(
                hashlib.sha256(
                    actual_key.asbytes()
                ).digest()
            ).decode().rstrip("=")
        )

        if (
            actual_digest
            != expected_fingerprint
        ):
            raise RuntimeError(
                "SSH host key verification failed"
            )

        transport.auth_password(
            username=username,
            password=password,
        )

        channel = transport.open_session(
            timeout=8
        )

        channel.exec_command(
            operation_data["ssh"]
        )

        stdout_chunks = []
        stderr_chunks = []

        while True:
            if channel.recv_ready():
                stdout_chunks.append(
                    channel.recv(4096)
                )

            if channel.recv_stderr_ready():
                stderr_chunks.append(
                    channel.recv_stderr(4096)
                )

            if (
                channel.exit_status_ready()
                and not channel.recv_ready()
                and not channel.recv_stderr_ready()
            ):
                break

        returncode = (
            channel.recv_exit_status()
        )

        stdout = b"".join(
            stdout_chunks
        ).decode(
            errors="replace"
        )

        stderr = b"".join(
            stderr_chunks
        ).decode(
            errors="replace"
        )

        return {
            "returncode":
                returncode,
            "stdout":
                stdout[-30000:],
            "stderr":
                stderr[-8000:],
        }

    finally:
        transport.close()
        sock.close()


def execute_plan(
    *,
    plan_id: str,
    approval_id: str,
    username: str | None = None,
    password: str | None = None,
    ssh_port: int = 22,
) -> dict:
    ensure_inspector_schema()

    plan_row = fetch_one(
        """
        SELECT *
        FROM network_inspection_plans
        WHERE id = %s
        """,
        (plan_id,),
    )

    if not plan_row:
        raise KeyError(
            "Inspection plan not found"
        )

    plan = dict(plan_row)

    if plan["status"] != "approved":
        raise ValueError(
            "Inspection plan is not approved"
        )

    if str(
        plan.get("approval_id")
    ) != str(
        approval_id
    ):
        raise ValueError(
            "Approval does not belong to this plan"
        )

    args_hash = hash_arguments(
        _plan_arguments(plan)
    )

    if not validate_approval(
        approval_id=approval_id,
        tool_name=TOOL_NAME,
        environment=plan["environment"],
        target=plan["target"],
        args_hash=args_hash,
    ):
        raise PermissionError(
            "Invalid, expired or consumed inspection approval"
        )

    if not consume_approval(
        approval_id=approval_id,
        tool_name=TOOL_NAME,
        environment=plan["environment"],
        target=plan["target"],
        args_hash=args_hash,
    ):
        raise PermissionError(
            "Inspection approval could not be consumed"
        )

    log_tool_event(
        tool_name=TOOL_NAME,
        environment=plan["environment"],
        target=plan["target"],
        risk="read",
        status="started",
        args_hash=args_hash,
        approval_id=approval_id,
        result_summary=(
            "Executing confirmed read-only inspection command"
        ),
    )

    try:
        if plan["transport"] == "local":
            result = _execute_local(
                plan
            )

        else:
            if (
                not username
                or password is None
            ):
                raise ValueError(
                    "SSH username and password are required"
                )

            result = _execute_ssh(
                plan,
                username=username,
                password=password,
                ssh_port=ssh_port,
            )

    except Exception as exc:
        execute(
            """
            UPDATE network_inspection_plans
            SET
                status = 'failed',
                executed_at = NOW()
            WHERE id = %s
            """,
            (plan_id,),
        )

        log_tool_event(
            tool_name=TOOL_NAME,
            environment=plan["environment"],
            target=plan["target"],
            risk="read",
            status="failed",
            args_hash=args_hash,
            approval_id=approval_id,
            error_message=str(exc)[:2000],
        )

        raise

    result_id = str(
        uuid.uuid4()
    )

    execute(
        """
        INSERT INTO network_inspection_results (
            id,
            plan_id,
            environment,
            target,
            profile,
            operation,
            command_preview,
            returncode,
            stdout,
            stderr
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        """,
        (
            result_id,
            plan_id,
            plan["environment"],
            plan["target"],
            plan["profile"],
            plan["operation"],
            plan["command_preview"],
            result["returncode"],
            result["stdout"],
            result["stderr"],
        ),
    )

    execute(
        """
        UPDATE network_inspection_plans
        SET
            status = 'executed',
            executed_at = NOW()
        WHERE id = %s
        """,
        (plan_id,),
    )

    log_tool_event(
        tool_name=TOOL_NAME,
        environment=plan["environment"],
        target=plan["target"],
        risk="read",
        status="success",
        args_hash=args_hash,
        approval_id=approval_id,
        result_summary=(
            f"Inspection completed returncode={result['returncode']}"
        ),
    )

    return {
        "result_id": result_id,
        "plan_id": plan_id,
        "target": plan["target"],
        "profile": plan["profile"],
        "operation": plan["operation"],
        "command":
            plan["command_preview"],
        **result,
    }


def recent_results(
    *,
    environment: str | None = None,
    limit: int = 50,
) -> list[dict]:
    ensure_inspector_schema()

    if environment:
        rows = fetch_all(
            """
            SELECT *
            FROM network_inspection_results
            WHERE environment = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (
                environment,
                limit,
            ),
        )
    else:
        rows = fetch_all(
            """
            SELECT *
            FROM network_inspection_results
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )

    return [
        dict(row)
        for row in rows
    ]
