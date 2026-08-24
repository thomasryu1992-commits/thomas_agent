"""Cleaning up a shadow book that predates the one-per-context rule.

#620 made the book hold one shadow per (symbol, timeframe), because the book it shadows holds
one position per (symbol, timeframe). It could not reach the shadows already open: measured
2026-08-08, 16 of 17 were ETHUSDT 1d for one strategy, opened one per 15-minute cycle over 31
hours while a block persisted.

Left alone they settle, and settling is the harm — `r_values_by_reason` counts each closed
shadow as one observation and `dashboard.sample_verdict` builds an interval over them, so
sixteen repeats shrink the interval on a sample whose true size is one. What is asserted here
is that removing them removes hypotheticals and never a result.
"""

from __future__ import annotations

import json

from runtime.mvp_runtime.crypto import counterfactual
from runtime.mvp_runtime.crypto.paper import state_dir

NOW = "2026-08-09T08:00:00Z"


def _control_events(root):
    """The control ledger as written, read straight off the file — `LedgerStore` appends here
    but exposes no reader for it, and the trace is the point of this door."""
    from runtime.mvp_runtime.store import CONTROL_FILE, LEDGER_REL

    path = root / LEDGER_REL / CONTROL_FILE
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]



def _shadow(cf_id, symbol="ETHUSDT", timeframe="1d", opened="2026-07-25T06:08:20Z", **extra):
    return {"status": "OPEN", "counterfactual_id": cf_id, "symbol": symbol,
            "timeframe": timeframe, "direction": "LONG", "entry_price": 1877.42,
            "opened_at_utc": opened, "strategy_id": "S1735",
            "block_reasons": ["daily_loss_limit_breached"], **extra}


def _write_book(root, rows):
    d = state_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / counterfactual.BOOK_FILENAME).write_text(json.dumps({
        "counterfactual_tracker_version": counterfactual.COUNTERFACTUAL_TRACKER_VERSION,
        "updated_at_utc": NOW, "open_count": len(rows), "positions": rows,
    }), encoding="utf-8")


# --- which rows are duplicates -------------------------------------------------

def test_supporting_shadows_dedupe_per_strategy_not_per_context():
    """A supporting shadow prices a benched LINEAGE, so two lineages benched in one context
    are two different answers — only a repeat of the same lineage is a duplicate. Gate rows
    (no ``shadow_kind``, including every row written before the field) keep the
    context-only key untouched."""
    rows = [
        _shadow("cf-gate"),
        _shadow("cf-sup-a1", opened="2026-07-25T06:09:00Z",
                shadow_kind=counterfactual.SHADOW_KIND_SUPPORTING, strategy_id="S_a"),
        _shadow("cf-sup-b", opened="2026-07-25T06:10:00Z",
                shadow_kind=counterfactual.SHADOW_KIND_SUPPORTING, strategy_id="S_b"),
        _shadow("cf-sup-a2", opened="2026-07-25T06:11:00Z",
                shadow_kind=counterfactual.SHADOW_KIND_SUPPORTING, strategy_id="S_a"),
    ]
    duplicates = counterfactual.context_duplicates(rows)
    assert [d["counterfactual_id"] for d in duplicates] == ["cf-sup-a2"]


def test_the_oldest_in_a_context_survives():
    """What the rule itself would have produced: the first blocked signal opens a shadow and
    every later one is refused while it is still open."""
    rows = [
        _shadow("c2", opened="2026-07-25T06:23:41Z"),
        _shadow("c1", opened="2026-07-25T06:08:20Z"),
        _shadow("c3", opened="2026-07-26T13:05:11Z"),
    ]
    dupes = {r["counterfactual_id"] for r in counterfactual.context_duplicates(rows)}
    assert dupes == {"c2", "c3"}


def test_a_context_with_one_shadow_is_untouched():
    rows = [_shadow("a", symbol="DOGEUSDT"), _shadow("b", symbol="ETHUSDT")]
    assert counterfactual.context_duplicates(rows) == []


def test_contexts_are_deduped_independently():
    rows = [
        _shadow("e1", symbol="ETHUSDT", opened="2026-07-25T00:00:00Z"),
        _shadow("e2", symbol="ETHUSDT", opened="2026-07-25T01:00:00Z"),
        _shadow("d1", symbol="DOGEUSDT", opened="2026-07-25T02:00:00Z"),
        _shadow("d2", symbol="DOGEUSDT", opened="2026-07-25T03:00:00Z"),
    ]
    dupes = {r["counterfactual_id"] for r in counterfactual.context_duplicates(rows)}
    assert dupes == {"e2", "d2"}


def test_the_same_symbol_on_two_timeframes_is_two_contexts():
    """A context is (symbol, timeframe) — the real book's own position key."""
    rows = [_shadow("a", timeframe="1d"), _shadow("b", timeframe="4h")]
    assert counterfactual.context_duplicates(rows) == []


