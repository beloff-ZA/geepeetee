import uuid

from app.db.database import execute, fetch_all, fetch_one


def ensure_document_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_documents (
            id UUID PRIMARY KEY,
            environment_id TEXT NOT NULL,
            conversation_id UUID,
            title TEXT NOT NULL,
            category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'published', 'archived')),
            content TEXT NOT NULL,
            source_agent_id TEXT,
            source_run_id UUID,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (environment_id, conversation_id, category)
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bound_documents_updated
        ON bound_documents(environment_id, status, updated_at DESC)
        """
    )


def upsert_document(
    *,
    environment_id: str,
    conversation_id: str | None,
    title: str,
    category: str,
    content: str,
    source_agent_id: str | None,
    source_run_id: str | None,
) -> dict:
    ensure_document_schema()

    document_id = str(uuid.uuid4())

    row = fetch_one(
        """
        INSERT INTO bound_documents (
            id,
            environment_id,
            conversation_id,
            title,
            category,
            status,
            content,
            source_agent_id,
            source_run_id,
            version
        )
        VALUES (
            %s, %s, %s, %s, %s,
            'draft', %s, %s, %s, 1
        )
        ON CONFLICT (
            environment_id,
            conversation_id,
            category
        )
        DO UPDATE SET
            title = EXCLUDED.title,
            content = EXCLUDED.content,
            source_agent_id = EXCLUDED.source_agent_id,
            source_run_id = EXCLUDED.source_run_id,
            version = bound_documents.version + 1,
            updated_at = NOW()
        RETURNING *
        """,
        (
            document_id,
            environment_id,
            conversation_id,
            title[:300],
            category[:100],
            content[:50000],
            source_agent_id,
            source_run_id,
        ),
    )

    return dict(row)


def list_documents(
    *,
    environment_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    ensure_document_schema()

    if environment_id:
        rows = fetch_all(
            """
            SELECT *
            FROM bound_documents
            WHERE environment_id = %s
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (
                environment_id,
                limit,
            ),
        )
    else:
        rows = fetch_all(
            """
            SELECT *
            FROM bound_documents
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (limit,),
        )

    return [
        dict(row)
        for row in rows
    ]


def get_document(
    document_id: str,
) -> dict | None:
    ensure_document_schema()

    row = fetch_one(
        """
        SELECT *
        FROM bound_documents
        WHERE id = %s
        """,
        (document_id,),
    )

    return (
        dict(row)
        if row
        else None
    )
