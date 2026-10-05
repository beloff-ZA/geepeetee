from app.db.database import execute


def ensure_agent_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id UUID PRIMARY KEY,
            parent_run_id UUID,
            conversation_id UUID,
            environment_id TEXT,
            agent_id TEXT NOT NULL,
            status TEXT NOT NULL,
            trigger_type TEXT NOT NULL,
            task TEXT NOT NULL,
            evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            output_json JSONB,
            provider TEXT,
            model TEXT,
            usage_event_id UUID,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            duration_ms INTEGER,
            error_message TEXT
        )
        """
    )

    execute(
        """
        ALTER TABLE agent_runs
        ADD COLUMN IF NOT EXISTS parent_run_id UUID
        """
    )

    execute(
        """
        ALTER TABLE agent_runs
        ADD COLUMN IF NOT EXISTS conversation_id UUID
        """
    )

    execute(
        """
        ALTER TABLE agent_runs
        ADD COLUMN IF NOT EXISTS environment_id TEXT
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started
        ON agent_runs(agent_id, started_at DESC)
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_conversation
        ON agent_runs(conversation_id, started_at ASC)
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_concerns (
            id UUID PRIMARY KEY,
            run_id UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
            source_agent_id TEXT NOT NULL,
            severity TEXT NOT NULL
                CHECK (severity IN ('info', 'warning', 'critical')),
            category TEXT NOT NULL,
            message TEXT NOT NULL,
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved BOOLEAN NOT NULL DEFAULT FALSE,
            resolved_at TIMESTAMPTZ,
            resolution_note TEXT
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_concerns_open
        ON agent_concerns(resolved, severity, created_at DESC)
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_action_proposals (
            id UUID PRIMARY KEY,
            run_id UUID REFERENCES agent_runs(id) ON DELETE CASCADE,
            agent_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            environment TEXT NOT NULL,
            target TEXT,
            arguments JSONB NOT NULL,
            rationale TEXT NOT NULL,
            evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
            requested_risk TEXT NOT NULL,
            gate_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (
                    gate_status IN (
                        'pending',
                        'rejected',
                        'eligible_for_execution',
                        'approval_required',
                        'executed'
                    )
                ),
            gate_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            reviewed_at TIMESTAMPTZ
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_action_proposals_status
        ON agent_action_proposals(gate_status, created_at DESC)
        """
    )

    execute(
        """
        CREATE TABLE IF NOT EXISTS agent_runtime_state (
            agent_id TEXT PRIMARY KEY,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            persistent BOOLEAN NOT NULL DEFAULT FALSE,
            mode TEXT NOT NULL,
            schedule_expression TEXT,
            last_run_at TIMESTAMPTZ,
            next_run_at TIMESTAMPTZ,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            paused_reason TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