def test_a_row_without_an_open_time_is_left_alone():
    """The ordering is the whole basis for deciding which one is real, so a row that cannot
    join it is neither chosen as the survivor nor selected for expiry."""
    rows = [_shadow("dated", opened="2026-07-25T06:08:20Z"), _shadow("undated", opened=None)]
    assert counterfactual.context_duplicates(rows) == []


def test_the_answer_does_not_depend_on_file_order():
    rows = [_shadow(f"c{i}", opened=f"2026-07-25T0{i}:00:00Z") for i in range(1, 5)]
    forward = {r["counterfactual_id"] for r in counterfactual.context_duplicates(rows)}
    backward = {r["counterfactual_id"] for r in counterfactual.context_duplicates(rows[::-1])}
    assert forward == backward == {"c2", "c3", "c4"}


# --- applying it ---------------------------------------------------------------

def test_expiring_keeps_one_and_writes_no_outcome(tmp_path):
    """Expiry, never settlement: an outcome carries a result_R and feeds per-reason
    expectancy, and nobody priced these."""
    _write_book(tmp_path, [_shadow(f"c{i}", opened=f"2026-07-25T0{i}:00:00Z")
                           for i in range(1, 5)])
    summary = counterfactual.expire_context_duplicates(root=tmp_path, now=NOW)

    assert (summary["open_before"], summary["open_after"], summary["expired_count"]) == (4, 1, 3)
    assert summary["expiry_reason"] == counterfactual.EXPIRY_REASON_CONTEXT_DUPLICATE
    survivors = counterfactual.load_open_counterfactuals(tmp_path)
    assert [r["counterfactual_id"] for r in survivors] == ["c1"]
    # The instrument's own record is untouched — nothing was priced, so nothing is recorded.
    assert counterfactual.read_counterfactual_outcomes(tmp_path) == []


def test_a_clean_book_is_not_rewritten(tmp_path):
    """Nothing to expire means nothing is written: a no-op must not restamp the book, or
    `updated_at_utc` stops meaning what it says."""
    _write_book(tmp_path, [_shadow("only")])
    before = (state_dir(tmp_path) / counterfactual.BOOK_FILENAME).read_bytes()
    summary = counterfactual.expire_context_duplicates(root=tmp_path, now=NOW)
    assert summary["expired_count"] == 0 and summary["persisted"] is False
    assert (state_dir(tmp_path) / counterfactual.BOOK_FILENAME).read_bytes() == before


def test_a_dry_run_changes_nothing(tmp_path):
    _write_book(tmp_path, [_shadow("c1", opened="2026-07-25T01:00:00Z"),
                           _shadow("c2", opened="2026-07-25T02:00:00Z")])
    summary = counterfactual.expire_context_duplicates(root=tmp_path, now=NOW, persist=False)
    assert summary["expired_count"] == 1 and summary["persisted"] is False
    assert len(counterfactual.load_open_counterfactuals(tmp_path)) == 2


def test_the_operator_door_records_what_it_expired(tmp_path):
    """The ledger entry is why this is a door rather than an edit: an expired shadow leaves
    the book, and unlike the cycle path there is no cycle record to carry the ids."""
    from runtime.mvp_runtime.store import CONTROL_FILE, LEDGER_REL
    from scripts.dedupe_counterfactual_book import DEDUPE_EVENT_TYPE, run_dedupe

    _write_book(tmp_path, [_shadow("c1", opened="2026-07-25T01:00:00Z"),
                           _shadow("c2", opened="2026-07-25T02:00:00Z")])
    summary = run_dedupe(operator="Thomas", reason="pre-#620 book", root=tmp_path, now=NOW)

    assert summary["expired_count"] == 1
    assert summary["outcome_effect"] == "NONE"
    events = _control_events(tmp_path)
    recorded = [e for e in events if e.get("record_type") == DEDUPE_EVENT_TYPE]
    assert len(recorded) == 1
    assert [r["counterfactual_id"] for r in recorded[0]["expired"]] == ["c2"]
    assert recorded[0]["operator"] == "Thomas"


def test_a_run_that_found_nothing_is_still_recorded(tmp_path):
    """Evidence too: without it an operator re-runs the cleanup because the ledger cannot say
    it already happened."""
    from runtime.mvp_runtime.store import CONTROL_FILE, LEDGER_REL
    from scripts.dedupe_counterfactual_book import DEDUPE_EVENT_TYPE, run_dedupe

    _write_book(tmp_path, [_shadow("only")])
    run_dedupe(operator="Thomas", reason="check", root=tmp_path, now=NOW)
    events = _control_events(tmp_path)
    assert [e for e in events if e.get("record_type") == DEDUPE_EVENT_TYPE]
