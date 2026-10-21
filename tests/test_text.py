import pytest

from app.text import split_text


@pytest.mark.parametrize(
    "text",
    ["", "x" * 4096, "x" * 4097, "🎸\n " * 5000, "a\n" * 9000],
    ids=["empty", "boundary", "over-boundary", "unicode", "multiline"],
)
def test_chunks_preserve_every_character_and_order(text):
    parts = split_text(text)
    assert "".join(parts) == text
    assert all(part and len(part.encode("utf-16-le")) // 2 <= 4096 for part in parts)


def test_exact_ascii_boundaries():
    assert split_text("a" * 4096 + "b") == ["a" * 4096, "b"]
