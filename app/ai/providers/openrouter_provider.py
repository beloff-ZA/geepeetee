import os

from app.ai.providers.base import ProviderError, ProviderResponse
from app.ai.providers.compat import call_openai_compatible_chat


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        model: str | None = None,
    ):
        self.model = (
            model
            or os.getenv(
                "OPENROUTER_MODEL",
                "openrouter/free",
            )
        )

    @property
    def available(self) -> bool:
        return bool(
            os.getenv("OPENROUTER_API_KEY")
        )

    def generate(
        self,
        *,
        message: str,
        system_prompt: str,
        conversation_context: list[dict] | None = None,
    ) -> ProviderResponse:
        key = os.getenv(
            "OPENROUTER_API_KEY"
        )

        if not key:
            raise ProviderError(
                self.name,
                "OPENROUTER_API_KEY is not configured",
                retryable=False,
            )

        messages = list(
            conversation_context or []
        )

        messages.append({
            "role": "user",
            "content": message,
        })

        extra_headers = {}

        site_url = os.getenv(
            "OPENROUTER_SITE_URL"
        )

        app_name = os.getenv(
            "OPENROUTER_APP_NAME",
            "BOUND Operator",
        )

        if site_url:
            extra_headers[
                "HTTP-Referer"
            ] = site_url

        if app_name:
            extra_headers[
                "X-Title"
            ] = app_name

        return call_openai_compatible_chat(
            provider=self.name,
            base_url=(
                "https://openrouter.ai/api/v1/"
                "chat/completions"
            ),
            api_key=key,
            model=self.model,
            system_prompt=system_prompt,
            messages=messages,
            extra_headers=extra_headers,
        )
