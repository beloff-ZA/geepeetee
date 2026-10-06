import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from app.agents.documents import upsert_document
from app.agents.environment import DEFAULT_ENVIRONMENT_ID
from app.agents.runtime import conversation_runs, run_agent
from app.db.conversations import get_conversation, get_messages
from app.db.database import execute, fetch_all, fetch_one


_scheduler: BackgroundScheduler | None = None

ALLOWED_BACKGROUND_JOB_TYPES = {
    "documentation",
    "work_optimisation",
    "knowledge_curation",
}


def ensure_background_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_background_control (
            id TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    execute(
        """
        INSERT INTO agent_background_control (id, enabled)
        VALUES ('global', TRUE)
        ON CONFLICT (id) DO NOTHING
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_background_jobs (
            id UUID PRIMARY KEY,
            job_type TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            environment_id TEXT,
            conversation_id UUID,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK (
                    status IN (
                        'queued',
                        'running',
                        'success',
                        'failed',
                        'skipped'
                    )
                ),
            priority INTEGER NOT NULL DEFAULT 100,
            not_before TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 2,
            dedupe_key TEXT UNIQUE,
            task TEXT NOT NULL,
            result_json JSONB,
            agent_run_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            error_message TEXT
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_background_jobs_queue
        ON agent_background_jobs(status, not_before, priority, created_at)
        """
    )


def background_enabled() -> bool:
    ensure_background_schema()

    env_enabled = (
        os.getenv(
            "BOUND_BACKGROUND_ENABLED",
            "true",
        ).strip().lower()
        not in {"0", "false", "no", "off"}
    )

    row = fetch_one(
        """
        SELECT enabled
        FROM agent_background_control
        WHERE id = 'global'
        """
    )

    return bool(
        env_enabled
        and row
        and row["enabled"]
    )


def set_background_enabled(
    enabled: bool,
) -> None:
    ensure_background_schema()

    execute(
        """
        UPDATE agent_background_control
        SET
            enabled = %s,
            updated_at = NOW()
        WHERE id = 'global'
        """,
        (enabled,),
    )


def _enqueue_job(
    *,
    job_type: str,
    agent_id: str,
    environment_id: str,
    conversation_id: str,
    task: str,
    dedupe_key: str,
    priority: int,
) -> None:
    if job_type not in ALLOWED_BACKGROUND_JOB_TYPES:
        raise ValueError(
            f"Unsupported background job type: {job_type}"
        )

    ensure_background_schema()

    execute(
        """
        INSERT INTO agent_background_jobs (
            id,
            job_type,
            agent_id,
            environment_id,
            conversation_id,
            status,
            priority,
            not_before,
            attempts,
            max_attempts,
            dedupe_key,
            task
        )
        VALUES (
            %s, %s, %s, %s, %s,
            'queued', %s, NOW(), 0, 2, %s, %s
        )
        ON CONFLICT (dedupe_key) DO NOTHING
        """,
        (
            str(uuid.uuid4()),
            job_type,
            agent_id,
            environment_id,
            conversation_id,
            priority,
            dedupe_key,
            task,
        ),
    )


def enqueue_post_conversation_jobs(
    *,
    conversation_id: str,
    environment_id: str = DEFAULT_ENVIRONMENT_ID,
) -> None:
    """
    Queue bounded, non-executing maintenance work after an interactive run.

    These jobs may write only to BOUND's internal knowledge/document stores.
    They cannot invoke tools or change agent policy.
    """

    _enqueue_job(
        job_type="documentation",
        agent_id="documentation_steward",
        environment_id=environment_id,
        conversation_id=conversation_id,
        priority=100,
        dedupe_key=(
            f"documentation:{conversation_id}"
        ),
        task=(
            "Review this completed conversation and its specialist work. "
            "Produce a concise internal operations note containing the problem, "
            "relevant environment context, verified or clearly-labelled facts, "
            "diagnostic path, decisions, unresolved questions, recommended next "
            "steps and any rollback or risk notes. Do not invent missing state."
        ),
    )

    _enqueue_job(
        job_type="work_optimisation",
        agent_id="work_optimizer",
        environment_id=environment_id,
        conversation_id=conversation_id,
        priority=110,
        dedupe_key=(
            f"optimisation:{conversation_id}"
        ),
        task=(
            "Review how the specialist team handled this conversation. "
            "Identify duplicated effort, missing specialists, avoidable questions, "
            "poor sequencing, reusable checks and routing improvements. "
            "Recommend improvements only. Do not modify prompts, permissions, "
            "routing policy, code or tool access."
        ),
    )

    _enqueue_job(
        job_type="knowledge_curation",
        agent_id="knowledge_curator",
        environment_id=environment_id,
        conversation_id=conversation_id,
        priority=120,
        dedupe_key=(
            f"knowledge:{conversation_id}"
        ),
        task=(
            "Review this conversation for durable knowledge worth retaining. "
            "Separate verified facts from working assumptions, identify conflicts "
            "with existing context, and note which specialist should own each "
            "piece of knowledge. Do not promote assumptions to facts."
        ),
    )


def _claim_next_job() -> dict | None:
    ensure_background_schema()

    row = fetch_one(
        """
        WITH next_job AS (
            SELECT id
            FROM agent_background_jobs
            WHERE status = 'queued'
              AND not_before <= NOW()
              AND attempts < max_attempts
            ORDER BY
                priority ASC,
                created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE agent_background_jobs AS jobs
        SET
            status = 'running',
            attempts = jobs.attempts + 1,
            started_at = NOW(),
            error_message = NULL
        FROM next_job
        WHERE jobs.id = next_job.id
        RETURNING jobs.*
        """
    )

    return (
        dict(row)
        if row
        else None
    )


def _conversation_evidence(
    conversation_id: str,
) -> list[dict]:
    evidence = []

    conversation = get_conversation(
        conversation_id
    )

    if conversation:
        evidence.append({
            "ref": "conversation-metadata",
            "source": "conversation",
            "content": json.dumps(
                {
                    "title":
                        conversation.get("title"),
                    "created_at":
                        str(
                            conversation.get(
                                "created_at"
                            )
                        ),
                    "updated_at":
                        str(
                            conversation.get(
                                "updated_at"
                            )
                        ),
                },
                ensure_ascii=False,
            ),
        })

    messages = get_messages(
        conversation_id
    )

    for index, message in enumerate(
        messages[-20:],
        start=1,
    ):
        evidence.append({
            "ref": f"message-{index}",
            "source": (
                "conversation:"
                + str(message.get("role"))
            ),
            "content": str(
                message.get("content") or ""
            )[:6000],
        })

    runs = conversation_runs(
        conversation_id,
        limit=100,
    )

    for run in runs[-20:]:
        output = run.get(
            "output_json"
        ) or {}

        evidence.append({
            "ref": (
                "agent-run-"
                + str(run.get("id"))
            ),
            "source": (
                "agent:"
                + str(run.get("agent_id"))
            ),
            "content": json.dumps(
                {
                    "status":
                        run.get("status"),
                    "summary":
                        output.get("summary"),
                    "questions":
                        output.get("questions"),
                    "concerns":
                        output.get("concerns"),
                    "recommendations":
                        output.get(
                            "recommendations"
                        ),
                },
                ensure_ascii=False,
                default=str,
            )[:8000],
        })

    return evidence


def _document_markdown(
    *,
    title: str,
    output: dict,
) -> str:
    lines = [
        f"# {title}",
        "",
        "Status: Draft generated by BOUND Documentation Steward.",
        "",
    ]

    summary = str(
        output.get("summary") or ""
    ).strip()

    if summary:
        lines.extend([
            "## Summary",
            summary,
            "",
        ])

    facts = output.get(
        "facts"
    ) or []

    if facts:
        lines.append("## Facts and observations")

        for item in facts:
            if isinstance(item, dict):
                claim = item.get(
                    "claim"
                )
                confidence = item.get(
                    "confidence"
                )
                refs = item.get(
                    "evidence_refs"
                ) or []

                suffix = ""

                if confidence is not None:
                    suffix += (
                        f" Confidence: {confidence}."
                    )

                if refs:
                    suffix += (
                        " Evidence: "
                        + ", ".join(
                            str(ref)
                            for ref in refs
                        )
                        + "."
                    )

                if claim:
                    lines.append(
                        f"- {claim}{suffix}"
                    )

        lines.append("")

    unknowns = output.get(
        "unknowns"
    ) or []

    if unknowns:
        lines.append("## Unknowns")

        for item in unknowns:
            lines.append(
                f"- {item}"
            )

        lines.append("")

    recommendations = output.get(
        "recommendations"
    ) or []

    if recommendations:
        lines.append("## Recommended next steps")

        for item in recommendations:
            if isinstance(item, dict):
                action = item.get(
                    "action"
                )
                reason = item.get(
                    "reason"
                )

                if action:
                    line = (
                        f"- {action}"
                    )

                    if reason:
                        line += (
                            f": {reason}"
                        )

                    lines.append(line)
            else:
                lines.append(
                    f"- {item}"
                )

        lines.append("")

    concerns = output.get(
        "concerns"
    ) or []

    if concerns:
        lines.append("## Concerns")

        for item in concerns:
            lines.append(
                f"- {item}"
            )

        lines.append("")

    lines.extend([
        "## Provenance",
        "Generated from the conversation and recorded BOUND agent runs. "
        "This draft is not an authoritative live-state record until reviewed.",
        "",
    ])

    return "\n".join(lines)


def process_next_background_job() -> dict | None:
    if not background_enabled():
        return None

    job = _claim_next_job()

    if not job:
        return None

    try:
        evidence = _conversation_evidence(
            str(job["conversation_id"])
        )

        result = run_agent(
            agent_id=job["agent_id"],
            task=job["task"],
            evidence=evidence,
            trigger_type=(
                "background:"
                + job["job_type"]
            ),
            with_oversight=False,
            conversation_id=str(
                job["conversation_id"]
            ),
            environment_id=(
                job["environment_id"]
                or DEFAULT_ENVIRONMENT_ID
            ),
        )

        output = result.get(
            "output"
        ) or {}

        if result.get("status") in {
            "failed",
            "blocked",
        }:
            raise RuntimeError(
                "Background agent run did not complete: "
                + str(result.get("status"))
            )

        if (
            job["job_type"]
            == "documentation"
        ):
            conversation = get_conversation(
                str(job["conversation_id"])
            ) or {}

            title = (
                "Operations note: "
                + str(
                    conversation.get(
                        "title"
                    )
                    or job["conversation_id"]
                )
            )

            upsert_document(
                environment_id=(
                    job["environment_id"]
                    or DEFAULT_ENVIRONMENT_ID
                ),
                conversation_id=str(
                    job["conversation_id"]
                ),
                title=title,
                category="conversation_operations_note",
                content=_document_markdown(
                    title=title,
                    output=output,
                ),
                source_agent_id=
                    job["agent_id"],
                source_run_id=
                    result.get("run_id"),
            )

        execute(
            """
            UPDATE agent_background_jobs
            SET
                status = 'success',
                result_json = %s::jsonb,
                agent_run_id = %s,
                finished_at = NOW()
            WHERE id = %s
            """,
            (
                json.dumps(
                    output,
                    ensure_ascii=False,
                    default=str,
                ),
                result.get("run_id"),
                job["id"],
            ),
        )

        return {
            "job_id": str(job["id"]),
            "status": "success",
            "agent_run_id":
                result.get("run_id"),
        }

    except Exception as exc:
        error = str(exc)[:4000]

        current = fetch_one(
            """
            SELECT attempts, max_attempts
            FROM agent_background_jobs
            WHERE id = %s
            """,
            (job["id"],),
        )

        retry = bool(
            current
            and int(current["attempts"])
            < int(current["max_attempts"])
        )

        execute(
            """
            UPDATE agent_background_jobs
            SET
                status = %s,
                not_before = CASE
                    WHEN %s
                    THEN NOW() + INTERVAL '5 minutes'
                    ELSE not_before
                END,
                finished_at = CASE
                    WHEN %s THEN NULL
                    ELSE NOW()
                END,
                error_message = %s
            WHERE id = %s
            """,
            (
                (
                    "queued"
                    if retry
                    else "failed"
                ),
                retry,
                retry,
                error,
                job["id"],
            ),
        )

        return {
            "job_id": str(job["id"]),
            "status": (
                "queued"
                if retry
                else "failed"
            ),
            "error": error,
        }


def list_background_jobs(
    limit: int = 100,
) -> list[dict]:
    ensure_background_schema()

    rows = fetch_all(
        """
        SELECT *
        FROM agent_background_jobs
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    return [
        dict(row)
        for row in rows
    ]


def background_status() -> dict:
    ensure_background_schema()

    counts = fetch_all(
        """
        SELECT status, COUNT(*) AS count
        FROM agent_background_jobs
        GROUP BY status
        """
    )

    return {
        "enabled":
            background_enabled(),
        "worker_running":
            bool(
                _scheduler
                and _scheduler.running
            ),
        "counts": {
            row["status"]:
                int(row["count"])
            for row in counts
        },
    }


def _recover_stale_jobs() -> None:
    ensure_background_schema()

    execute(
        """
        UPDATE agent_background_jobs
        SET
            status = 'queued',
            not_before = NOW() + INTERVAL '1 minute',
            error_message = COALESCE(
                error_message || E'\n',
                ''
            ) || 'Recovered stale running job after process restart.'
        WHERE status = 'running'
          AND started_at < NOW() - INTERVAL '15 minutes'
        """
    )


def start_background_worker() -> None:
    global _scheduler

    ensure_background_schema()
    _recover_stale_jobs()

    if _scheduler and _scheduler.running:
        return

    _scheduler = BackgroundScheduler(
        timezone="UTC",
    )

    _scheduler.add_job(
        process_next_background_job,
        trigger="interval",
        seconds=20,
        id="bound-background-worker",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    _scheduler.start()


def stop_background_worker() -> None:
    global _scheduler

    if _scheduler and _scheduler.running:
        _scheduler.shutdown(
            wait=False
        )
