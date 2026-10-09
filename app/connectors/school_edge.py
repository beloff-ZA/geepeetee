import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.database import execute, fetch_all, fetch_one


EDGE_CAPABILITIES: dict[str, dict[str, Any]] = {
    "network.discover": {
        "domain": "network",
        "description": "Bounded host and service discovery inside a configured local scope.",
        "risk": "read",
    },
    "network.ping": {
        "domain": "network",
        "description": "ICMP reachability check for one allowlisted target.",
        "risk": "read",
    },
    "network.tcp": {
        "domain": "network",
        "description": "TCP reachability check for one allowlisted target and approved port.",
        "risk": "read",
    },
    "network.ssh_profile": {
        "domain": "network",
        "description": "Run a predefined read-only CLI profile against an allowlisted network device.",
        "risk": "read",
    },
    "network.snmp_walk": {
        "domain": "network",
        "description": "Read an approved SNMP OID subtree from an allowlisted device.",
        "risk": "read",
    },
    "ad.directory_summary": {
        "domain": "identity",
        "description": "Read Active Directory domain, controller, user, computer and group summary data over LDAP.",
        "risk": "read",
    },
    "ad.search": {
        "domain": "identity",
        "description": "Run a constrained LDAP directory search using an approved search type.",
        "risk": "read",
    },
    "windows.read_profile": {
        "domain": "identity",
        "description": "Run a predefined read-only PowerShell profile over WinRM for AD, DNS, DHCP, NPS or Windows service state.",
        "risk": "read",
    },
    "unifi.devices": {
        "domain": "wireless",
        "description": "Read UniFi infrastructure device inventory and state from the configured controller.",
        "risk": "read",
    },
    "unifi.clients": {
        "domain": "wireless",
        "description": "Read UniFi client association data from the configured controller.",
        "risk": "read",
    },
    "unifi.health": {
        "domain": "wireless",
        "description": "Read UniFi site health information from the configured controller.",
        "risk": "read",
    },
    "grandstream.devices": {
        "domain": "wireless",
        "description": "Read Grandstream/GWN infrastructure inventory through the configured controller API or SNMP profile.",
        "risk": "read",
    },
    "grandstream.clients": {
        "domain": "wireless",
        "description": "Read Grandstream/GWN client association information through the configured controller API.",
        "risk": "read",
    },
    "grandstream.snmp_walk": {
        "domain": "network",
        "description": "Read an approved SNMP subtree from Grandstream switching infrastructure.",
        "risk": "read",
    },
    "cctv.onvif_info": {
        "domain": "physical_systems",
        "description": "Read ONVIF device identity and basic service information from an allowlisted camera or recorder.",
        "risk": "read",
    },
    "cctv.tcp_status": {
        "domain": "physical_systems",
        "description": "Check approved CCTV service ports without requesting video streams.",
        "risk": "read",
    },
    "pbx.ami_status": {
        "domain": "communications",
        "description": "Read Asterisk-compatible PBX status using a read-only AMI account when configured.",
        "risk": "read",
    },
    "pbx.tcp_status": {
        "domain": "communications",
        "description": "Check approved PBX signalling and management ports.",
        "risk": "read",
    },
}


