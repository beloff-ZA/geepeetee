import httpx

from app.ai.providers.base import ProviderError, ProviderResponse


def call_openai_compatible_chat(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    timeout: float = 90.0,
    extra_headers: dict[str, str] | None = None,
) -> ProviderResponse:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            *messages,
        ],
    }

    try:
        response = httpx.post(
            base_url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise ProviderError(
            provider,
            f"{provider} network error: {exc}",
            retryable=True,
        ) from exc

    if response.status_code >= 400:
        retryable = response.status_code in {
            408,
            409,
            425,
            429,
            500,
            502,
            503,
            504,
        }

        body = response.text[:1500]

        raise ProviderError(
            provider,
            (
                f"{provider} returned HTTP "
                f"{response.status_code}: {body}"
            ),
            retryable=retryable,
            status_code=response.status_code,
        )

    data = response.json()

    choices = data.get("choices") or []

    if not choices:
        raise ProviderError(
            provider,
            f"{provider} returned no choices",
            retryable=False,
        )

    message = choices[0].get("message") or {}
    text = message.get("content") or ""

    usage = data.get("usage") or {}

    input_tokens = (
        usage.get("prompt_tokens")
        or usage.get("input_tokens")
    )

    output_tokens = (
        usage.get("completion_tokens")
        or usage.get("output_tokens")
    )

    total_tokens = usage.get("total_tokens")

    cached_input_tokens = None

    prompt_details = usage.get("prompt_tokens_details") or {}

    if isinstance(prompt_details, dict):
        cached_input_tokens = (
            prompt_details.get("cached_tokens")
        )

    route_metadata = {}

    if provider == "freellmapi":
        for header, key in (
            ("X-Routed-Via", "routed_via"),
            ("X-Fallback-Attempts", "fallback_attempts"),
            ("X-Fallback-Trail", "fallback_trail"),
            ("X-Fallback-Detail", "fallback_detail"),
        ):
            value = response.headers.get(header)
            if value:
                route_metadata[key] = value

    return ProviderResponse(
        provider=provider,
        model=data.get("model") or model,
        text=text,
        response_id=data.get("id"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cached_input_tokens=cached_input_tokens,
        raw_usage=usage,
        metadata=route_metadata,
    )
