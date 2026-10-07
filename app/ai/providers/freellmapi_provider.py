import os

from app.ai.providers.base import ProviderError, ProviderResponse
from app.ai.providers.compat import call_openai_compatible_chat


class FreeLLMAPIProvider:
    """Local OpenAI-compatible inference gateway.

    BOUND treats FreeLLMAPI as an inference service, not an authority surface.
    Provider keys remain inside FreeLLMAPI; BOUND only holds its unified bearer token.
    """

    name = "freellmapi"

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("FREELLMAPI_MODEL", "auto")

    @property
    def base_url(self) -> str:
        return os.getenv(
            "FREELLMAPI_BASE_URL",
            "http://127.0.0.1:3001/v1",
        ).rstrip("/")

    @property
    def available(self) -> bool:
        return bool(
            os.getenv("FREELLMAPI_API_KEY")
            and self.base_url
        )

    def generate(
        self,
        *,
        message: str,
        system_prompt: str,
        conversation_context: list[dict] | None = None,
    ) -> ProviderResponse:
        key = os.getenv("FREELLMAPI_API_KEY")

        if not key:
            raise ProviderError(
                self.name,
                "FREELLMAPI_API_KEY is not configured",
                retryable=False,
            )

        messages = list(conversation_context or [])
        messages.append({
            "role": "user",
            "content": message,
        })

        return call_openai_compatible_chat(
            provider=self.name,
            base_url=f"{self.base_url}/chat/completions",
            api_key=key,
            model=self.model,
            system_prompt=system_prompt,
            messages=messages,
            timeout=float(
                os.getenv(
                    "FREELLMAPI_TIMEOUT_SECONDS",
                    "120",
                )
            ),
        )
