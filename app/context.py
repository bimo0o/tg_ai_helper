from collections.abc import Sequence


def trim_history(
    messages: Sequence[dict[str, str]], limit_messages: int, limit_chars: int
) -> list[dict[str, str]]:
    """Return the newest complete turns within both budgets, in original order."""
    pairs = []
    pending = None
    for message in messages:
        if message["role"] == "user":
            pending = message
        elif message["role"] == "assistant" and pending is not None:
            pairs.append((pending, message))
            pending = None
    selected = []
    total_chars = 0
    for user, assistant in reversed(pairs):
        size = len(user["content"]) + len(assistant["content"])
        if len(selected) + 2 > limit_messages or total_chars + size > limit_chars:
            break
        selected[0:0] = [user, assistant]
        total_chars += size
    return [dict(message) for message in selected]
