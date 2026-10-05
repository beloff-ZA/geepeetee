import math
import os
import re
import time
import uuid
from dataclasses import dataclass

from app.db.database import execute, fetch_all, fetch_one


PROVIDER_TOKEN_FACTORS = {
    "openai": 1.00,
    "gemini": 0.98,
    "groq": 1.03,
    "openrouter": 1.05,
}


DEFAULT_SHADOW_RATES = {
    # Deliberately fictional comparison prices in USD per 1M tokens.
    # These are NOT vendor billing prices.
    "openai": (2.00, 8.00),
    "gemini": (1.00, 4.00),
    "groq": (0.50, 2.00),
    "openrouter": (0.25, 1.00),
}


@dataclass
class TokenEstimate:
    input_tokens: int
    output_tokens: int


def _text_token_estimate(
    text: str,
    provider: str,
) -> int:
    if not text:
        return 0

    chars = len(text)
    words = len(re.findall(r"\S+", text))

    char_estimate = chars / 4.0
    word_estimate = words / 0.75

    punctuation = len(
        re.findall(r"[^\w\s]", text)
    )
    punctuation_ratio = punctuation / max(chars, 1)

    code_multiplier = (
        1.12
        if punctuation_ratio > 0.12
        else 1.0
    )

    provider_factor = PROVIDER_TOKEN_FACTORS.get(
        provider,
        1.0,
    )

    estimate = (
        (
            (char_estimate * 0.60)
            + (word_estimate * 0.40)
        )
        * code_multiplier
        * provider_factor
    )

    return max(1, math.ceil(estimate))


def estimate_request_tokens(
    *,
    provider: str,
    system_prompt: str,
    conversation_context: list[dict] | None,
    message: str,
) -> int:
    pieces = [system_prompt]

    for item in conversation_context or []:
        pieces.append(str(item.get("role", "")))
        pieces.append(str(item.get("content", "")))

    pieces.append("user")
    pieces.append(message)

    joined = "\n".join(pieces)

    # Small allowance for message-envelope/token framing.
    return _text_token_estimate(
        joined,
        provider,
    ) + (4 * (len(conversation_context or []) + 2))


def estimate_output_tokens(
    *,
    provider: str,
    text: str,
) -> int:
    return _text_token_estimate(
        text,
        provider,
    )


def shadow_rates(
    provider: str,
) -> tuple[float, float]:
    default_input, default_output = (
        DEFAULT_SHADOW_RATES.get(
            provider,
            (1.0, 4.0),
        )
    )

    prefix = provider.upper()

    input_rate = float(
        os.getenv(
            f"BOUND_SHADOW_{prefix}_INPUT_PER_M",
            default_input,
        )
    )

    output_rate = float(
        os.getenv(
            f"BOUND_SHADOW_{prefix}_OUTPUT_PER_M",
            default_output,
        )
    )

    return input_rate, output_rate


def shadow_cost_usd(
    *,
    provider: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    input_rate, output_rate = shadow_rates(
        provider
    )

    return (
        (input_tokens / 1_000_000) * input_rate
        + (output_tokens / 1_000_000) * output_rate
    )


def ensure_usage_schema() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS ai_usage_events (
            id UUID PRIMARY KEY,
            conversation_id UUID,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            fallback_index INTEGER NOT NULL DEFAULT 0,
            fallback_reason TEXT,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            duration_ms INTEGER,
            estimated_input_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_output_tokens INTEGER NOT NULL DEFAULT 0,
            actual_input_tokens INTEGER,
            actual_output_tokens INTEGER,
            actual_total_tokens INTEGER,
            cached_input_tokens INTEGER,
            usage_source TEXT NOT NULL DEFAULT 'estimated',
            shadow_input_rate_per_m NUMERIC NOT NULL DEFAULT 0,
            shadow_output_rate_per_m NUMERIC NOT NULL DEFAULT 0,
            shadow_cost_usd NUMERIC NOT NULL DEFAULT 0,
            response_id TEXT,
            error_message TEXT,
            useful BOOLEAN,
            quality_score INTEGER
                CHECK (
                    quality_score IS NULL
                    OR (
                        quality_score >= 1
                        AND quality_score <= 5
                    )
                ),
            feedback_note TEXT,
            feedback_at TIMESTAMPTZ
        )
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_ai_usage_provider_started
        ON ai_usage_events(provider, started_at DESC)
        """
    )

    execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_ai_usage_conversation
        ON ai_usage_events(conversation_id, started_at DESC)
        """
    )


