def split_text(text: str, limit: int = 4096) -> list[str]:
    """Split without losing whitespace; account for astral Unicode characters."""
    if limit < 2:
        raise ValueError("Message limit must be at least 2.")
    parts = []
    start = units = 0
    for index, character in enumerate(text):
        width = 2 if ord(character) > 0xFFFF else 1
        if units + width > limit:
            parts.append(text[start:index])
            start, units = index, 0
        units += width
    if start < len(text):
        parts.append(text[start:])
    return parts
