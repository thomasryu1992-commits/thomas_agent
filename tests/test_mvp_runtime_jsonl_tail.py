"""`jsonl.tail_objects` / `count_lines`: the newest N of an append-only store, read from the
end, lock-free — the read door's `scheduler_events` shape (measured 2026-09-04: 20 ms → 0.1 ms)."""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import jsonl
from runtime.mvp_runtime.errors import PersistenceError
from runtime.mvp_runtime.store import LedgerStore


def _write(path, rows, *, tail=b""):
    path.write_bytes(b"".join(json.dumps(r, ensure_ascii=False).encode() + b"\n" for r in rows) + tail)


def test_the_tail_is_the_newest_n_in_order_and_matches_a_full_read(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [{"i": i, "text": "한글 " * (i % 7)} for i in range(500)]
    _write(path, rows)
    full = jsonl.read_objects(path, read_code="X", label="t")
    for n in (1, 20, 100, 499, 500, 5000):
        assert jsonl.tail_objects(path, n, read_code="X", label="t") == full[-n:]
    assert jsonl.tail_objects(path, 0, read_code="X", label="t") == []
    assert jsonl.count_lines(path, read_code="X", label="t") == 500


def test_the_tail_crosses_chunk_boundaries_on_a_file_larger_than_one_chunk(tmp_path):
    path = tmp_path / "big.jsonl"
    rows = [{"i": i, "pad": "x" * 900} for i in range(400)]      # ~360 KB, several 64 KiB chunks
    _write(path, rows)
    assert [r["i"] for r in jsonl.tail_objects(path, 150, read_code="X", label="t")] == list(range(250, 400))
    assert [r["i"] for r in jsonl.tail_objects(path, 3, read_code="X", label="t")] == [397, 398, 399]


def test_a_torn_final_line_is_dropped_not_parsed_and_not_counted(tmp_path):
    path = tmp_path / "torn.jsonl"
    _write(path, [{"i": 1}, {"i": 2}], tail=b'{"i": 3, "half')
    assert jsonl.tail_objects(path, 5, read_code="X", label="t") == [{"i": 1}, {"i": 2}]
    assert jsonl.count_lines(path, read_code="X", label="t") == 2


def test_a_corrupt_complete_line_in_the_tail_still_fails_closed(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b'{"i": 1}\nnot json\n{"i": 3}\n')
    with pytest.raises(PersistenceError) as exc:
        jsonl.tail_objects(path, 3, read_code="LEDGER_UNREADABLE", label="t")
    assert exc.value.reason_code == "LEDGER_UNREADABLE"
    # ...but a bad line ABOVE the tail is not this reader's business, by design.
    assert jsonl.tail_objects(path, 1, read_code="LEDGER_UNREADABLE", label="t") == [{"i": 3}]


def test_absent_and_blank_lined_files(tmp_path):
    assert jsonl.tail_objects(tmp_path / "nope.jsonl", 5, read_code="X", label="t") == []
    assert jsonl.count_lines(tmp_path / "nope.jsonl", read_code="X", label="t") == 0
    path = tmp_path / "blanks.jsonl"
    path.write_bytes(b'\n{"i": 1}\n\n{"i": 2}\n\n')
    assert jsonl.tail_objects(path, 5, read_code="X", label="t") == [{"i": 1}, {"i": 2}]


def test_the_ledger_tail_agrees_with_the_locked_full_read(tmp_path):
    ledger = LedgerStore(tmp_path)
    for i in range(30):
        ledger.append_scheduler_event({"record_type": "scheduler_event.v0", "action": "fired", "schedule_id": f"s{i}",
                                       "kind": "crypto_pipeline", "status": "ok", "created_at": f"2026-09-04T10:{i:02d}:00Z"})
    assert ledger.read_scheduler_events_tail(5) == ledger.read_scheduler_events()[-5:]
    assert ledger.count_scheduler_events() == 30
    assert ledger.read_scheduler_events_tail(0) == [] and LedgerStore(tmp_path / "empty").read_scheduler_events_tail(5) == []
