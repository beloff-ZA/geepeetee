import json
import uuid

from app.db.database import execute, fetch_all, fetch_one


def ensure_agent_knowledge_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_knowledge (
            id UUID PRIMARY KEY,
            agent_id TEXT NOT NULL,
            environment_id TEXT,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            source_run_id UUID,
            source_agent_id TEXT,
            verified BOOLEAN NOT NULL DEFAULT FALSE,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_knowledge_lookup
        ON agent_knowledge(agent_id, environment_id, active, created_at DESC)
        """
    )


def remember_agent_note(
    *,
    agent_id: str,
    content: str,
    environment_id: str | None,
    kind: str = "working_note",
    source_run_id: str | None = None,
    source_agent_id: str | None = None,
    verified: bool = False,
) -> dict:
    ensure_agent_knowledge_schema()

    knowledge_id = str(uuid.uuid4())

    row = fetch_one(
        """
        INSERT INTO agent_knowledge (
            id,
            agent_id,
            environment_id,
            kind,
            content,
            source_run_id,
            source_agent_id,
            verified
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s
        )
        RETURNING *
        """,
        (
            knowledge_id,
            agent_id,
            environment_id,
            kind,
            content[:8000],
            source_run_id,
            source_agent_id,
            verified,
        ),
    )

    return dict(row)


def get_agent_knowledge(
    *,
    agent_id: str,
    environment_id: str | None,
    limit: int = 30,
) -> list[dict]:
    ensure_agent_knowledge_schema()

    rows = fetch_all(
        """
        SELECT *
        FROM agent_knowledge
        WHERE agent_id = %s
          AND active = TRUE
          AND (
              environment_id IS NULL
              OR environment_id IS NOT DISTINCT FROM %s
          )
        ORDER BY
            verified DESC,
            created_at DESC
        LIMIT %s
        """,
        (
            agent_id,
            environment_id,
            limit,
        ),
    )

    return [
        dict(row)
        for row in rows
    ]


def handover_knowledge(
    *,
    source_agent_id: str,
    target_agent_id: str,
    knowledge_ids: list[str],
) -> list[dict]:
    """
    Explicitly copy selected persistent notes to another agent.

    Source knowledge remains intact. Handover never silently deletes history.
    """

    ensure_agent_knowledge_schema()

    handed_over = []

    for knowledge_id in knowledge_ids:
        row = fetch_one(
            """
            SELECT *
            FROM agent_knowledge
            WHERE id = %s
              AND agent_id = %s
              AND active = TRUE
            """,
            (
                knowledge_id,
                source_agent_id,
            ),
        )

        if not row:
            continue

        copy = remember_agent_note(
            agent_id=target_agent_id,
            content=row["content"],
            environment_id=row["environment_id"],
            kind="handover",
            source_run_id=row["source_run_id"],
            source_agent_id=source_agent_id,
            verified=bool(row["verified"]),
        )

        handed_over.append(copy)

    return handed_over


def knowledge_packet(
    *,
    agent_id: str,
    environment_id: str | None,
) -> list[dict]:
    notes = get_agent_knowledge(
        agent_id=agent_id,
        environment_id=environment_id,
    )

    packet = []

    for note in notes:
        status = (
            "verified persistent knowledge"
            if note["verified"]
            else "persistent working note; not independently verified"
        )

        packet.append({
            "ref": f"agent-memory-{note['id']}",
            "source": (
                f"agent_memory:{agent_id}:{status}"
            ),
            "content": note["content"],
        })

    return packet
