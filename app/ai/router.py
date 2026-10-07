import os

from app.ai.prompt import SYSTEM_PROMPT
from app.ai.providers import (
    FreeLLMAPIProvider,
    GeminiProvider,
    GroqProvider,
    OpenAIProvider,
    OpenRouterProvider,
    ProviderError,
)
from app.ai.state import disable_for_billing
from app.ai.usage import (
    ensure_usage_schema,
    estimate_output_tokens,
    estimate_request_tokens,
    monotonic_ms,
    record_usage_event,
    shadow_cost_usd,
)


class RouterExhausted(RuntimeError):
    def __init__(
        self,
        errors: list[dict],
    ):
        self.errors = errors
        super().__init__(
            "All eligible AI providers failed"
        )


def _provider_order() -> list[str]:
    raw = os.getenv(
        "BOUND_PROVIDER_ORDER",
        "freellmapi,openai,gemini,groq,openrouter",
    )

    return [
        item.strip().lower()
        for item in raw.split(",")
        if item.strip()
    ]


def _build_providers(
    openai_model: str | None,
) -> dict:
    providers = {
        "freellmapi": FreeLLMAPIProvider(),
        "gemini": GeminiProvider(),
        "groq": GroqProvider(),
        "openrouter": OpenRouterProvider(),
    }

    if openai_model:
        providers["openai"] = OpenAIProvider(
            openai_model
        )

    return providers


def provider_status(
    *,
    openai_model: str | None,
) -> list[dict]:
    providers = _build_providers(
        openai_model
    )

    order = _provider_order()
    results = []

    for name in order:
        provider = providers.get(name)

        if not provider:
            results.append({
                "provider": name,
                "available": False,
                "model": None,
                "reason": (
                    "Provider is not configured "
                    "for the current runtime state"
                ),
            })
            continue

        results.append({
            "provider": name,
            "available": provider.available,
            "model": provider.model,
            "reason": (
                None
                if provider.available
                else "API key is not configured"
            ),
        })

    return results


def route_request(
    *,
    message: str,
    conversation_context: list[dict] | None,
    conversation_id: str | None,
    openai_model: str | None,
    allow_openai: bool = True,
    system_prompt: str = SYSTEM_PROMPT,
) -> dict:
    ensure_usage_schema()

    providers = _build_providers(
        openai_model
    )

    order = _provider_order()

    errors: list[dict] = []
    fallback_reason = None

    for fallback_index, name in enumerate(order):
        if name == "openai" and not allow_openai:
            continue

        provider = providers.get(name)

        if not provider or not provider.available:
            continue

        estimated_input = estimate_request_tokens(
            provider=name,
            system_prompt=system_prompt,
            conversation_context=conversation_context,
            message=message,
        )

        started_ms = monotonic_ms()

        try:
            response = provider.generate(
                message=message,
                system_prompt=system_prompt,
                conversation_context=conversation_context,
            )

            duration_ms = (
                monotonic_ms() - started_ms
            )

            estimated_output = (
                estimate_output_tokens(
                    provider=name,
                    text=response.text,
                )
            )

            event_id = record_usage_event(
                conversation_id=conversation_id,
                provider=name,
                model=response.model,
                status="success",
                fallback_index=fallback_index,
                fallback_reason=fallback_reason,
                duration_ms=duration_ms,
                estimated_input_tokens=estimated_input,
                estimated_output_tokens=estimated_output,
                actual_input_tokens=response.input_tokens,
                actual_output_tokens=response.output_tokens,
                actual_total_tokens=response.total_tokens,
                cached_input_tokens=response.cached_input_tokens,
                response_id=response.response_id,
                error_message=None,
            )

            cost_input = (
                response.input_tokens
                if response.input_tokens is not None
                else estimated_input
            )

            cost_output = (
                response.output_tokens
                if response.output_tokens is not None
                else estimated_output
            )

            return {
                "provider": name,
                "model": response.model,
                "text": response.text,
                "response_id": response.response_id,
                "usage_event_id": event_id,
                "fallback": fallback_index > 0,
                "fallback_index": fallback_index,
                "fallback_reason": fallback_reason,
                "provider_metadata": response.metadata,
                "usage": {
                    "estimated_input_tokens":
                        estimated_input,
                    "estimated_output_tokens":
                        estimated_output,
                    "actual_input_tokens":
                        response.input_tokens,
                    "actual_output_tokens":
                        response.output_tokens,
                    "actual_total_tokens":
                        response.total_tokens,
                    "cached_input_tokens":
                        response.cached_input_tokens,
                    "source": (
                        "actual"
                        if (
                            response.input_tokens is not None
                            and response.output_tokens is not None
                        )
                        else "estimated"
                    ),
                    "shadow_cost_usd":
                        shadow_cost_usd(
                            provider=name,
                            input_tokens=cost_input,
                            output_tokens=cost_output,
                        ),
                },
            }

        except ProviderError as exc:
            duration_ms = (
                monotonic_ms() - started_ms
            )

            error_text = str(exc)[:1500]

            if name == "openai":
                lowered = error_text.lower()

                if (
                    "insufficient_quota" in lowered
                    or "credit_balance_exhausted" in lowered
                    or "no credits remaining" in lowered
                ):
                    disable_for_billing(
                        error_text
                    )

            record_usage_event(
                conversation_id=conversation_id,
                provider=name,
                model=provider.model,
                status="failed",
                fallback_index=fallback_index,
                fallback_reason=fallback_reason,
                duration_ms=duration_ms,
                estimated_input_tokens=estimated_input,
                estimated_output_tokens=0,
                actual_input_tokens=None,
                actual_output_tokens=None,
                actual_total_tokens=None,
                cached_input_tokens=None,
                response_id=None,
                error_message=error_text,
            )

            errors.append({
                "provider": name,
                "model": provider.model,
                "error": error_text,
                "status_code":
                    exc.status_code,
            })

            fallback_reason = (
                f"{name} failed: {error_text[:300]}"
            )

        except Exception as exc:
            duration_ms = (
                monotonic_ms() - started_ms
            )

            error_text = str(exc)[:1500]

            record_usage_event(
                conversation_id=conversation_id,
                provider=name,
                model=provider.model,
                status="failed",
                fallback_index=fallback_index,
                fallback_reason=fallback_reason,
                duration_ms=duration_ms,
                estimated_input_tokens=estimated_input,
                estimated_output_tokens=0,
                actual_input_tokens=None,
                actual_output_tokens=None,
                actual_total_tokens=None,
                cached_input_tokens=None,
                response_id=None,
                error_message=error_text,
            )

            errors.append({
                "provider": name,
                "model": provider.model,
                "error": error_text,
                "status_code": None,
            })

            fallback_reason = (
                f"{name} failed: {error_text[:300]}"
            )

    raise RouterExhausted(errors)
