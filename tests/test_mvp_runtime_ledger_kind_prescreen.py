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
