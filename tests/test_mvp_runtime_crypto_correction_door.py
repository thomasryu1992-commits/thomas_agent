"""The door that writes a correction — governed, and with no escape hatch.

`promote_strategy_candidates` has `--without-approval` because a pool change can be the
recovery action on a runtime that is already stuck. A correction never is: the uncorrected
figure is what the runtime has been reading all along, so an escape here would only ever be a
way to change a money figure without being asked to justify it.

Under test: the read-only screen states the arithmetic and names the one row that disagrees
with its own prices; the confirm path refuses anything the read path would later refuse, so a
correction that fails verification is never written (a written-but-refused correction fails the
WHOLE live history closed); the append re-chains onto the file's current tip; and the ask binds
the same content the confirm produces.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_correction as LC
from runtime.mvp_runtime.crypto.live_pnl import read_live_outcomes, read_live_outcomes_raw, state_dir
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError
from runtime.read_only_kernel import integrity
from scripts import correct_live_outcome as DOOR

NOW = "2026-08-24T03:00:00Z"

GHOST = {
    "outcome_id": "live_out_ghost", "settlement_id": "settle_ghost", "symbol": "BTCUSDT",
    "side": "SELL", "quantity": 0.001, "entry_price": 77881.3, "exit_price": 77708.5,
    "risk_usdt": 0.1948, "realized_pnl_usdt": 77.5357, "result_R": 398.02720739,
    "outcome_closed": True, "closed_at_utc": "2026-08-21T09:03:43Z",
}
CLEAN = {
    "outcome_id": "live_out_clean", "settlement_id": "settle_clean", "symbol": "BTCUSDT",
    "side": "SELL", "quantity": 0.001, "entry_price": 100.0, "exit_price": 99.0,
    "risk_usdt": 0.001, "realized_pnl_usdt": -0.001, "result_R": -1.0,
    "outcome_closed": True, "closed_at_utc": "2026-08-22T00:00:00Z",
}
NO_RISK = dict(CLEAN, outcome_id="live_out_norisk", settlement_id="settle_norisk",
               risk_usdt=None, result_R=None)


def _outcomes(root, *rows):
    directory = state_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    lines = []
    for row in rows:
        body = dict(row)
        body["record_sha256"] = integrity.sha256_record(body)
        lines.append(json.dumps(body, ensure_ascii=False))
    (directory / "live_outcomes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


class _Store:
    """Just enough ApprovalStore for the door: `get(approval_id)`."""

    def __init__(self, records=None):
        self._records = records or {}

    def get(self, approval_id):
        return self._records.get(approval_id)


def _approval(record):
    return {"status": "APPROVED", "approved_action_snapshot": {
        "action_type": LC.CORRECTION_ACTION_TYPE, "content_sha256": LC.content_sha256(record)}}


def _confirm(monkeypatch, root, store, **kwargs):
    monkeypatch.setattr(DOOR, "ApprovalStore", lambda *a, **k: store)
    monkeypatch.setattr(DOOR.ApprovalStore, "default", staticmethod(lambda *a, **k: store), raising=False)
    return DOOR.run_confirm(root=root, now=NOW, **kwargs)


# --- the read-only screen --------------------------------------------------------------------

def test_the_screen_names_the_row_that_disagrees_with_its_own_prices(tmp_path):
    _outcomes(tmp_path, GHOST, CLEAN, NO_RISK)
    rows = DOOR.run_list(root=tmp_path)
    assert [r["outcome_id"] for r in rows][0] == "live_out_ghost"     # disagreements first
    ghost = rows[0]
    assert ghost["agrees"] is False
    assert ghost["derived_realized_pnl_usdt"] == pytest.approx(-0.1728)
    assert ghost["derived_result_R"] == pytest.approx(-0.8871, abs=1e-4)


def test_a_row_without_the_terms_is_undecidable_not_wrong(tmp_path):
    """`None` is a third answer. "cannot check" must not read as "checked and wrong"."""
    _outcomes(tmp_path, NO_RISK)
    assert DOOR.run_list(root=tmp_path)[0]["agrees"] is None


def test_the_screen_marks_a_row_that_already_carries_a_correction(tmp_path, monkeypatch):
    _outcomes(tmp_path, GHOST, CLEAN)
    record = LC.build_correction(
        target=read_live_outcomes_raw(tmp_path)[0], disposition=LC.SUPERSEDE,
        reason_code="OUTCOME_QUANTITY_MISMATCH", reason="x", approval_id="approval_a",
        corrected_by="thomas", previous_record_sha256=None, now=NOW)
    _confirm(monkeypatch, tmp_path, _Store({"approval_a": _approval(record)}),
             outcome_id="live_out_ghost", disposition=LC.SUPERSEDE, reason="x",
             approval_id="approval_a", corrected_by="thomas")
    ghost = next(r for r in DOOR.run_list(root=tmp_path) if r["outcome_id"] == "live_out_ghost")
    assert ghost["already_corrected"] is True


# --- confirm ---------------------------------------------------------------------------------

def test_confirm_writes_a_correction_the_read_path_then_applies(tmp_path, monkeypatch):
    _outcomes(tmp_path, GHOST, CLEAN)
    record = LC.build_correction(
        target=read_live_outcomes_raw(tmp_path)[0], disposition=LC.SUPERSEDE,
        reason_code="OUTCOME_QUANTITY_MISMATCH", reason="x", approval_id="approval_a",
        corrected_by="thomas", previous_record_sha256=None, now=NOW)
    store = _Store({"approval_a": _approval(record)})
    _confirm(monkeypatch, tmp_path, store, outcome_id="live_out_ghost",
             disposition=LC.SUPERSEDE, reason="x", approval_id="approval_a",
             corrected_by="thomas")
    monkeypatch.setattr("runtime.mvp_runtime.crypto.live_pnl._approvals_for",
                        lambda corrections, root: {"approval_a": _approval(record)})
    ghost = next(r for r in read_live_outcomes(tmp_path) if r["outcome_id"] == "live_out_ghost")
    assert ghost["result_R"] == pytest.approx(-0.8871, abs=1e-4)
    # the file itself is untouched
    assert read_live_outcomes_raw(tmp_path)[0]["result_R"] == 398.02720739


def test_confirm_refuses_what_the_read_path_would_refuse(tmp_path, monkeypatch):
    """A written-but-refused correction fails the WHOLE live history closed. Far better to
    discover the mismatch at the door than on the next breaker read."""
    _outcomes(tmp_path, GHOST)
    with pytest.raises(ToolError) as excinfo:
        _confirm(monkeypatch, tmp_path, _Store({}), outcome_id="live_out_ghost",
                 disposition=LC.SUPERSEDE, reason="x", approval_id="approval_missing",
                 corrected_by="thomas")
    assert excinfo.value.reason_code == LC.CORRECTION_UNAPPROVED
    assert not (state_dir(tmp_path) / LC.CORRECTIONS_FILENAME).exists()


def test_confirm_on_an_unknown_row_is_blocked(tmp_path, monkeypatch):
    _outcomes(tmp_path, CLEAN)
    with pytest.raises(MvpRuntimeError) as excinfo:
        _confirm(monkeypatch, tmp_path, _Store({}), outcome_id="live_out_nope",
                 disposition=LC.SUPERSEDE, reason="x", approval_id="a", corrected_by="thomas")
    assert excinfo.value.reason_code == LC.CORRECTION_TARGET_MISSING


def test_there_is_no_without_approval_escape():
    """Deliberate asymmetry with the promotion door. Pinned so it is removed on purpose or
    not at all."""
    import inspect
    parser_source = inspect.getsource(DOOR.main)
    assert "--without-approval" not in parser_source
    assert "without_approval" not in inspect.signature(DOOR.run_confirm).parameters


# --- the chain, at the append ----------------------------------------------------------------

def test_the_append_rechains_onto_the_current_tip(tmp_path, monkeypatch):
    """The caller read the tip when it started building; another process may have appended
    since, and two records pointing at the same stale tip is a fork the reader cannot tell
    from a deletion."""
    _outcomes(tmp_path, GHOST, CLEAN)
    rows = read_live_outcomes_raw(tmp_path)
    first = LC.build_correction(target=rows[0], disposition=LC.SUPERSEDE,
                                reason_code="R", reason="x", approval_id="a",
                                corrected_by="thomas", previous_record_sha256=None, now=NOW)
    LC.append_correction(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME, first)
    # built against a stale tip (None) on purpose
    second = LC.build_correction(target=rows[1], disposition=LC.SUPERSEDE,
                                 reason_code="R", reason="x", approval_id="b",
                                 corrected_by="thomas", previous_record_sha256=None, now=NOW)
    LC.append_correction(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME, second)
    stored = LC.read_corrections(state_dir(tmp_path) / LC.CORRECTIONS_FILENAME)
    assert len(stored) == 2
    assert stored[1]["previous_record_sha256"] == stored[0]["record_sha256"]


def test_the_append_refuses_onto_an_already_broken_chain(tmp_path):
    """Otherwise the append buries the break under a valid-looking record."""
    _outcomes(tmp_path, GHOST, CLEAN)
    rows = read_live_outcomes_raw(tmp_path)
    path = state_dir(tmp_path) / LC.CORRECTIONS_FILENAME
    broken = LC.build_correction(target=rows[0], disposition=LC.SUPERSEDE, reason_code="R",
                                 reason="x", approval_id="a", corrected_by="thomas",
                                 previous_record_sha256="sha256:not-the-genesis", now=NOW)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(broken, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        LC.append_correction(path, LC.build_correction(
            target=rows[1], disposition=LC.SUPERSEDE, reason_code="R", reason="x",
            approval_id="b", corrected_by="thomas", previous_record_sha256=None, now=NOW))
    assert excinfo.value.reason_code == LC.CORRECTION_TAMPERED


# --- ask and confirm agree -------------------------------------------------------------------

def test_the_ask_binds_the_content_the_confirm_produces(tmp_path):
    """If these two ever disagreed, every approval would verify at the ask and refuse at the
    confirm — the door would be unusable and the reason would be invisible."""
    _outcomes(tmp_path, GHOST)
    target = read_live_outcomes_raw(tmp_path)[0]
    asked = LC.build_correction(target=target, disposition=LC.SUPERSEDE, reason_code="OUTCOME_QUANTITY_MISMATCH",
                                reason="x", approval_id="pending", corrected_by="pending",
                                previous_record_sha256=None, now=NOW)
    confirmed = LC.build_correction(target=target, disposition=LC.SUPERSEDE, reason_code="OUTCOME_QUANTITY_MISMATCH",
                                    reason="x", approval_id="approval_a", corrected_by="thomas",
                                    previous_record_sha256="sha256:whatever", now=NOW)
    assert LC.content_sha256(asked) == LC.content_sha256(confirmed)


def test_the_content_hash_moves_when_the_target_row_does(tmp_path):
    """Rule 2, asserted one door earlier: an approval read against one row cannot execute
    against another."""
    _outcomes(tmp_path, GHOST)
    target = read_live_outcomes_raw(tmp_path)[0]
    first = LC.build_correction(target=target, disposition=LC.SUPERSEDE, reason_code="R",
                                reason="x", approval_id="a", corrected_by="t",
                                previous_record_sha256=None, now=NOW)
    moved = LC.build_correction(target=dict(target, record_sha256="sha256:moved"),
                                disposition=LC.SUPERSEDE, reason_code="R", reason="x",
                                approval_id="a", corrected_by="t",
                                previous_record_sha256=None, now=NOW)
    assert LC.content_sha256(first) != LC.content_sha256(moved)


# --- the screen must survive the data it exists to describe -----------------------------------

def test_the_listing_prints_a_row_that_has_no_figures(capsys, monkeypatch):
    """Measured in the deployed runtime: `result_R: None` on the two rows with no recorded risk
    raised TypeError and took the whole listing down at the seventh row. The rows this screen is
    FOR are the rows something is wrong with."""
    monkeypatch.setattr(DOOR, "run_list", lambda **_: [
        {"outcome_id": "a", "symbol": "BTCUSDT", "recorded_realized_pnl_usdt": None,
         "recorded_result_R": None, "already_corrected": False, "derivable": False,
         "agrees": None},
        {"outcome_id": "b", "symbol": "BTCUSDT", "recorded_realized_pnl_usdt": 77.5357,
         "recorded_result_R": 398.0272, "already_corrected": False, "derivable": True,
         "agrees": False, "derived_realized_pnl_usdt": -0.1728, "derived_result_R": -0.8871},
    ])
    monkeypatch.setattr(DOOR, "assert_not_foreign_root_run", lambda *a, **k: None)
    assert DOOR.main(["--list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("?? a BTCUSDT recorded ? USDT / ?R")
    assert "+398.0272R" in out[1] and "-0.8871R" in out[1]
