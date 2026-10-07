from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    def __init__(
        self,
        provider: str,
        message: str,
        *,
        retryable: bool = True,
        status_code: int | None = None,
    ):
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(message)


@dataclass
class ProviderResponse:
    provider: str
    model: str
    text: str
    response_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