def ensure_edge_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_edge_connectors (
            connector_id TEXT PRIMARY KEY,
            environment_id TEXT NOT NULL,
            display_name TEXT NOT NULL,
            version TEXT,
            capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            last_seen_at TIMESTAMPTZ,
            last_remote_addr TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_edge_jobs (
            id UUID PRIMARY KEY,
            connector_id TEXT NOT NULL,
            environment_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            target TEXT,
            parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
            requested_by TEXT NOT NULL DEFAULT 'operator',
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (
                    status IN (
                        'queued',
                        'leased',
                        'succeeded',
                        'failed',
                        'expired',
                        'cancelled'
                    )
                ),
            lease_token UUID,
            leased_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_edge_results (
            id UUID PRIMARY KEY,
            job_id UUID NOT NULL UNIQUE
                REFERENCES bound_edge_jobs(id)
                ON DELETE CASCADE,
            connector_id TEXT NOT NULL,
            ok BOOLEAN NOT NULL,
            result JSONB,
            error TEXT,
            observed_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bound_edge_jobs_queue
        ON bound_edge_jobs(connector_id, status, created_at)
        """
    )


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def authenticate_edge(token: str | None) -> bool:
    expected = os.getenv("BOUND_SCHOOL_EDGE_TOKEN", "").strip()
    if not expected or not token:
        return False
    return hmac.compare_digest(
        _token_hash(token),
        _token_hash(expected),
    )


def record_heartbeat(
    *,
    connector_id: str,
    environment_id: str,
    display_name: str,
    version: str | None,
    capabilities: list[str],
    remote_addr: str | None = None,
) -> dict:
    ensure_edge_schema()

    supported = [
        item
        for item in capabilities
        if item in EDGE_CAPABILITIES
    ]

    row = fetch_one(
        """
        INSERT INTO bound_edge_connectors (
            connector_id,
            environment_id,
            display_name,
            version,
            capabilities,
            last_seen_at,
            last_remote_addr
        )
        VALUES (%s, %s, %s, %s, %s::jsonb, NOW(), %s)
        ON CONFLICT (connector_id) DO UPDATE
        SET
            environment_id = EXCLUDED.environment_id,
            display_name = EXCLUDED.display_name,
            version = EXCLUDED.version,
            capabilities = EXCLUDED.capabilities,
            last_seen_at = NOW(),
            last_remote_addr = EXCLUDED.last_remote_addr,
            updated_at = NOW()
        RETURNING *
        """,
        (
            connector_id,
            environment_id,
            display_name,
            version,
            json.dumps(supported),
            remote_addr,
        ),
    )

    return dict(row)


def connector_status() -> list[dict]:
    ensure_edge_schema()
    rows = fetch_all(
        """
        SELECT
            connector_id,
            environment_id,
            display_name,
            version,
            capabilities,
            last_seen_at,
            created_at,
            updated_at
        FROM bound_edge_connectors
        ORDER BY connector_id
        """
    )
    return [dict(row) for row in rows]


def queue_job(
    *,
    connector_id: str,
    environment_id: str,
    capability: str,
    target: str | None,
    parameters: dict[str, Any] | None = None,
    requested_by: str = "operator",
) -> dict:
    ensure_edge_schema()

    if capability not in EDGE_CAPABILITIES:
        raise ValueError(f"Unsupported edge capability: {capability}")

    if not connector_id.strip():
        raise ValueError("connector_id is required")

    job_id = str(uuid.uuid4())
    row = fetch_one(
        """
        INSERT INTO bound_edge_jobs (
            id,
            connector_id,
            environment_id,
            capability,
            target,
            parameters,
            requested_by
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        RETURNING *
        """,
        (
            job_id,
            connector_id,
            environment_id,
            capability,
            target,
            json.dumps(parameters or {}),
            requested_by,
        ),
    )
    return dict(row)


def next_job(connector_id: str) -> dict | None:
    ensure_edge_schema()

    # Re-queue stale leases after five minutes. Connector actions are read-only
    # and idempotent by design, but a lease token still prevents late duplicate
    # result submission from overwriting a newer attempt.
    execute(
        """
        UPDATE bound_edge_jobs
        SET
            status = 'queued',
            lease_token = NULL,
            leased_at = NULL
        WHERE
            connector_id = %s
            AND status = 'leased'
            AND leased_at < NOW() - INTERVAL '5 minutes'
        """,
        (connector_id,),
    )

    row = fetch_one(
        """
        WITH candidate AS (
            SELECT id
            FROM bound_edge_jobs
            WHERE connector_id = %s
              AND status = 'queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE bound_edge_jobs AS jobs
        SET
            status = 'leased',
            lease_token = gen_random_uuid(),
            leased_at = NOW()
        FROM candidate
        WHERE jobs.id = candidate.id
        RETURNING jobs.*
        """,
        (connector_id,),
    )

    return dict(row) if row else None


def get_job(job_id: str) -> dict | None:
    ensure_edge_schema()
    row = fetch_one(
        """
        SELECT
            jobs.*,
            results.ok,
            results.result,
            results.error,
            results.observed_at,
            results.received_at
        FROM bound_edge_jobs AS jobs
        LEFT JOIN bound_edge_results AS results
          ON results.job_id = jobs.id
        WHERE jobs.id = %s
        """,
        (job_id,),
    )
    return dict(row) if row else None


def submit_result(
    *,
    job_id: str,
    connector_id: str,
    lease_token: str,
    ok: bool,
    result: Any,
    error: str | None,
    observed_at: str | None,
) -> dict:
    ensure_edge_schema()

    job = fetch_one(
        """
        SELECT *
        FROM bound_edge_jobs
        WHERE id = %s
        """,
        (job_id,),
    )

    if not job:
        raise KeyError("Edge job not found")

    if job["connector_id"] != connector_id:
        raise PermissionError("Connector does not own this job")

    if job["status"] != "leased":
        raise PermissionError("Job is not currently leased")

    if str(job["lease_token"]) != str(lease_token):
        raise PermissionError("Lease token mismatch")

    try:
        observed = (
            datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if observed_at
            else datetime.now(timezone.utc)
        )
    except Exception:
        observed = datetime.now(timezone.utc)

    result_id = str(uuid.uuid4())

    execute(
        """
        INSERT INTO bound_edge_results (
            id,
            job_id,
            connector_id,
            ok,
            result,
            error,
            observed_at
        )
        VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (job_id) DO UPDATE
        SET
            connector_id = EXCLUDED.connector_id,
            ok = EXCLUDED.ok,
            result = EXCLUDED.result,
            error = EXCLUDED.error,
            observed_at = EXCLUDED.observed_at,
            received_at = NOW()
        """,
        (
            result_id,
            job_id,
            connector_id,
            bool(ok),
            json.dumps(result),
            (error or "")[:4000] or None,
            observed,
        ),
    )

    execute(
        """
        UPDATE bound_edge_jobs
        SET
            status = %s,
            completed_at = NOW()
        WHERE id = %s
        """,
        (
            "succeeded" if ok else "failed",
            job_id,
        ),
    )

    return get_job(job_id) or {"id": job_id}
