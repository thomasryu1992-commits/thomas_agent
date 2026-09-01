"""The kind prescreen — a parse skipped is a parse the appender's lock does not wait for.

`iter_records*` hold the record ledger's lock for the whole iteration, so a reader after a
rare kind was not only slow, it was slow *while holding up every append*. Measured on the live
host 2026-08-31: 23 MB / 5,458 active rows of which 97.6% are `crypto_cycle`, plus 177 MB /
31,590 archived rows — 1.29 s under the lock for a `--list` that wanted the weekly package
rows. These tests pin the two properties that make skipping a parse safe, because both are
the kind of thing that decays silently: the screen must never hide a wanted row, and it must
not turn a corrupt ledger into a quiet one.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import PersistenceError
from runtime.mvp_runtime.store import LedgerStore


def _seed(tmp_path, rows):
    store = LedgerStore.default(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    for kind, record in rows:
        store.append_records(f"trace_{kind}", {kind: record})
    return store


def test_a_filtered_read_returns_exactly_what_an_unfiltered_read_would(tmp_path):
    store = _seed(tmp_path, [
        ("crypto_cycle", {"n": 1}), ("task", {"n": 2}),
        ("crypto_cycle", {"n": 3}), ("budget_usage", {"n": 4}),
    ])
    wanted = {"task", "budget_usage"}
    unfiltered = [r for r in store.iter_records() if r["kind"] in wanted]
    filtered = list(store.iter_records(kinds=wanted))
    assert filtered == unfiltered
    assert [r["record"]["n"] for r in filtered] == [2, 4]


def test_a_false_positive_is_admitted_for_the_caller_to_reject(tmp_path):
    """The screen is allowed to be wrong in one direction only. A row carrying the wanted
    name as some other field's VALUE is admitted and parsed — one wasted parse, and the
    caller's own `kind` check is what rejects it. (Prose mentioning the name is not even
    that: `json.dumps` escapes the quotes, so it never matches.)"""
    store = _seed(tmp_path, [
        ("crypto_cycle", {"requested_kind": "task"}),          # value == the wanted name
        ("crypto_cycle", {"note": 'prose mentioning "task"'}),  # escaped: no match at all
        ("task", {"n": 1}),
    ])
    rows = list(store.iter_records(kinds={"task"}))
    assert [r["kind"] for r in rows] == ["crypto_cycle", "task"]
    assert [r for r in rows if r["kind"] == "task"][0]["record"]["n"] == 1


def test_a_torn_final_line_still_raises_under_a_filter(tmp_path):
    """The store's real corruption mode is an interrupted append. A filtered read must not
    swallow it: the shape test (`{` … `}`) sends it to the parser, which fails closed."""
    store = _seed(tmp_path, [("task", {"n": 1})])
    with (store.root / "records.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "crypto_cycle", "record": {"n": 2}\n')  # truncated: no closing brace
    with pytest.raises(PersistenceError) as caught:
        list(store.iter_records(kinds={"task"}))
    assert caught.value.reason_code == "LEDGER_UNREADABLE"


def test_an_empty_kind_selection_refuses_rather_than_matching_nothing(tmp_path):
    """`kinds=()` reads as 'no filter' to a careless caller and as 'match nothing' to the
    screen — the gap between those two is a silently empty ledger, so it is refused."""
    store = _seed(tmp_path, [("task", {"n": 1})])
    with pytest.raises(PersistenceError) as caught:
        list(store.iter_records(kinds=()))
    assert caught.value.reason_code == "LEDGER_EMPTY_KIND_FILTER"


def test_the_archive_walk_screens_too_and_still_spans_files(tmp_path):
    store = _seed(tmp_path, [("crypto_cycle", {"n": 1}), ("task", {"n": 2})])
    archive = store.root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "records.2026-08-01T000000Z.jsonl").write_text(
        json.dumps({"kind": "crypto_cycle", "trace_id": "t", "record": {"n": 3}}) + "\n"
        + json.dumps({"kind": "task", "trace_id": "t", "record": {"n": 4}}) + "\n",
        encoding="utf-8")
    rows = list(store.iter_records_with_archive(kinds={"task"}))
    assert [r["record"]["n"] for r in rows] == [4, 2]  # archive first, then active


# --- the archive index: the only skip that does not scale with the store ------------


import os

from runtime.mvp_runtime import retention
from runtime.mvp_runtime.store import archive_index_path, read_archive_index


def _archive(store, name, rows):
    directory = store.root / "archive"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("".join(
        json.dumps({"kind": k, "trace_id": "t", "record": r}) + "\n" for k, r in rows),
        encoding="utf-8")
    return path


def test_rotation_indexes_the_archive_it_closes(tmp_path):
    # The task row sits in the middle so rotation carries it INTO the archive; the last row
    # is what stays behind, and an index built from the wrong half would pass a laxer test.
    store = _seed(tmp_path, [("crypto_cycle", {"n": 1}), ("task", {"n": 2}),
                             ("crypto_cycle", {"n": 3})])
    result = retention.rotate_file(store, "records.jsonl", keep_rows=1, now="2026-09-01T00:00:00Z")
    archive = store.root / result["archive"]
    assert result["rotated"] == 2
    assert read_archive_index(archive) == frozenset({"crypto_cycle", "task"})
    # ...and the reader trusts it: asking for `task` still reaches the archived row.
    assert [r["record"]["n"] for r in store.iter_records_with_archive(kinds={"task"})] == [2]


def test_an_indexed_archive_that_cannot_match_is_never_opened(tmp_path):
    """The index earns its keep by making the file unreachable, not merely quick — so the
    proof is that a *deleted* archive still answers, which only holds if it was skipped."""
    store = _seed(tmp_path, [("task", {"n": 1})])
    archive = _archive(store, "records.2026-08-01T000000Z.jsonl", [("crypto_cycle", {"n": 2})])
    retention.write_archive_index(archive)
    archive.unlink()  # the index says it holds no `task`; a reader that opens it would raise
    assert [r["record"]["n"] for r in store.iter_records_with_archive(kinds={"task"})] == [1]


def test_an_unindexed_archive_is_always_opened(tmp_path):
    """Fail-closed: no index is 'unknown', never 'nothing there'."""
    store = _seed(tmp_path, [("task", {"n": 1})])
    _archive(store, "records.2026-08-01T000000Z.jsonl", [("task", {"n": 2})])
    assert [r["record"]["n"] for r in store.iter_records_with_archive(kinds={"task"})] == [2, 1]


def test_a_truncated_index_is_treated_as_absent(tmp_path):
    """An index that names nothing reads the same as a torn write, so it must not be trusted
    to mean 'this archive is empty of every kind' — that would skip a file on damaged
    evidence, which is the one failure this whole mechanism must not have."""
    store = _seed(tmp_path, [("task", {"n": 1})])
    archive = _archive(store, "records.2026-08-01T000000Z.jsonl", [("task", {"n": 2})])
    archive_index_path(archive).write_text("", encoding="utf-8")
    assert read_archive_index(archive) is None
    assert [r["record"]["n"] for r in store.iter_records_with_archive(kinds={"task"})] == [2, 1]


def test_the_index_may_over_report_but_never_under_report(tmp_path):
    """Built by substring without parsing, so a payload quoting a kind name adds an entry.
    That costs one needless read; the opposite would lose rows."""
    store = _seed(tmp_path, [])
    archive = _archive(store, "records.2026-08-01T000000Z.jsonl",
                       [("crypto_cycle", {"requested_kind": "task"})])
    kinds = retention.write_archive_index(archive)
    assert {"crypto_cycle", "task"} <= kinds          # over-reports `task`
    rows = list(store.iter_records_with_archive(kinds={"task"}))
    assert [r["kind"] for r in rows] == ["crypto_cycle"]   # opened, then rejected by the caller


def test_an_unfiltered_read_ignores_the_index_entirely(tmp_path):
    store = _seed(tmp_path, [("task", {"n": 1})])
    archive = _archive(store, "records.2026-08-01T000000Z.jsonl", [("crypto_cycle", {"n": 2})])
    retention.write_archive_index(archive)
    assert [r["record"]["n"] for r in store.iter_records_with_archive()] == [2, 1]
