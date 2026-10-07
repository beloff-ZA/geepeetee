import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.db.database import execute, fetch_all, fetch_one


ALLOWED_KINDS = {"skill", "agent", "mcp", "workflow"}
APPROVABLE_SCAN_STATES = {"safe", "caution"}

# The roster that existed before the SkillSpector trust gate was introduced.
# New code-defined agent IDs are fail-closed until represented by an approved
# capability record. This prevents a future catalog edit from silently creating
# another enabled authority surface.
BOOTSTRAP_TRUSTED_AGENT_IDS = frozenset({
    "operator",
    "sentinel",
    "network",
    "identity",
    "endpoint_mdm",
    "hardware",
    "software",
    "security",
    "physical_systems",
    "alternative_solutions",
    "reasoning",
    "problem_solver",
    "evidence",
    "admin",
    "accounting",
    "business_management",
    "communications",
    "vendor_procurement",
    "documentation_steward",
    "work_optimizer",
    "knowledge_curator",
})


def ensure_capability_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_capabilities (
            id UUID PRIMARY KEY,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            description TEXT,
            provenance TEXT,
            requested_capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
            status TEXT NOT NULL DEFAULT 'quarantine',
            scan_state TEXT NOT NULL DEFAULT 'pending',
            risk_score INTEGER,
            risk_severity TEXT,
            recommendation TEXT,
            analysis_complete BOOLEAN,
            latest_scan_id UUID,
            approved_by TEXT,
            approved_at TIMESTAMPTZ,
            installed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(kind, name, source)
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_capability_scans (
            id UUID PRIMARY KEY,
            capability_id UUID NOT NULL
                REFERENCES bound_capabilities(id)
                ON DELETE CASCADE,
            scanner TEXT NOT NULL,
            scanner_available BOOLEAN NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            risk_score INTEGER,
            risk_severity TEXT,
            recommendation TEXT,
            analysis_complete BOOLEAN,
            exit_code INTEGER,
            report_json JSONB,
            stderr TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bound_capabilities_status
        ON bound_capabilities(status, kind, updated_at DESC)
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bound_capability_scans_capability
        ON bound_capability_scans(capability_id, created_at DESC)
        """
    )



def _validate_source(source: str) -> str:
    """Constrain capability acquisition targets before invoking the scanner.

    GitHub and raw GitHub HTTPS sources are allowed. Local paths must live under
    BOUND's quarantine root. Other remote URLs stay disabled unless the operator
    explicitly opts in after putting the API behind authentication.
    """

    source = source.strip()
    parsed = urlparse(source)

    if parsed.scheme in {"http", "https"}:
        if parsed.scheme != "https":
            raise ValueError("Remote capability sources must use HTTPS")

        host = (parsed.hostname or "").lower()
        if host in {"github.com", "raw.githubusercontent.com"}:
            return source

        allow_remote = os.getenv(
            "BOUND_CAPABILITY_ALLOW_REMOTE_URLS",
            "false",
        ).strip().lower() in {"1", "true", "yes", "on"}

        if not allow_remote:
            raise ValueError(
                "Remote capability sources are restricted to GitHub by default"
            )

        return source

    if parsed.scheme:
        raise ValueError(
            f"Unsupported capability source scheme: {parsed.scheme}"
        )

    root = Path(
        os.getenv(
            "BOUND_CAPABILITY_QUARANTINE_ROOT",
            "/var/lib/bound/capabilities/quarantine",
        )
    ).resolve()

    candidate = Path(source).expanduser().resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Local capability sources must be inside {root}"
        ) from exc

    if not candidate.exists():
        raise ValueError("Local capability source does not exist")

    return str(candidate)

def _scanner_binary() -> str | None:
    configured = os.getenv("BOUND_SKILLSPECTOR_BIN", "").strip()
    if configured:
        return configured if Path(configured).exists() else None
    return shutil.which("skillspector")


def capability_status() -> dict:
    ensure_capability_schema()
    binary = _scanner_binary()
    rows = fetch_all(
        """
        SELECT status, COUNT(*) AS count
        FROM bound_capabilities
        GROUP BY status
        ORDER BY status
        """
    )
    return {
        "scanner": "NVIDIA SkillSpector",
        "scanner_available": bool(binary),
        "scanner_binary": binary,
        "static_scan_required": True,
        "semantic_scan_required": False,
        "semantic_scan_enabled": os.getenv(
            "BOUND_SKILLSPECTOR_SEMANTIC", "false"
        ).strip().lower() in {"1", "true", "yes", "on"},
        "counts": {
            row["status"]: int(row["count"])
            for row in rows
        },
    }


def _extract_assessment(report: dict) -> tuple[int, str, str, bool]:
    assessment = report.get("risk_assessment") or {}
    score = (
        report.get("max_risk_score")
        if report.get("max_risk_score") is not None
        else report.get("risk_score")
    )
    if score is None:
        score = assessment.get("score", 0)

    severity = (
        report.get("risk_severity")
        or assessment.get("severity")
        or "UNKNOWN"
    )
    recommendation = (
        report.get("risk_recommendation")
        or assessment.get("recommendation")
        or "CAUTION"
    )
    recommendation = str(recommendation).replace(" ", "_").upper()

    completeness = report.get("analysis_completeness") or {}
    complete = completeness.get("is_complete")
    if complete is None:
        complete = report.get("execution_successful", True)

    return int(score or 0), str(severity), recommendation, bool(complete)


def _scan_state(
    *,
    score: int,
    recommendation: str,
    complete: bool,
    exit_code: int,
) -> str:
    if not complete:
        return "caution"
    if recommendation == "DO_NOT_INSTALL" or score > 50:
        return "blocked"
    if recommendation == "SAFE" and exit_code == 0:
        return "safe"
    return "caution"


def _run_skillspector(source: str) -> dict:
    binary = _scanner_binary()
    if not binary:
        return {
            "scanner_available": False,
            "status": "scanner_unavailable",
            "risk_score": None,
            "risk_severity": None,
            "recommendation": "REVIEW_REQUIRED",
            "analysis_complete": False,
            "exit_code": None,
            "report": None,
            "stderr": (
                "SkillSpector is not installed. Capability remains quarantined. "
                "Install it separately and rescan."
            ),
        }

    semantic = os.getenv(
        "BOUND_SKILLSPECTOR_SEMANTIC", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}

    timeout = int(os.getenv("BOUND_SKILLSPECTOR_TIMEOUT", "240"))

    with tempfile.TemporaryDirectory(prefix="bound-skillspector-") as tmp:
        output = Path(tmp) / "report.json"
        command = [
            binary,
            "scan",
            source,
            "--format",
            "json",
            "--output",
            str(output),
            "--fail-on-incomplete",
        ]
        if not semantic:
            command.append("--no-llm")

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "scanner_available": True,
                "status": "scan_failed",
                "risk_score": None,
                "risk_severity": None,
                "recommendation": "REVIEW_REQUIRED",
                "analysis_complete": False,
                "exit_code": None,
                "report": None,
                "stderr": f"SkillSpector timed out after {timeout}s: {exc}",
            }

        report = None
        if output.exists():
            try:
                report = json.loads(output.read_text(encoding="utf-8"))
            except Exception:
                report = None

        if not isinstance(report, dict):
            return {
                "scanner_available": True,
                "status": "scan_failed",
                "risk_score": None,
                "risk_severity": None,
                "recommendation": "REVIEW_REQUIRED",
                "analysis_complete": False,
                "exit_code": process.returncode,
                "report": None,
                "stderr": (process.stderr or process.stdout or "")[:8000],
            }

        score, severity, recommendation, complete = _extract_assessment(report)
        state = _scan_state(
            score=score,
            recommendation=recommendation,
            complete=complete,
            exit_code=process.returncode,
        )

        return {
            "scanner_available": True,
            "status": state,
            "risk_score": score,
            "risk_severity": severity,
            "recommendation": recommendation,
            "analysis_complete": complete,
            "exit_code": process.returncode,
            "report": report,
            "stderr": (process.stderr or "")[:8000],
        }


def register_capability(
    *,
    name: str,
    kind: str,
    source: str,
    description: str | None = None,
    provenance: str | None = None,
    requested_capabilities: list[str] | None = None,
    scan_now: bool = True,
) -> dict:
    ensure_capability_schema()

    kind = kind.strip().lower()
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"Unsupported capability kind: {kind}")

    name = name.strip()
    source = source.strip()
    if not name or not source:
        raise ValueError("Capability name and source are required")

    source = _validate_source(source)

    existing = fetch_one(
        """
        SELECT id
        FROM bound_capabilities
        WHERE kind = %s AND name = %s AND source = %s
        """,
        (kind, name, source),
    )

    if existing:
        capability_id = str(existing["id"])
        execute(
            """
            UPDATE bound_capabilities
            SET
                description = %s,
                provenance = %s,
                requested_capabilities = %s::jsonb,
                status = 'quarantine',
                scan_state = 'pending',
                approved_by = NULL,
                approved_at = NULL,
                installed_at = NULL,
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                description,
                provenance,
                json.dumps(requested_capabilities or []),
                capability_id,
            ),
        )
    else:
        capability_id = str(uuid.uuid4())
        execute(
            """
            INSERT INTO bound_capabilities (
                id, name, kind, source, description, provenance,
                requested_capabilities, status, scan_state
            )
            VALUES (
                %s, %s, %s, %s, %s, %s,
                %s::jsonb, 'quarantine', 'pending'
            )
            """,
            (
                capability_id,
                name,
                kind,
                source,
                description,
                provenance,
                json.dumps(requested_capabilities or []),
            ),
        )

    if scan_now:
        return rescan_capability(capability_id)

    return get_capability(capability_id)


