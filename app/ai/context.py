from app.db.conversations import get_recent_messages


MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_CHARS = 30000


def build_conversation_context(
    conversation_id: str,
) -> list[dict]:
    """
    Build bounded conversational context for model inference.

    Call this before storing the current user message so that the
    latest message is not sent to the model twice.
    """

    messages = get_recent_messages(
        conversation_id,
        limit=MAX_CONTEXT_MESSAGES,
    )

    context: list[dict] = []
    used_chars = 0

    for message in reversed(messages):
        role = message["role"]

        if role not in {"user", "assistant"}:
            continue

        text = message["content"] or ""

        if used_chars + len(text) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - used_chars

            if remaining <= 0:
                break

            text = text[-remaining:]

        context.append({
            "role": role,
            "content": text,
        })

        used_chars += len(text)

        if used_chars >= MAX_CONTEXT_CHARS:
            break

    context.reverse()

    return context