def record_usage_event(
    *,
    conversation_id: str | None,
    provider: str,
    model: str,
    status: str,
    fallback_index: int,
    fallback_reason: str | None,
    duration_ms: int,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    actual_input_tokens: int | None,
    actual_output_tokens: int | None,
    actual_total_tokens: int | None,
    cached_input_tokens: int | None,
    response_id: str | None,
    error_message: str | None,
) -> str:
    event_id = str(uuid.uuid4())

    charged_input = (
        actual_input_tokens
        if actual_input_tokens is not None
        else estimated_input_tokens
    )

    charged_output = (
        actual_output_tokens
        if actual_output_tokens is not None
        else estimated_output_tokens
    )

    usage_source = (
        "actual"
        if (
            actual_input_tokens is not None
            and actual_output_tokens is not None
        )
        else "estimated"
    )

    input_rate, output_rate = shadow_rates(
        provider
    )

    shadow_cost = shadow_cost_usd(
        provider=provider,
        input_tokens=charged_input,
        output_tokens=charged_output,
    )

    execute(
        """
        INSERT INTO ai_usage_events (
            id,
            conversation_id,
            provider,
            model,
            status,
            fallback_index,
            fallback_reason,
            duration_ms,
            estimated_input_tokens,
            estimated_output_tokens,
            actual_input_tokens,
            actual_output_tokens,
            actual_total_tokens,
            cached_input_tokens,
            usage_source,
            shadow_input_rate_per_m,
            shadow_output_rate_per_m,
            shadow_cost_usd,
            response_id,
            error_message
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        """,
        (
            event_id,
            conversation_id,
            provider,
            model,
            status,
            fallback_index,
            fallback_reason,
            duration_ms,
            estimated_input_tokens,
            estimated_output_tokens,
            actual_input_tokens,
            actual_output_tokens,
            actual_total_tokens,
            cached_input_tokens,
            usage_source,
            input_rate,
            output_rate,
            shadow_cost,
            response_id,
            error_message,
        ),
    )

    return event_id


def add_usage_feedback(
    *,
    event_id: str,
    useful: bool,
    quality_score: int,
    note: str | None = None,
) -> bool:
    if quality_score < 1 or quality_score > 5:
        raise ValueError(
            "quality_score must be between 1 and 5"
        )

    row = fetch_one(
        """
        UPDATE ai_usage_events
        SET
            useful = %s,
            quality_score = %s,
            feedback_note = %s,
            feedback_at = NOW()
        WHERE id = %s
        RETURNING id
        """,
        (
            useful,
            quality_score,
            note,
            event_id,
        ),
    )

    return bool(row)


def usage_summary(
    days: int = 30,
) -> list[dict]:
    rows = fetch_all(
        """
        SELECT
            provider,
            COUNT(*) AS calls,
            COUNT(*) FILTER (
                WHERE status = 'success'
            ) AS successful_calls,
            ROUND(
                AVG(duration_ms)
                FILTER (WHERE status = 'success')
            ) AS avg_latency_ms,
            COALESCE(
                SUM(
                    COALESCE(
                        actual_input_tokens,
                        estimated_input_tokens
                    )
                ),
                0
            ) AS input_tokens,
            COALESCE(
                SUM(
                    COALESCE(
                        actual_output_tokens,
                        estimated_output_tokens
                    )
                ),
                0
            ) AS output_tokens,
            ROUND(
                SUM(shadow_cost_usd),
                8
            ) AS shadow_spend_usd,
            ROUND(
                AVG(quality_score),
                2
            ) AS avg_quality,
            ROUND(
                AVG(
                    CASE
                        WHEN useful IS TRUE THEN 1.0
                        WHEN useful IS FALSE THEN 0.0
                    END
                ) * 100,
                2
            ) AS useful_percent,
            COUNT(quality_score) AS rated_calls,
            ROUND(
                AVG(
                    CASE
                        WHEN actual_total_tokens IS NOT NULL
                             AND actual_total_tokens > 0
                        THEN (
                            ABS(
                                (
                                    estimated_input_tokens
                                    + estimated_output_tokens
                                )
                                - actual_total_tokens
                            )::NUMERIC
                            / actual_total_tokens
                        ) * 100
                    END
                ),
                2
            ) AS avg_token_estimate_error_percent
        FROM ai_usage_events
        WHERE started_at >= NOW() - (%s * INTERVAL '1 day')
        GROUP BY provider
        ORDER BY provider
        """,
        (days,),
    )

    results = []

    for row in rows:
        item = dict(row)

        calls = int(item["calls"] or 0)
        successes = int(
            item["successful_calls"] or 0
        )

        item["success_percent"] = (
            round(
                (successes / calls) * 100,
                2,
            )
            if calls
            else None
        )

        avg_quality = item.get(
            "avg_quality"
        )
        useful_percent = item.get(
            "useful_percent"
        )

        if (
            avg_quality is not None
            and useful_percent is not None
        ):
            quality_component = (
                float(avg_quality) / 5.0
            )
            usefulness_component = (
                float(useful_percent) / 100.0
            )

            item["effectiveness_score"] = round(
                (
                    quality_component * 0.65
                    + usefulness_component * 0.35
                )
                * 100,
                2,
            )
        else:
            item["effectiveness_score"] = None

        shadow_spend = float(
            item.get("shadow_spend_usd") or 0
        )

        if (
            shadow_spend > 0
            and item["effectiveness_score"] is not None
        ):
            item["effectiveness_per_shadow_dollar"] = (
                round(
                    item["effectiveness_score"]
                    / shadow_spend,
                    2,
                )
            )
        else:
            item["effectiveness_per_shadow_dollar"] = None

        results.append(item)

    return results


