import pytest

from app.context import trim_history


def turns():
    return [
        {"role": role, "content": content}
        for role, content in [
            ("user", "old"),
            ("assistant", "one"),
            ("user", "new"),
            ("assistant", "two"),
        ]
    ]


@pytest.mark.parametrize("count,chars", [(2, 100), (20, 6), (3, 100), (2, 6)])
def test_fifo_removes_whole_oldest_pairs(count, chars):
    history = turns()
    assert trim_history(history, count, chars) == history[2:]
    assert len(history) == 4


@pytest.mark.parametrize("count,chars", [(0, 100), (20, 0), (1, 100), (20, 5)])
def test_pair_that_does_not_fit_is_not_split(count, chars):
    assert trim_history(turns(), count, chars) == []


def test_failed_and_pending_user_requests_do_not_form_false_turns():
    history = [
        {"role": "assistant", "content": "orphan"},
        {"role": "user", "content": "failed"},
        *turns(),
        {"role": "user", "content": "pending"},
    ]
    assert trim_history(history, 40, 8000) == turns()
