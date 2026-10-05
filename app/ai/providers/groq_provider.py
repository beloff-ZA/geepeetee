import os

from app.ai.prompt import SYSTEM_PROMPT
from app.ai.providers.base import ProviderError, ProviderResponse
from app.ai.providers.compat import call_openai_compatible_chat


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        model: str | None = None,
    ):
        self.model = (
            model
            or os.getenv(
                "GROQ_MODEL",
                "openai/gpt-oss-20b",
            )
        )

    @property
    def available(self) -> bool:
        return bool(os.getenv("GROQ_API_KEY"))

    def generate(
        self,
        *,
        message: str,
        conversation_context: list[dict] | None = None,
    ) -> ProviderResponse:
        key = os.getenv("GROQ_API_KEY")

        if not key:
            raise ProviderError(
                self.name,
                "GROQ_API_KEY is not configured",
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
                "https://api.groq.com/openai/v1/"
                "chat/completions"
            ),
            api_key=key,
            model=self.model,
            system_prompt=SYSTEM_PROMPT,
            messages=messages,
        )