def recent_usage(
    limit: int = 100,
) -> list[dict]:
    rows = fetch_all(
        """
        SELECT
            id,
            conversation_id,
            provider,
            model,
            status,
            fallback_index,
            fallback_reason,
            started_at,
            duration_ms,
            estimated_input_tokens,
            estimated_output_tokens,
            actual_input_tokens,
            actual_output_tokens,
            actual_total_tokens,
            cached_input_tokens,
            usage_source,
            shadow_cost_usd,
            response_id,
            error_message,
            useful,
            quality_score,
            feedback_note,
            feedback_at
        FROM ai_usage_events
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    return [
        dict(row)
        for row in rows
    ]


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def usage_recommendations(
    days: int = 30,
) -> dict:
    providers = usage_summary(days)

    if not providers:
        return {
            "recommended_order": [],
            "providers": [],
            "notes": [
                "No usage data exists yet."
            ],
        }

    max_latency = max(
        [
            float(item["avg_latency_ms"])
            for item in providers
            if item.get("avg_latency_ms") is not None
        ]
        or [1.0]
    )

    max_shadow_spend = max(
        [
            float(item["shadow_spend_usd"])
            for item in providers
        ]
        or [1.0]
    )

    ranked = []

    for item in providers:
        calls = int(item.get("calls") or 0)
        rated_calls = int(
            item.get("rated_calls") or 0
        )
        success = float(
            item.get("success_percent") or 0
        ) / 100.0

        effectiveness = item.get(
            "effectiveness_score"
        )

        if effectiveness is None:
            effectiveness_norm = 0.50
            feedback_confidence = 0.0
        else:
            effectiveness_norm = (
                float(effectiveness) / 100.0
            )
            feedback_confidence = min(
                rated_calls / 10.0,
                1.0,
            )

        latency = float(
            item.get("avg_latency_ms")
            or max_latency
        )

        latency_score = (
            1.0
            - min(
                latency / max(max_latency, 1.0),
                1.0,
            )
        )

        spend = float(
            item.get("shadow_spend_usd")
            or 0
        )

        if max_shadow_spend > 0:
            cost_score = (
                1.0
                - min(
                    spend / max_shadow_spend,
                    1.0,
                )
            )
        else:
            cost_score = 1.0

        observed_value = (
            effectiveness_norm
            * feedback_confidence
            + 0.50
            * (1.0 - feedback_confidence)
        )

        routing_score = (
            observed_value * 0.55
            + success * 0.20
            + latency_score * 0.10
            + cost_score * 0.15
        ) * 100

        recommendations = []

        if rated_calls < 5:
            recommendations.append(
                "Collect more usefulness ratings "
                "before changing routing priority."
            )

        if success < 0.90 and calls >= 3:
            recommendations.append(
                "Provider reliability is below 90%; "
                "keep a fallback behind it."
            )

        token_error = item.get(
            "avg_token_estimate_error_percent"
        )

        if (
            token_error is not None
            and float(token_error) > 25
        ):
            recommendations.append(
                "Token estimator error exceeds 25%; "
                "calibrate this provider's token factor."
            )

        if (
            effectiveness is not None
            and float(effectiveness) < 60
            and rated_calls >= 5
        ):
            recommendations.append(
                "Observed usefulness is weak; "
                "lower routing priority or restrict "
                "this provider to suitable task types."
            )

        ranked.append({
            **item,
            "routing_score": round(
                routing_score,
                2,
            ),
            "feedback_confidence_percent":
                round(
                    feedback_confidence * 100,
                    2,
                ),
            "recommendations":
                recommendations,
        })

    ranked.sort(
        key=lambda item: item["routing_score"],
        reverse=True,
    )

    return {
        "recommended_order": [
            item["provider"]
            for item in ranked
        ],
        "providers": ranked,
        "notes": [
            (
                "Routing score uses observed usefulness, "
                "success rate, latency and BOUND shadow "
                "cost. It is an optimisation aid, not "
                "vendor billing or an objective model "
                "quality benchmark."
            ),
            (
                "Unrated providers are intentionally "
                "treated as uncertain rather than bad."
            ),
        ],
    }
