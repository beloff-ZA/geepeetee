from app.db.database import (
    fetch_one,
    fetch_all,
    execute,
)


def create_conversation(
    title: str | None = None,
) -> dict:

    row = fetch_one(
        """
        INSERT INTO conversations (
            title
        )
        VALUES (%s)
        RETURNING
            id,
            title,
            created_at,
            updated_at
        """,
        (title,),
    )

    return dict(row)


def get_conversation(
    conversation_id: str,
) -> dict | None:

    row = fetch_one(
        """
        SELECT
            id,
            title,
            created_at,
            updated_at
        FROM conversations
        WHERE id = %s
        """,
        (conversation_id,),
    )

    if not row:
        return None

    return dict(row)


def list_conversations(
    limit: int = 50,
) -> list[dict]:

    rows = fetch_all(
        """
        SELECT
            id,
            title,
            created_at,
            updated_at
        FROM conversations
        ORDER BY updated_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    return [
        dict(row)
        for row in rows
    ]


def add_message(
    *,
    conversation_id: str,
    role: str,
    content: str,
    model: str | None = None,
    response_id: str | None = None,
) -> dict:

    row = fetch_one(
        """
        INSERT INTO messages (
            conversation_id,
            role,
            content,
            model,
            response_id
        )
        VALUES (
            %s, %s, %s, %s, %s
        )
        RETURNING
            id,
            conversation_id,
            role,
            content,
            model,
            response_id,
            created_at
        """,
        (
            conversation_id,
            role,
            content,
            model,
            response_id,
        ),
    )

    execute(
        """
        UPDATE conversations
        SET updated_at = NOW()
        WHERE id = %s
        """,
        (conversation_id,),
    )

    return dict(row)


def get_messages(
    conversation_id: str,
    limit: int = 100,
) -> list[dict]:

    rows = fetch_all(
        """
        SELECT
            id,
            conversation_id,
            role,
            content,
            model,
            response_id,
            created_at
        FROM messages
        WHERE conversation_id = %s
        ORDER BY created_at ASC
        LIMIT %s
        """,
        (
            conversation_id,
            limit,
        ),
    )

    return [
        dict(row)
        for row in rows
    ]


def get_recent_messages(
    conversation_id: str,
    limit: int = 20,
) -> list[dict]:
    """
    Return the newest messages while preserving chronological order.
    """

    rows = fetch_all(
        """
        SELECT *
        FROM (
            SELECT
                id,
                conversation_id,
                role,
                content,
                model,
                response_id,
                created_at
            FROM messages
            WHERE conversation_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        ) recent
        ORDER BY created_at ASC
        """,
        (
            conversation_id,
            limit,
        ),
    )

    return [
        dict(row)
        for row in rows
    ]


def set_title(
    conversation_id: str,
    title: str,
) -> None:

    execute(
        """
        UPDATE conversations
        SET
            title = %s,
            updated_at = NOW()
        WHERE id = %s
        """,
        (
            title,
            conversation_id,
        ),
    )
