"""`jsonl.iter_numbered` and an unterminated last line: an append still landing is waited for,
one that died still fails closed.

A reader that does not hold the appender's lock can see an append half-landed — the kernel grows
the file a page at a time while one write is copied in. Measured 2026-09-18 on this host: 748 of
10,885 reads on tmpfs and 1,352 of 39,561 on ext4 found a last line without its newline, against
a writer appending ~2.6 KB lines without pause. Every such read used to be refused as a corrupt
store (APPROVAL_READ_FAILED, WORKING_MEMORY_UNREADABLE, ...).

These tests are deterministic. The other process is a stub whose bytes land when the reader
sleeps, and time only passes when the reader sleeps, so nothing depends on real timing.
"""

from __future__ import annotations

import json
import time

import pytest

from runtime.mvp_runtime import jsonl
from runtime.mvp_runtime.approval_store import ApprovalStore
from runtime.mvp_runtime.errors import PersistenceError, ToolError
from runtime.mvp_runtime.working_memory import WorkingMemoryStore


def _line(obj) -> bytes:
    """One row exactly as `append_lines` writes it."""
    return (json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")


class _OtherWriter:
    """The appending process. Each time the reader sleeps, the next landing reaches the file."""

    def __init__(self, path, landings=()):
        self.path = path
        self.landings = list(landings)
        self.sleeps = 0
        self.now = 1000.0

    def sleep(self, seconds):
        self.sleeps += 1
        self.now += seconds
        if self.landings:
            with open(self.path, "ab") as fh:
                fh.write(self.landings.pop(0))



@pytest.fixture
def other_writer(monkeypatch):
    def install(path, *landings):
        writer = _OtherWriter(path, landings)
        monkeypatch.setattr(time, "sleep", writer.sleep)
        return writer
    return install


def _half(row: bytes, at: int) -> tuple[bytes, bytes]:
    return row[:at], row[at:]


def test_a_last_line_still_being_appended_is_waited_for_not_refused(tmp_path, other_writer):
    path = tmp_path / "s.jsonl"
    third = _line({"i": 3, "pad": "x" * 6000})
    # The page boundary falls inside the third row: the first page has landed, the rest has not.
    landed, rest = _half(third, 4096 - 2 * len(_line({"i": 1})))
    assert rest
    path.write_bytes(_line({"i": 1}) + _line({"i": 2}) + landed)
    writer = other_writer(path, rest)

    rows = list(jsonl.iter_numbered(path, read_code="R", label="s"))

    assert [(n, r["i"]) for n, r in rows] == [(1, 1), (2, 2), (3, 3)]
    assert writer.sleeps == 1


def test_a_line_that_lands_in_several_pieces_is_waited_for_until_its_newline(tmp_path, other_writer):
    path = tmp_path / "s.jsonl"
    row = _line({"i": 1, "pad": "y" * 9000})
    path.write_bytes(row[:4096])
    writer = other_writer(path, row[4096:8192], row[8192:])

    assert jsonl.read_objects(path, read_code="R", label="s") == [{"i": 1, "pad": "y" * 9000}]
    assert writer.sleeps == 2


def test_a_tear_inside_a_multibyte_character_is_waited_for_too(tmp_path, other_writer):
    """Korean text is ordinary in these stores. Read as text, a tear between the bytes of one
    Hangul syllable escaped the reader as a bare UnicodeDecodeError instead of the store's code."""
    path = tmp_path / "s.jsonl"
    row = _line({"note": "승인 요청 — 한국어 본문"})
    cut = row.index("한".encode("utf-8")) + 1
    path.write_bytes(_line({"note": "첫 줄"}) + row[:cut])
    other_writer(path, row[cut:])

    assert jsonl.read_objects(path, read_code="R", label="s") == [
        {"note": "첫 줄"}, {"note": "승인 요청 — 한국어 본문"},
    ]


def test_a_last_line_that_is_never_finished_still_fails_closed_after_the_patience(tmp_path, other_writer):
    """The crash-truncated store: the writer died mid-append. Waiting must not turn that into a
    store that reads as whole — it raises the caller's code, as before, and only after waiting."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(_line({"i": 1}) + b'{"i": 2, "half')
    writer = other_writer(path)
    started = writer.now

    stream = jsonl.iter_objects(path, read_code="WORKING_MEMORY_UNREADABLE", label="working memory")
    assert next(stream) == {"i": 1}
    with pytest.raises(PersistenceError) as exc:
        next(stream)

    assert exc.value.reason_code == "WORKING_MEMORY_UNREADABLE"
    assert "line 2" in str(exc.value) and "not finished" in str(exc.value)
    # It waited the whole patience, one look per poll, and no more.
    assert writer.sleeps == jsonl._TAIL_POLLS
    assert writer.now - started == pytest.approx(jsonl._TAIL_PATIENCE_SECONDS)


def test_a_frozen_clock_cannot_keep_a_reader_waiting(tmp_path, other_writer, monkeypatch):
    """Tests freeze `time.monotonic`. A wait bounded by that clock would never end on a store a
    crash cut off, so the wait is a count of looks and ends whatever the clock says. If it ever
    stops ending, this fails rather than hangs."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(b'{"i": 1, "half')
    writer = other_writer(path)
    monkeypatch.setattr(time, "monotonic", lambda: 42.0)

    def bounded_sleep(seconds):
        if writer.sleeps >= 10 * jsonl._TAIL_POLLS:
            raise AssertionError("the reader kept waiting")
        writer.sleep(seconds)
    monkeypatch.setattr(time, "sleep", bounded_sleep)

    with pytest.raises(PersistenceError):
        jsonl.read_objects(path, read_code="R", label="s")
    assert writer.sleeps == jsonl._TAIL_POLLS


def test_a_cut_character_that_is_never_finished_raises_the_stores_code(tmp_path, other_writer):
    path = tmp_path / "s.jsonl"
    row = _line({"note": "한국어"})
    path.write_bytes(row[: row.index("한".encode("utf-8")) + 2])
    other_writer(path)

    with pytest.raises(PersistenceError) as exc:
        jsonl.read_objects(path, read_code="APPROVAL_READ_FAILED", label="approval store")
    assert exc.value.reason_code == "APPROVAL_READ_FAILED"
    with pytest.raises(ToolError) as tool_exc:
        jsonl.read_objects(path, read_code="CF_UNREADABLE", label="t", exc_type=ToolError)
    assert tool_exc.value.reason_code == "CF_UNREADABLE"


def test_a_finished_line_that_still_does_not_parse_is_corruption(tmp_path, other_writer):
    """Two unlocked writers can interleave into a line that ends properly and parses as nothing.
    Finishing is not the same as being whole."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(b'{"i": 1, "b"')
    other_writer(path, b'{"j": 2}\n')

    with pytest.raises(PersistenceError) as exc:
        jsonl.read_objects(path, read_code="R", label="s")
    assert "line 1 is not valid JSON" in str(exc.value)
    assert "not finished" not in str(exc.value)


def test_a_bad_line_above_the_last_is_refused_at_once(tmp_path, other_writer):
    """Only the last line can be an append in flight. Every other bad line is corruption, and
    waiting for it would only delay the refusal — so the reader does not."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(_line({"i": 1}) + b'{not json\n' + _line({"i": 3}))
    writer = other_writer(path)
    with pytest.raises(PersistenceError) as exc:
        jsonl.read_objects(path, read_code="R", label="s")
    assert "line 2" in str(exc.value)

    # A byte that is not UTF-8 is that line's failure, with the store's code — not a bare
    # UnicodeDecodeError from the decoder.
    path.write_bytes(_line({"i": 1}) + b'{"i": "\xff"}\n')
    with pytest.raises(PersistenceError) as exc:
        jsonl.read_objects(path, read_code="R", label="s")
    assert "line 2" in str(exc.value)
    assert writer.sleeps == 0


def test_a_last_line_that_parses_is_read_without_waiting(tmp_path, other_writer):
    """The tear can land between a row's closing brace and its newline. That row is complete."""
    path = tmp_path / "s.jsonl"
    path.write_bytes(_line({"i": 1}) + _line({"i": 2})[:-1])
    writer = other_writer(path)
    assert jsonl.read_objects(path, read_code="R", label="s") == [{"i": 1}, {"i": 2}]
    assert writer.sleeps == 0


def test_rows_appended_after_the_unfinished_line_are_not_read(tmp_path, other_writer):
    """The read ends where the append it waited for ended, so a writer that never stops cannot
    keep a reader waiting."""
    path = tmp_path / "s.jsonl"
    second = _line({"i": 2})
    path.write_bytes(_line({"i": 1}) + second[:5])
    other_writer(path, second[5:] + _line({"i": 3}) + b'{"i": 4')

    assert [r["i"] for r in jsonl.read_objects(path, read_code="R", label="s")] == [1, 2]


def test_the_prescreen_never_skips_an_unfinished_last_line(tmp_path, other_writer):
    """A tear can end in a brace it does not own and still balance — here the brace sits inside
    a string. The screen used to skip such a line unparsed, which hid a store cut off there from
    every filtered reader. It now reaches the parser."""
    path = tmp_path / "s.jsonl"
    row = _line({"a_note": "}", "kind": "wanted"})
    landed = row[: row.index(b'"}"') + 2]
    assert landed.endswith(b"}") and landed.count(b"{") == landed.count(b"}")
    path.write_bytes(_line({"kind": "other"}) + landed)
    other_writer(path, row[len(landed):])

    assert list(jsonl.iter_objects(path, read_code="R", label="s", must_contain=('"wanted"',))) == [
        {"a_note": "}", "kind": "wanted"},
    ]

    path.write_bytes(_line({"kind": "other"}) + landed)
    other_writer(path)
    with pytest.raises(PersistenceError):
        list(jsonl.iter_objects(path, read_code="R", label="s", must_contain=('"wanted"',)))


def test_an_approval_read_racing_an_append_returns_the_approval(tmp_path, other_writer):
    """The symptom as the operator met it: /approve read the store while `approval_cli request`
    was appending, and was refused APPROVAL_READ_FAILED as though the store were corrupt."""
    store = ApprovalStore(tmp_path / "approvals")
    store.append([{"approval_id": "apr_1", "status": "PENDING",
                   "validity": {"issued_at": "2026-09-18T00:00:00Z"}}])
    asked = {"approval_id": "apr_2", "status": "PENDING", "request": "메모리 승격 승인 요청 " * 200,
             "validity": {"issued_at": "2026-09-18T00:01:00Z"}}
    row = _line(asked)
    with open(store.path, "ab") as fh:
        fh.write(row[:4096])
    other_writer(store.path, row[4096:])

    assert store.get("apr_2") == asked
    assert [a["approval_id"] for a in store.pending()] == ["apr_1", "apr_2"]


def test_a_working_memory_read_racing_an_append_returns_every_entry(tmp_path, other_writer):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append_validated([{"validated_memory_id": "vm_1", "content": "첫 기억"}])
    row = _line({"validated_memory_id": "vm_2", "content": "두 번째 기억 " * 400})
    with open(store.root / "validated.jsonl", "ab") as fh:
        fh.write(row[:5000])
    other_writer(store.root / "validated.jsonl", row[5000:])

    assert [e["validated_memory_id"] for e in store.read_validated()] == ["vm_1", "vm_2"]
