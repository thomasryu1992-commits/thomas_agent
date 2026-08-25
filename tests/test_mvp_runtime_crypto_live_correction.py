"""A wrong live outcome row is corrected by a record, never by an edit.

`live_out_e56cf310dcc07d9c6edd` records `+398.03R` where the truth is `-0.887R`, and it passes
its own `record_sha256` because the corruption happened before the hash was taken. The ledger is
fsync append-only, a second row with the same `outcome_id` fails every live-history read closed,
and hand-editing the number would pass a recomputed hash while leaving no trace that a money
figure changed. Design: `docs/proposals/LIVE_OUTCOME_CORRECTION_RECORD_V0.2.md`.

Under test: the corrections file proves itself (self-hash AND chain, because a correction is the
only record whose *absence* changes a money figure); the five fail-closed rules; that a DERIVED
correction's figures are the target row's own arithmetic and are refused when they are not; that
nothing applies unless everything verifies; and that the write path still reads the file rather
than the corrected view.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_correction as LC
from runtime.mvp_runtime.crypto.live_pnl import (
    read_live_outcomes, read_live_outcomes_raw, state_dir,
)
from runtime.read_only_kernel import integrity
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-08-24T03:00:00Z"

# The real row, field for field.
GHOST = {
    "outcome_id": "live_out_e56cf310dcc07d9c6edd", "settlement_id": "settle_ghost",
    "symbol": "BTCUSDT", "side": "SELL", "quantity": 0.001,
    "entry_price": 77881.3, "exit_price": 77708.5, "risk_usdt": 0.1948,
    "realized_pnl_usdt": 77.5357, "result_R": 398.02720739,
    "outcome_closed": True, "closed_at_utc": "2026-08-21T09:03:43Z",
    "strategy_id": "PROBE-probe_batch_bbca200a67aae9570a36",
}
CLEAN = {
    "outcome_id": "live_out_clean", "settlement_id": "settle_clean",
    "symbol": "BTCUSDT", "side": "SELL", "quantity": 0.001,
    "entry_price": 100.0, "exit_price": 99.0, "risk_usdt": 0.001,
    "realized_pnl_usdt": -0.001, "result_R": -1.0,
    "outcome_closed": True, "closed_at_utc": "2026-08-22T00:00:00Z",
    "strategy_id": "S001",
}


def _hashed(row):
    body = dict(row)
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def _outcomes(root, *rows):
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "live_outcomes.jsonl").write_text(
        "".join(json.dumps(_hashed(r), ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _write(root, *corrections):
    (state_dir(root) / LC.CORRECTIONS_FILENAME).write_text(
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in corrections), encoding="utf-8")


def _correction(root, target_row, disposition=LC.SUPERSEDE, previous=None, approval="approval_x"):
    target = next(r for r in read_live_outcomes_raw(root)
                  if r["outcome_id"] == target_row["outcome_id"])
    return LC.build_correction(
        target=target, disposition=disposition, reason_code="OUTCOME_QUANTITY_MISMATCH",
        reason="Close-All exit filled twice the book.", approval_id=approval,
        corrected_by="thomas", previous_record_sha256=previous, now=NOW)


def _approvals(*corrections):
    return {c["approval_id"]: {"status": "APPROVED", "approved_action_snapshot": {
        "action_type": LC.CORRECTION_ACTION_TYPE, "content_sha256": LC.content_sha256(c)}}
        for c in corrections}


def _rehash(correction, **changes):
    body = {k: v for k, v in correction.items() if k != "record_sha256"}
    body.update(changes)
    body["record_sha256"] = integrity.sha256_record(body)
    return body


# --- the figures are the row's own arithmetic ------------------------------------------------

def test_the_corrected_figures_are_derived_not_supplied(tmp_path):
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST)
    assert correction["basis"] == LC.DERIVED
    assert correction["corrected_realized_pnl_usdt"] == pytest.approx(-0.1728)
    assert correction["corrected_result_R"] == pytest.approx(-0.8871, abs=1e-4)


def test_the_corrected_view_replaces_the_figures_and_names_the_record(tmp_path):
    _outcomes(tmp_path, GHOST, CLEAN)
    correction = _correction(tmp_path, GHOST)
    _write(tmp_path, correction)
    monkey = _approvals(correction)
    rows = LC.apply_corrections(read_live_outcomes_raw(tmp_path), [correction], approvals=monkey)
    ghost = next(r for r in rows if r["outcome_id"] == GHOST["outcome_id"])
    assert ghost["result_R"] == pytest.approx(-0.8871, abs=1e-4)
    assert ghost["corrected_by_correction_id"] == correction["correction_id"]
    # Untouched rows come through as they were.
    assert next(r for r in rows if r["outcome_id"] == "live_out_clean")["result_R"] == -1.0


def test_the_outcome_file_is_not_touched(tmp_path):
    _outcomes(tmp_path, GHOST)
    before = (state_dir(tmp_path) / "live_outcomes.jsonl").read_bytes()
    correction = _correction(tmp_path, GHOST)
    _write(tmp_path, correction)
    LC.apply_corrections(read_live_outcomes_raw(tmp_path), [correction], approvals=_approvals(correction))
    assert (state_dir(tmp_path) / "live_outcomes.jsonl").read_bytes() == before


def test_a_void_drops_the_row(tmp_path):
    _outcomes(tmp_path, GHOST, CLEAN)
    correction = _correction(tmp_path, GHOST, disposition=LC.VOID)
    rows = LC.apply_corrections(read_live_outcomes_raw(tmp_path), [correction],
                                approvals=_approvals(correction))
    assert [r["outcome_id"] for r in rows] == ["live_out_clean"]


# --- no corrections: nothing changes anywhere ------------------------------------------------

def test_with_no_corrections_file_the_read_is_exactly_what_it_always_was(tmp_path):
    """Every deployment until one is written. No extra file read, no approval store."""
    _outcomes(tmp_path, GHOST, CLEAN)
    assert read_live_outcomes(tmp_path) == read_live_outcomes_raw(tmp_path)


def test_an_empty_corrections_file_changes_nothing(tmp_path):
    _outcomes(tmp_path, GHOST)
    _write(tmp_path)
    assert read_live_outcomes(tmp_path) == read_live_outcomes_raw(tmp_path)


# --- the chain -------------------------------------------------------------------------------

def test_a_deleted_correction_is_caught(tmp_path):
    """The reason this file is chained and the outcome file is not: deleting a correction
    silently restores the number it corrected, and that reversion looks like normal operation."""
    _outcomes(tmp_path, GHOST, CLEAN)
    first = _correction(tmp_path, GHOST)
    second = _correction(tmp_path, CLEAN, previous=first["record_sha256"])
    _write(tmp_path, second)                      # first removed
    with pytest.raises(ToolError) as excinfo:
        LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert excinfo.value.reason_code == LC.CORRECTION_TAMPERED
    assert "removed or reordered" in str(excinfo.value)


def test_a_reordered_pair_is_caught(tmp_path):
    _outcomes(tmp_path, GHOST, CLEAN)
    first = _correction(tmp_path, GHOST)
    second = _correction(tmp_path, CLEAN, previous=first["record_sha256"])
    _write(tmp_path, second, first)
    with pytest.raises(ToolError) as excinfo:
        LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert excinfo.value.reason_code == LC.CORRECTION_TAMPERED


def test_an_edited_correction_fails_its_self_hash(tmp_path):
    _outcomes(tmp_path, GHOST)
    correction = dict(_correction(tmp_path, GHOST))
    correction["corrected_result_R"] = -99.0          # hash NOT recomputed
    _write(tmp_path, correction)
    with pytest.raises(ToolError) as excinfo:
        LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert excinfo.value.reason_code == LC.CORRECTION_TAMPERED


def test_a_well_formed_chain_reads(tmp_path):
    _outcomes(tmp_path, GHOST, CLEAN)
    first = _correction(tmp_path, GHOST)
    second = _correction(tmp_path, CLEAN, previous=first["record_sha256"])
    _write(tmp_path, first, second)
    assert [c["correction_id"] for c in
            LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)] == \
           [first["correction_id"], second["correction_id"]]


# --- the closed schema -----------------------------------------------------------------------

def test_an_unknown_key_is_refused(tmp_path):
    _outcomes(tmp_path, GHOST)
    _write(tmp_path, _rehash(_correction(tmp_path, GHOST), note="hello"))
    with pytest.raises(ToolError) as excinfo:
        LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert excinfo.value.reason_code == LC.CORRECTION_SCHEMA_INVALID


def test_attested_is_refused_because_it_is_deferred_by_design(tmp_path):
    """V0.2 §5-1 defers the path where nothing but the approval hash vouches for the figures.
    Refusing it is how the deferral is enforced rather than merely intended."""
    _outcomes(tmp_path, GHOST)
    _write(tmp_path, _rehash(_correction(tmp_path, GHOST), basis=LC.ATTESTED))
    with pytest.raises(ToolError) as excinfo:
        LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert excinfo.value.reason_code == LC.CORRECTION_SCHEMA_INVALID
    assert "deferred by design" in str(excinfo.value)


def test_a_void_carrying_figures_is_two_instructions_in_one_record(tmp_path):
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST)
    void = {k: v for k, v in correction.items() if k != "evidence"}
    _write(tmp_path, _rehash(void, disposition=LC.VOID))
    with pytest.raises(ToolError) as excinfo:
        LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert excinfo.value.reason_code == LC.CORRECTION_SCHEMA_INVALID


# --- the five rules --------------------------------------------------------------------------

def test_rule_1_a_correction_for_a_row_that_is_not_there(tmp_path):
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST)
    with pytest.raises(ToolError) as excinfo:
        LC.apply_corrections([CLEAN], [correction], approvals=_approvals(correction))
    assert excinfo.value.reason_code == LC.CORRECTION_TARGET_MISSING


def test_rule_2_the_pinned_row_hash_must_still_match(tmp_path):
    """A correction that named only the outcome id would still verify after the row changed
    underneath it. This is the field that stops it."""
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST)
    moved = dict(read_live_outcomes_raw(tmp_path)[0], record_sha256="sha256:different")
    with pytest.raises(ToolError) as excinfo:
        LC.apply_corrections([moved], [correction], approvals=_approvals(correction))
    assert excinfo.value.reason_code == LC.CORRECTION_TARGET_CHANGED


def test_rule_3_two_corrections_for_one_row(tmp_path):
    _outcomes(tmp_path, GHOST)
    first = _correction(tmp_path, GHOST, approval="approval_one")
    second = _correction(tmp_path, GHOST, disposition=LC.VOID,
                         previous=first["record_sha256"], approval="approval_two")
    with pytest.raises(ToolError) as excinfo:
        LC.apply_corrections(read_live_outcomes_raw(tmp_path), [first, second],
                             approvals=_approvals(first, second))
    assert excinfo.value.reason_code == LC.CORRECTION_AMBIGUOUS


@pytest.mark.parametrize("approvals,label", [
    (None, "unreadable store"),
    ({}, "no such approval"),
    ("wrong-status", "not APPROVED"),
    ("wrong-action", "snapshots something else"),
    ("wrong-content", "binds a different correction"),
])
def test_rule_4_the_approval_must_bind_this_correction(tmp_path, approvals, label):
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST)
    if approvals == "wrong-status":
        approvals = _approvals(correction)
        approvals[correction["approval_id"]]["status"] = "PENDING"
    elif approvals == "wrong-action":
        approvals = _approvals(correction)
        approvals[correction["approval_id"]]["approved_action_snapshot"]["action_type"] = "other"
    elif approvals == "wrong-content":
        approvals = _approvals(correction)
        approvals[correction["approval_id"]]["approved_action_snapshot"]["content_sha256"] = "sha256:x"
    with pytest.raises(ToolError) as excinfo:
        LC.apply_corrections(read_live_outcomes_raw(tmp_path), [correction], approvals=approvals)
    assert excinfo.value.reason_code == LC.CORRECTION_UNAPPROVED, label


def test_rule_4_does_not_check_the_validity_window(tmp_path):
    """A promotion approval is verified once, at the install; its 15-minute window is the right
    question there. A correction is verified on every read, forever — expiring it would mean the
    correction stops applying a quarter of an hour after it was made and the live history fails
    closed from then on, permanently, for having been correctly authorized."""
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST)
    approvals = _approvals(correction)
    approvals[correction["approval_id"]]["validity"] = {"expires_at": "2020-01-01T00:00:00Z"}
    rows = LC.apply_corrections(read_live_outcomes_raw(tmp_path), [correction], approvals=approvals)
    assert rows[0]["result_R"] == pytest.approx(-0.8871, abs=1e-4)


def test_rule_5_figures_that_are_not_the_rows_arithmetic_are_refused(tmp_path):
    _outcomes(tmp_path, GHOST)
    correction = _rehash(_correction(tmp_path, GHOST), corrected_result_R=-0.5)
    with pytest.raises(ToolError) as excinfo:
        LC.apply_corrections(read_live_outcomes_raw(tmp_path), [correction],
                             approvals=_approvals(correction))
    assert excinfo.value.reason_code == LC.CORRECTION_ARITHMETIC_DISAGREES


def test_rule_5_tolerates_what_753_tolerates(tmp_path):
    """Last-bits float difference prices; it is the same tolerance, not a second constant."""
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST)
    nudged = _rehash(correction,
                     corrected_realized_pnl_usdt=correction["corrected_realized_pnl_usdt"] + 1e-12)
    rows = LC.apply_corrections(read_live_outcomes_raw(tmp_path), [nudged],
                                approvals=_approvals(nudged))
    assert rows[0]["result_R"] == pytest.approx(-0.8871, abs=1e-4)


# --- all or nothing, and the write path ------------------------------------------------------

def test_one_bad_correction_applies_none_of_them(tmp_path):
    """A half-applied set is a history no consumer can reason about, and every consumer of this
    one is a risk decision."""
    _outcomes(tmp_path, GHOST, CLEAN)
    good = _correction(tmp_path, GHOST)
    bad = _correction(tmp_path, CLEAN, previous=good["record_sha256"], approval="approval_missing")
    with pytest.raises(ToolError):
        LC.apply_corrections(read_live_outcomes_raw(tmp_path), [good, bad], approvals=_approvals(good))
    # and the raw file is still the raw file
    assert read_live_outcomes_raw(tmp_path)[0]["result_R"] == 398.02720739


def test_the_settlement_dedupe_reads_the_file_not_the_corrected_view(tmp_path):
    """A VOID removes the row from the view. A dedupe reading the view would not see the
    settlement already on disk and would append it twice — and one duplicate fails EVERY
    verified read of this history."""
    _outcomes(tmp_path, GHOST)
    correction = _correction(tmp_path, GHOST, disposition=LC.VOID)
    _write(tmp_path, correction)
    ids = {o.get("settlement_id") for o in read_live_outcomes_raw(tmp_path)}
    assert "settle_ghost" in ids


# --- the reason code is the operator's, and it has to group ----------------------------------

@pytest.mark.parametrize("code", ["", "R", "lowercase", "Has Spaces", "1LEADING_DIGIT", None,
                                  "X" * 65])
def test_a_reason_code_that_would_not_group_is_refused(tmp_path, code):
    """Prose in this field makes it prose forever — `OUTCOME_QUANTITY_MISMATCH` and
    `Outcome quantity mismatch` do not group."""
    _outcomes(tmp_path, GHOST)
    _write(tmp_path, _rehash(_correction(tmp_path, GHOST), reason_code=code))
    with pytest.raises(ToolError) as excinfo:
        LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert excinfo.value.reason_code == LC.CORRECTION_SCHEMA_INVALID


@pytest.mark.parametrize("code", ["OUTCOME_QUANTITY_MISMATCH", "ABC", "A1_B2"])
def test_a_well_formed_reason_code_passes(tmp_path, code):
    """Shape, not a closed set — an enum would be guessing the taxonomy from one known cause."""
    _outcomes(tmp_path, GHOST)
    _write(tmp_path, _rehash(_correction(tmp_path, GHOST), reason_code=code))
    assert LC.read_corrections(
        state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)[0]["reason_code"] == code


def test_the_reason_code_is_not_in_the_approval_content_hash(tmp_path):
    """Adding a field to this hash retroactively invalidates every correction already written —
    rule 4 refuses them, and one refused correction fails the WHOLE live history closed.
    `live_corr_1710c5ca552d3e88c668`'s approval was minted without it."""
    _outcomes(tmp_path, GHOST)
    base = _correction(tmp_path, GHOST)
    assert LC.content_sha256(base) == LC.content_sha256(
        _rehash(base, reason_code="SOMETHING_ELSE_ENTIRELY"))


def test_the_module_docstring_no_longer_claims_it_cannot_write():
    """It gained `append_correction` in #772 and the docstring did not follow."""
    import inspect
    from runtime.mvp_runtime.crypto import live_correction
    doc = inspect.getdoc(live_correction) or ""
    assert "does not write" not in doc
    assert callable(live_correction.append_correction)
