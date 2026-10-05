from app.db.conversations import get_messages


MAX_CONTEXT_MESSAGES = 20


def build_conversation_context(
    conversation_id: str,
) -> list[dict]:
    messages = get_messages(
        conversation_id,
        limit=MAX_CONTEXT_MESSAGES,
    )

    context = []

    for message in messages:
        role = message["role"]

        if role not in {
            "user",
            "assistant",
            "system",
        }:
            continue

        context.append({
            "role": role,
            "content": message["content"],
        })

    return context

