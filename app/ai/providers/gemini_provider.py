import os

from app.ai.providers.base import ProviderError, ProviderResponse
from app.ai.providers.compat import call_openai_compatible_chat


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        model: str | None = None,
    ):
        self.model = (
            model
            or os.getenv(
                "GEMINI_MODEL",
                "gemini-3.8-flash",
            )
        )

    @property
    def available(self) -> bool:
        return bool(os.getenv("GEMINI_API_KEY"))

    def generate(
        self,
        *,
        message: str,
        system_prompt: str,
        conversation_context: list[dict] | None = None,
    ) -> ProviderResponse:
        key = os.getenv("GEMINI_API_KEY")

        if not key:
            raise ProviderError(
                self.name,
                "GEMINI_API_KEY is not configured",
                retryable=False,
            )

        messages = list(
            conversation_context or []
        )

        messages.append({
            "role": "user",
            "content": message,
        })

        return call_openai_compatible_chat(
            provider=self.name,
            base_url=(
                "https://generativelanguage.googleapis.com/"
                "v1beta/openai/chat/completions"
            ),
            api_key=key,
            model=self.model,
            system_prompt=system_prompt,
            messages=messages,
        )
