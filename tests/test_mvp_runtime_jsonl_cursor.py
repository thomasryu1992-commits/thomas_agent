"""`jsonl.AppendCursor` — reading an append-only store one increment at a time.

The task registry used to re-parse its whole file on every read, under its lock, and the operator
reads it every batch. The cursor parses only what was appended since the last read. What it must
never do is disagree with a full read: every property below compares against one.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import jsonl
from runtime.mvp_runtime.errors import PersistenceError

CODES = {"read_code": "TEST_UNREADABLE", "label": "the test store"}


def _append(path, *objects):
    jsonl.append_lines(path, list(objects), write_code="TEST_WRITE_FAILED", label="the test store")


def test_each_read_returns_only_what_was_appended_since_the_last(tmp_path):
    path = tmp_path / "store.jsonl"
    cursor = jsonl.AppendCursor(path)
    assert cursor.read_new(**CODES) == (False, [])            # absent file: nothing, no restart
    _append(path, {"n": 1}, {"n": 2})
    assert cursor.read_new(**CODES) == (False, [{"n": 1}, {"n": 2}])
    assert cursor.read_new(**CODES) == (False, [])
    _append(path, {"n": 3})
    assert cursor.read_new(**CODES) == (False, [{"n": 3}])


def test_a_replaced_or_shortened_file_restarts_and_says_so(tmp_path):
    """Append-only is the premise; when it visibly breaks, the caller must drop what it folded."""
    path = tmp_path / "store.jsonl"
    cursor = jsonl.AppendCursor(path)
    _append(path, {"n": 1}, {"n": 2})
    cursor.read_new(**CODES)

    jsonl.write_objects(path, [{"n": 9}], write_code="W", label="the test store")   # atomic replace
    assert cursor.read_new(**CODES) == (True, [{"n": 9}])

    path.write_text("", encoding="utf-8")                                            # truncated in place
    assert cursor.read_new(**CODES) == (True, [])

    path.unlink()
    assert cursor.read_new(**CODES) == (True, [])


def test_a_corrupt_row_raises_and_the_cursor_does_not_move(tmp_path):
    path = tmp_path / "store.jsonl"
    cursor = jsonl.AppendCursor(path)
    _append(path, {"n": 1})
    cursor.read_new(**CODES)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
    for _ in range(2):                                        # refused every time, never skipped
        with pytest.raises(PersistenceError) as exc:
            cursor.read_new(**CODES)
        assert exc.value.reason_code == "TEST_UNREADABLE"


def test_a_complete_row_whose_newline_has_not_landed_is_read_once(tmp_path):
    """As a full read counts it. Its newline, when it lands, is a blank line to the next read."""
    path = tmp_path / "store.jsonl"
    cursor = jsonl.AppendCursor(path)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"n": 1}))
    assert cursor.read_new(**CODES) == (False, [{"n": 1}])
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + json.dumps({"n": 2}) + "\n")
    assert cursor.read_new(**CODES) == (False, [{"n": 2}])


def test_many_increments_fold_to_exactly_what_one_full_read_sees(tmp_path):
    path = tmp_path / "store.jsonl"
    cursor = jsonl.AppendCursor(path)
    seen = []
    for batch in range(20):
        _append(path, *({"batch": batch, "i": i} for i in range(batch % 4)))
        seen.extend(cursor.read_new(**CODES)[1])
    assert seen == jsonl.read_objects(path, **CODES)
