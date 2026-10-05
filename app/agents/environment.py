import json
import uuid

from app.db.database import execute, fetch_all, fetch_one


DEFAULT_ENVIRONMENT_ID = "school"


def ensure_environment_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_environments (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS environment_facts (
            id UUID PRIMARY KEY,
            environment_id TEXT NOT NULL
                REFERENCES bound_environments(id)
                ON DELETE CASCADE,
            category TEXT NOT NULL,
            fact TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'operator',
            confidence NUMERIC NOT NULL DEFAULT 1.0,
            sensitive BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_environment_facts_environment
        ON environment_facts(environment_id, category, created_at)
        """
    )

    execute(
        """
        INSERT INTO bound_environments (
            id,
            name,
            description
        )
        VALUES (
            'school',
            'School IT Environment',
            'Primary managed school environment for BOUND specialist work.'
        )
        ON CONFLICT (id) DO NOTHING
        """
    )


def add_environment_fact(
    *,
    environment_id: str,
    category: str,
    fact: str,
    source: str = "operator",
    confidence: float = 1.0,
    sensitive: bool = False,
) -> dict:
    ensure_environment_schema()

    environment = fetch_one(
        """
        SELECT id
        FROM bound_environments
        WHERE id = %s
        """,
        (environment_id,),
    )

    if not environment:
        raise KeyError(
            f"Unknown environment: {environment_id}"
        )

    fact_id = str(uuid.uuid4())

    row = fetch_one(
        """
        INSERT INTO environment_facts (
            id,
            environment_id,
            category,
            fact,
            source,
            confidence,
            sensitive
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING *
        """,
        (
            fact_id,
            environment_id,
            category,
            fact,
            source,
            confidence,
            sensitive,
        ),
    )

    return dict(row)


def list_environment_facts(
    environment_id: str = DEFAULT_ENVIRONMENT_ID,
    *,
    include_sensitive: bool = True,
) -> list[dict]:
    ensure_environment_schema()

    query = """
        SELECT *
        FROM environment_facts
        WHERE environment_id = %s
    """

    params = [environment_id]

    if not include_sensitive:
        query += " AND sensitive = FALSE"

    query += " ORDER BY category, created_at"

    rows = fetch_all(
        query,
        tuple(params),
    )

    return [
        dict(row)
        for row in rows
    ]


def get_environment_packet(
    environment_id: str = DEFAULT_ENVIRONMENT_ID,
) -> list[dict]:
    """
    Return environment facts as evidence-like records.

    These are operator-maintained context, not live observations.
    Agents must still distinguish them from current measured state.
    """

    facts = list_environment_facts(
        environment_id,
        include_sensitive=True,
    )

    return [
        {
            "ref": f"environment-{item['id']}",
            "source": (
                "environment_context:"
                + item["environment_id"]
                + ":"
                + item["source"]
            ),
            "content": item["fact"],
        }
        for item in facts
    ]


def bootstrap_school_profile() -> None:
    """
    Seed only high-level, non-secret context.

    Detailed addresses, credentials, IPs, domains and topology belong in
    local database facts, never source control.
    """

    ensure_environment_schema()

    existing = fetch_one(
        """
        SELECT COUNT(*) AS count
        FROM environment_facts
        WHERE environment_id = 'school'
        """
    )

    if existing and int(existing["count"]) > 0:
        return

    defaults = [
        (
            "identity",
            "The school environment uses Windows Active Directory and RADIUS/NPS-style authentication alongside Google Workspace."
        ),
        (
            "network",
            "The school network is segmented into separate staff, student, guest and management traffic domains and uses managed switching and wireless infrastructure."
        ),
        (
            "wireless",
            "The wireless estate contains equipment from more than one vendor, so controller behaviour and model-specific differences must be considered."
        ),
        (
            "security",
            "Firewall policy, authentication, filtering, segmentation and auditability are operational priorities."
        ),
        (
            "endpoints",
            "The environment includes Windows devices, school-managed Apple devices and BYOD, so ownership and management authority must be distinguished."
        ),
        (
            "operations",
            "The IT function supports school leadership, staff, students, vendors and infrastructure projects, so technical recommendations need clear ownership and business impact."
        ),
        (
            "print",
            "The environment includes managed print/accounting workflows such as PaperCut."
        ),
        (
            "physical_systems",
            "The environment includes CCTV and related physical infrastructure with storage, retention and network dependencies."
        ),
    ]

    for category, fact in defaults:
        add_environment_fact(
            environment_id="school",
            category=category,
            fact=fact,
            source="bound_bootstrap",
            confidence=0.9,
            sensitive=False,
        )
