from html.parser import HTMLParser
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.rendering import answer_formatted, format_chunks


class CheckedHTML(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.text = ""
        self.stack = []
        self.feed(html)
        assert self.stack == []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "code", "pre", "a"}
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.text += data


def test_headings_bold_lists_links_and_code_are_rendered_as_telegram_html():
    text = "## Plan\n1. **Bass** and *kick*.\n- `EQ < 100`\n[Source](https://example.org/?a=1&b=2)"
    chunks = format_chunks(text)
    assert len(chunks) == 1
    assert "<b>Plan</b>" in chunks[0].html
    assert "<b>Bass</b>" in chunks[0].html and "<i>kick</i>" in chunks[0].html
    assert '<a href="https://example.org/?a=1&amp;b=2">Source</a>' in chunks[0].html
    assert CheckedHTML(chunks[0].html).text == chunks[0].text
    assert chunks[0].text == "Plan\n1. Bass and kick.\n• EQ < 100\nSource"


def test_untrusted_html_and_unsafe_links_cannot_become_html_entities():
    chunk = format_chunks("<script>attack</script> [bad](javascript:alert) [x](https://u:p@host)")[
        0
    ]
    assert "<script>" not in chunk.html and "href=" not in chunk.html
    assert CheckedHTML(chunk.html).text == "<script>attack</script> bad x"


def test_invalid_port_is_not_rendered_as_a_clickable_link():
    chunk = format_chunks("[source](https://example.org:invalid/path)")[0]
    assert chunk.html == chunk.text == "source"


@pytest.mark.parametrize("separator", ["\n\n", "\n", " "], ids=["paragraph", "line", "word"])
def test_chunks_prefer_readable_boundaries_without_losing_whitespace(separator):
    first = "a" * 55 + separator
    source = "**" + first + "b" * 70 + "**"
    chunks = format_chunks(source, limit=100)
    assert chunks[0].text == first
    assert "".join(chunk.text for chunk in chunks) == first + "b" * 70
    assert all(CheckedHTML(chunk.html).text == chunk.text for chunk in chunks)


def test_large_unbroken_unicode_word_is_split_without_exceeding_telegram_limit():
    chunks = format_chunks("**" + "🎸" * 5000 + "**")
    assert "".join(chunk.text for chunk in chunks) == "🎸" * 5000
    assert all(len(chunk.text.encode("utf-16-le")) // 2 <= 4096 for chunk in chunks)


@pytest.mark.parametrize(
    "source",
    ["**" + "🎸&<>" * 3000 + "**", "```python\n" + "x<&\n" * 5000],
    ids=["long-bold-unicode", "unclosed-code"],
)
def test_long_bold_and_unclosed_code_chunks_preserve_visible_text_and_balanced_tags(source):
    chunks = format_chunks(source)
    assert len(chunks) > 1
    for chunk in chunks:
        assert CheckedHTML(chunk.html).text == chunk.text
        assert len(chunk.text.encode("utf-16-le")) // 2 <= 4096
    expected = source[2:-2] if source.startswith("**") else source.split("\n", 1)[1]
    assert "".join(chunk.text for chunk in chunks) == expected


def test_code_and_escaped_markdown_are_not_reformatted():
    chunk = format_chunks("`**literal**` and \\*literal\\* and user_name")[0]
    assert "<code>**literal**</code>" in chunk.html
    assert chunk.text == "**literal** and *literal* and user_name"


async def test_telegram_entity_rejection_falls_back_to_clean_plain_text():
    error = TelegramBadRequest(
        method=SendMessage(chat_id=1, text="unused"), message="Bad Request: can't parse entities"
    )
    message = AsyncMock()
    message.answer.side_effect = [error, None]
    await answer_formatted(message, "## **Title**")
    assert message.answer.call_args_list[0].kwargs["parse_mode"] == "HTML"
    assert message.answer.call_args_list[1].args == ("Title",)
    assert message.answer.call_args_list[1].kwargs["parse_mode"] is None


async def test_other_telegram_errors_are_not_retried():
    error = TelegramBadRequest(
        method=SendMessage(chat_id=1, text="unused"), message="chat not found"
    )
    message = AsyncMock()
    message.answer.side_effect = error
    with pytest.raises(TelegramBadRequest):
        await answer_formatted(message, "answer")
    message.answer.assert_awaited_once()