def rescan_capability(capability_id: str) -> dict:
    ensure_capability_schema()
    capability = get_capability(capability_id)
    if not capability:
        raise KeyError("Capability not found")

    result = _run_skillspector(capability["source"])
    scan_id = str(uuid.uuid4())

    execute(
        """
        INSERT INTO bound_capability_scans (
            id, capability_id, scanner, scanner_available, mode, status,
            risk_score, risk_severity, recommendation, analysis_complete,
            exit_code, report_json, stderr
        )
        VALUES (
            %s, %s, 'NVIDIA SkillSpector', %s, %s, %s,
            %s, %s, %s, %s, %s, %s::jsonb, %s
        )
        """,
        (
            scan_id,
            capability_id,
            result["scanner_available"],
            (
                "static+llm"
                if os.getenv("BOUND_SKILLSPECTOR_SEMANTIC", "false").strip().lower()
                in {"1", "true", "yes", "on"}
                else "static"
            ),
            result["status"],
            result["risk_score"],
            result["risk_severity"],
            result["recommendation"],
            result["analysis_complete"],
            result["exit_code"],
            json.dumps(result["report"]) if result["report"] is not None else None,
            result["stderr"],
        ),
    )

    status = "quarantine"
    if result["status"] == "safe":
        status = "review"
    elif result["status"] == "caution":
        status = "review"
    elif result["status"] == "blocked":
        status = "blocked"

    execute(
        """
        UPDATE bound_capabilities
        SET
            status = %s,
            scan_state = %s,
            risk_score = %s,
            risk_severity = %s,
            recommendation = %s,
            analysis_complete = %s,
            latest_scan_id = %s,
            approved_by = NULL,
            approved_at = NULL,
            installed_at = NULL,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            status,
            result["status"],
            result["risk_score"],
            result["risk_severity"],
            result["recommendation"],
            result["analysis_complete"],
            scan_id,
            capability_id,
        ),
    )

    return get_capability(capability_id)


def approve_capability(
    capability_id: str,
    *,
    approved_by: str = "operator",
) -> dict:
    ensure_capability_schema()
    capability = get_capability(capability_id)
    if not capability:
        raise KeyError("Capability not found")

    if capability["scan_state"] not in APPROVABLE_SCAN_STATES:
        raise PermissionError(
            "Capability cannot be approved until SkillSpector completes with SAFE or CAUTION."
        )

    execute(
        """
        UPDATE bound_capabilities
        SET
            status = 'approved',
            approved_by = %s,
            approved_at = NOW(),
            updated_at = NOW()
        WHERE id = %s
        """,
        (approved_by, capability_id),
    )
    return get_capability(capability_id)


def mark_installed(capability_id: str) -> dict:
    capability = get_capability(capability_id)
    if not capability:
        raise KeyError("Capability not found")
    if capability["status"] != "approved":
        raise PermissionError("Only approved capabilities may be marked installed")

    execute(
        """
        UPDATE bound_capabilities
        SET status = 'installed', installed_at = NOW(), updated_at = NOW()
        WHERE id = %s
        """,
        (capability_id,),
    )
    return get_capability(capability_id)


def list_capabilities(
    *,
    kind: str | None = None,
    limit: int = 200,
) -> list[dict]:
    ensure_capability_schema()
    params: list = []
    where = ""
    if kind:
        where = "WHERE kind = %s"
        params.append(kind)
    params.append(limit)
    rows = fetch_all(
        f"""
        SELECT *
        FROM bound_capabilities
        {where}
        ORDER BY created_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return [dict(row) for row in rows]


def get_capability(capability_id: str) -> dict | None:
    ensure_capability_schema()
    row = fetch_one(
        """
        SELECT *
        FROM bound_capabilities
        WHERE id = %s
        """,
        (capability_id,),
    )
    if not row:
        return None

    result = dict(row)
    scans = fetch_all(
        """
        SELECT *
        FROM bound_capability_scans
        WHERE capability_id = %s
        ORDER BY created_at DESC
        LIMIT 10
        """,
        (capability_id,),
    )
    result["scans"] = [dict(scan) for scan in scans]
    return result


def ensure_agent_definition_capability(agent) -> dict | None:
    """Create and scan a quarantine manifest for a newly code-defined agent.

    The existing bootstrap roster is grandfathered. Any future agent definition
    gets a deterministic SKILL.md snapshot and SkillSpector scan before it can be
    enabled. Scan success does not auto-approve the agent.
    """

    if agent.id in BOOTSTRAP_TRUSTED_AGENT_IDS:
        return None

    ensure_capability_schema()

    existing = fetch_one(
        """
        SELECT id
        FROM bound_capabilities
        WHERE kind = 'agent'
          AND name = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (agent.id,),
    )

    if existing:
        return get_capability(str(existing["id"]))

    root = Path(
        os.getenv(
            "BOUND_CAPABILITY_QUARANTINE_ROOT",
            "/var/lib/bound/capabilities/quarantine",
        )
    ).resolve()

    manifest_dir = root / "agent" / agent.id
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = manifest_dir / "SKILL.md"

    allowed_tools = ", ".join(agent.allowed_tools) or "none"
    body = (
        "---\n"
        f"name: {agent.id}\n"
        f"description: {json.dumps(agent.purpose)}\n"
        "metadata:\n"
        "  origin: bound-agent-catalog\n"
        "---\n\n"
        f"# {agent.name}\n\n"
        "## Purpose\n"
        f"{agent.purpose}\n\n"
        "## Persona\n"
        f"{agent.persona}\n\n"
        "## Mandate\n"
        f"{agent.mandate}\n\n"
        "## Runtime authority\n"
        f"{agent.authority.value}\n\n"
        "## Mode\n"
        f"{agent.mode.value}\n\n"
        "## Declared tools\n"
        f"{allowed_tools}\n\n"
        "This agent operates inside BOUND's shared agent contract. "
        "It cannot execute tools directly and remains subject to BOUND policy, "
        "Sentinel oversight, capability isolation and human approval gates.\n"
    )

    manifest.write_text(body, encoding="utf-8")

    return register_capability(
        name=agent.id,
        kind="agent",
        source=str(manifest_dir),
        description=agent.purpose,
        provenance="bound_agent_catalog",
        requested_capabilities=list(agent.allowed_tools),
        scan_now=True,
    )


def onboard_new_agent_definitions(agents) -> list[dict]:
    results = []
    for agent in agents:
        item = ensure_agent_definition_capability(agent)
        if item:
            results.append(item)
    return results


def agent_is_trusted(agent_id: str) -> bool:
    if agent_id in BOOTSTRAP_TRUSTED_AGENT_IDS:
        return True

    ensure_capability_schema()
    row = fetch_one(
        """
        SELECT 1
        FROM bound_capabilities
        WHERE kind = 'agent'
          AND name = %s
          AND status IN ('approved', 'installed')
        LIMIT 1
        """,
        (agent_id,),
    )
    return bool(row)
