import json
import uuid

from app.db.database import execute, fetch_all


def ensure_evaluation_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS bound_capability_evaluations (
            id UUID PRIMARY KEY,
            capability_id UUID NOT NULL
                REFERENCES bound_capabilities(id)
                ON DELETE CASCADE,
            evaluator_run_id UUID,
            status TEXT NOT NULL,
            fit_score INTEGER,
            summary TEXT,
            result_json JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_bound_capability_evaluations
        ON bound_capability_evaluations(capability_id, created_at DESC)
        """
    )


def record_evaluation(
    *,
    capability_id: str,
    status: str,
    evaluator_run_id: str | None,
    fit_score: int | None,
    summary: str | None,
    result: dict,
) -> dict:
    ensure_evaluation_schema()
    evaluation_id = str(uuid.uuid4())

    execute(
        """
        INSERT INTO bound_capability_evaluations (
            id,
            capability_id,
            evaluator_run_id,
            status,
            fit_score,
            summary,
            result_json
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            evaluation_id,
            capability_id,
            evaluator_run_id,
            status,
            fit_score,
            summary,
            json.dumps(result),
        ),
    )

    return {
        "id": evaluation_id,
        "capability_id": capability_id,
        "status": status,
        "fit_score": fit_score,
        "summary": summary,
        "result": result,
    }


def list_evaluations(
    capability_id: str,
    limit: int = 20,
) -> list[dict]:
    ensure_evaluation_schema()
    rows = fetch_all(
        """
        SELECT *
        FROM bound_capability_evaluations
        WHERE capability_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (capability_id, limit),
    )
    return [dict(row) for row in rows]
