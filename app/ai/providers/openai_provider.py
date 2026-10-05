import os

from openai import OpenAI, RateLimitError

from app.ai.providers.base import ProviderError, ProviderResponse


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str):
        self.model = model

    @property
    def available(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def generate(
        self,
        *,
        message: str,
        system_prompt: str,
        conversation_context: list[dict] | None = None,
    ) -> ProviderResponse:
        key = os.getenv("OPENAI_API_KEY")

        if not key:
            raise ProviderError(
                self.name,
                "OPENAI_API_KEY is not configured",
                retryable=False,
            )

        input_messages: list[dict] = []

        if conversation_context:
            input_messages.extend(conversation_context)

        input_messages.append({
            "role": "user",
            "content": message,
        })

        client = OpenAI(api_key=key)

        try:
            response = client.responses.create(
                model=self.model,
                instructions=system_prompt,
                input=input_messages,
            )

        except RateLimitError as exc:
            raise ProviderError(
                self.name,
                str(exc),
                retryable=True,
                status_code=429,
            ) from exc

        except Exception as exc:
            raise ProviderError(
                self.name,
                str(exc),
                retryable=True,
            ) from exc

        usage_obj = getattr(
            response,
            "usage",
            None,
        )

        input_tokens = getattr(
            usage_obj,
            "input_tokens",
            None,
        )

        output_tokens = getattr(
            usage_obj,
            "output_tokens",
            None,
        )

        total_tokens = getattr(
            usage_obj,
            "total_tokens",
            None,
        )

        cached_input_tokens = None

        details = getattr(
            usage_obj,
            "input_tokens_details",
            None,
        )

        if details is not None:
            cached_input_tokens = getattr(
                details,
                "cached_tokens",
                None,
            )

        raw_usage = {}

        if usage_obj is not None:
            try:
                raw_usage = (
                    usage_obj.model_dump()
                )
            except Exception:
                raw_usage = {}

        return ProviderResponse(
            provider=self.name,
            model=self.model,
            text=response.output_text,
            response_id=response.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cached_input_tokens=cached_input_tokens,
            raw_usage=raw_usage,
        )
