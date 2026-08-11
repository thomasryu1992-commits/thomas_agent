"""Stop-slippage probe tests — the plan binds the approved batch; a probe row is lineage-free.

Under test: the content hash pins every approved parameter and the plan store fails
closed on tamper; the batch budget refuses above its cap; cell selection enforces the
regime grid, one-probe-at-a-time, and exhaustion; a probe outcome can never reach
forward confirmation; the ask/confirm approval round-trip mirrors the promotion door's;
and every ``--fire`` refusal path refuses with its typed code before any venue call —
driven end to end with fakes, zero network anywhere.

Since the second-batch approval (Thomas 2026-08-11) the symbol set is a request-time
parameter, and this file also pins its compatibility surface: the DEFAULT batch's
identity is frozen byte for byte (the live machine's batch-1 approval binds it), the
batch-1 plan's exact on-disk shape keeps loading, N/cells/budget derive from the
requested set, the unchanged 10 USDT cap refuses a five-symbol batch, and symbol
validation fails closed on duplicates and malformed names.
"""

from __future__ import annotations

import json
import types

import pytest

import scripts.run_slippage_probe as cli
from runtime.mvp_runtime import timeutil
from runtime.mvp_runtime.crypto import forward_confirmation, lifecycle, probe
from runtime.mvp_runtime.crypto.live_execution import DryRunOrderAdapter
from runtime.mvp_runtime.crypto.live_order import (
    CANARY_CONFIRMATION_PHRASE,
    LIVE_CONFIRMATION_PHRASE,
    LiveOrderLimits,
)
from runtime.mvp_runtime.crypto.live_pnl import build_live_outcome_record
from runtime.mvp_runtime.crypto.live_sizing import SymbolFilters
from runtime.mvp_runtime.errors import ApprovalBlocked, MvpRuntimeError, ToolError
from tests._helpers import requires_local_core

NOW = timeutil.utc_now_iso()

# Frozen on origin/main @ 2bfd610 — the last commit BEFORE symbols became a request-time
# parameter. The live machine's batch-1 approval and ACTIVE plan bind exactly this
# identity, so the DEFAULT batch must keep reproducing it byte for byte: a drift here
# silently invalidates a standing approval.
BATCH_ONE_CONTENT_SHA256 = "sha256:11a6109dc75131cd4998cbd251e4b590f4c5a0bada3d45946618ff69f61c1a2f"
BATCH_ONE_BATCH_ID = "probe_batch_900fc0ad8ea51614381a"


def _params(**overrides):
    kwargs = dict(
        n=12, symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"), stop_bps=40.0, repeats=2,
        timeout_minutes=120, per_probe_notional_cap_usdt=100.0, budget_cap_usdt=10.0,
    )
    kwargs.update(overrides)
    return probe.build_batch_params(**kwargs)


def _fake_approval(params, *, status="APPROVED", content=None,
                   action=probe.PROBE_ACTION_TYPE, expires="2999-01-01T00:00:00Z"):
    return {
        "approval_id": "approval_probe_test",
        "status": status,
        "validity": {"issued_at": NOW, "expires_at": expires},
        "approved_action_snapshot": {
            "action_type": action,
            "content_sha256": content if content is not None else probe.probe_content_sha256(params),
        },
    }


def _active_plan(tmp_path, params=None):
    params = params or _params()
    return probe.write_plan(
        probe.build_plan(params, approval_id="approval_probe_test", now=NOW), tmp_path
    )


# --- content hash + budget ------------------------------------------------------------

def test_content_hash_is_stable_and_changes_on_any_material_change():
    base = probe.probe_content_sha256(_params())
    assert probe.probe_content_sha256(_params()) == base
    assert probe.probe_content_sha256(_params(stop_bps=50.0)) != base
    assert probe.probe_content_sha256(_params(timeout_minutes=90)) != base
    assert probe.probe_content_sha256(
        _params(n=8, symbols=("BTCUSDT", "ETHUSDT"))) != base
    # Symbol order is not material — the params sort them before hashing.
    assert probe.probe_content_sha256(
        _params(symbols=("SOLUSDT", "BTCUSDT", "ETHUSDT"))) == base


def test_batch_budget_refuses_above_the_cap():
    # 12 x 100 x 0.0075 = 9.0 USDT clears the 10 USDT cap; a wider per-probe ceiling
    # prices the worst case at 10.8 and refuses.
    _params()
    with pytest.raises(ToolError) as exc:
        _params(per_probe_notional_cap_usdt=120.0)
    assert exc.value.reason_code == probe.PROBE_BUDGET_EXCEEDED


def test_params_refuse_a_grid_mismatch():
    with pytest.raises(ToolError) as exc:
        probe.build_batch_params(n=10)
    assert exc.value.reason_code == probe.PROBE_PARAMS_INVALID


def test_the_default_batch_identity_is_frozen_for_the_live_approval():
    """--symbols omitted must stay byte-identical to the pre-change default: the batch-1
    approval and plan on the live machine bind this hash, and the new code must not
    quietly re-mint them."""
    params = probe.build_batch_params()
    assert probe.probe_content_sha256(params) == BATCH_ONE_CONTENT_SHA256
    assert probe.batch_id_of(params) == BATCH_ONE_BATCH_ID
    assert params["n"] == 12 and params["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_a_two_symbol_batch_derives_n_cells_and_budget_from_its_symbols():
    # Batch 2's shape (Thomas 2026-08-11, "2차 배치도 진행해줘"): n derives from the
    # grid, input is normalized then sorted, and the worst case reprices to 8 x 100 x
    # 0.0075 = 6.0 USDT — comfortably under the unchanged 10 USDT cap.
    params = probe.build_batch_params(symbols=("dogeusdt", "BNBUSDT"))
    assert params["n"] == 8
    assert params["symbols"] == ["BNBUSDT", "DOGEUSDT"]
    assert probe.worst_case_loss_usdt(params) == pytest.approx(6.0)
    cells = probe.build_cells(params)
    assert len(cells) == 8
    assert {(c["symbol"], c["regime"], c["repeat"]) for c in cells} == {
        (s, r, k) for s in ("BNBUSDT", "DOGEUSDT") for r in probe.REGIMES for k in (1, 2)
    }
    # A different set is a different batch — and therefore a different approval.
    assert probe.probe_content_sha256(params) != BATCH_ONE_CONTENT_SHA256


def test_a_five_symbol_batch_refuses_on_the_unchanged_budget_cap():
    # 5 x 2 x 2 = 20 probes x 100 USDT x 0.0075 = 15.0 USDT > the approved 10.0 cap.
    # Deliberately so: the cap does not stretch with the universe — a bigger batch
    # needs a newly approved cap, never a silent widening.
    with pytest.raises(ToolError) as exc:
        probe.build_batch_params(
            symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT"))
    assert exc.value.reason_code == probe.PROBE_BUDGET_EXCEEDED


@pytest.mark.parametrize("symbols", [
    (),                        # no symbols at all
    ("",),                     # an empty entry refuses, never silently drops
    ("BNBUSDT", "  "),         # whitespace is an empty entry too
    ("BTCUSDT", "btcusdt"),    # duplicate after normalization would re-weight the grid
    ("BNBBUSD",),              # not USDT-quoted (the caps are priced in USDT)
    ("USDT",),                 # the bare quote asset is not a market
    ("BNB-USDT",),             # not alphanumeric
])
def test_symbol_validation_fails_closed(symbols):
    with pytest.raises(ToolError) as exc:
        probe.build_batch_params(symbols=symbols)
    assert exc.value.reason_code == probe.PROBE_PARAMS_INVALID


# --- the plan store fails closed ------------------------------------------------------

def test_plan_round_trips_and_tamper_fails_closed(tmp_path):
    plan = _active_plan(tmp_path)
    assert probe.read_plan(tmp_path) == plan

    raw = json.loads(probe.plan_path(tmp_path).read_text(encoding="utf-8"))
    raw["params"]["stop_bps"] = 80.0  # widen the stop without re-hashing
    probe.plan_path(tmp_path).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        probe.read_plan(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_TAMPERED


def test_plan_refuses_unknown_fields_even_with_a_fresh_hash(tmp_path):
    plan = _active_plan(tmp_path)
    raw = {k: v for k, v in plan.items() if k != "record_sha256"}
    raw["extra_field"] = True
    raw["record_sha256"] = probe._plan_sha256(raw)
    probe.plan_path(tmp_path).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        probe.read_plan(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_INVALID


def test_plan_batch_id_must_derive_from_params(tmp_path):
    plan = _active_plan(tmp_path)
    raw = {k: v for k, v in plan.items() if k != "record_sha256"}
    raw["batch_id"] = "probe_batch_forged"
    raw["record_sha256"] = probe._plan_sha256(raw)
    probe.plan_path(tmp_path).write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        probe.read_plan(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_INVALID


def _batch_one_disk_plan():
    """The plan EXACTLY as the pre-change code wrote it to disk (the live machine's
    ACTIVE batch-1 shape): every value a literal — no builder, no module constant — and
    the record hash frozen from origin/main @ 2bfd610, so the fixture cannot drift with
    the code it exists to hold still."""
    cells = [
        {"symbol": symbol, "regime": regime, "repeat": repeat, "status": "EMPTY",
         "opened_at": None, "updated_at": None, "position_id": None,
         "entry_client_order_id": None, "outcome_id": None, "close_reason": None,
         "stop_slippage_bps": None, "note": None}
        for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT")
        for regime in ("low_vol", "high_vol")
        for repeat in (1, 2)
    ]
    return {
        "plan_version": "stop_slippage_probe_plan.v1",
        "batch_id": BATCH_ONE_BATCH_ID,
        "params": {
            "n": 12, "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "direction": "LONG",
            "stop_bps": 40.0, "regime_feature": "atr_percentile", "regime_split": 0.5,
            "regime_timeframe": "4h", "repeats": 2, "timeout_minutes": 120,
            "worst_case_loss_fraction": 0.0075, "per_probe_notional_cap_usdt": 100.0,
            "budget_cap_usdt": 10.0,
        },
        "cells": cells,
        "approval_id": "approval_batch_one",
        "status": "ACTIVE",
        "created_at": "2026-08-11T00:00:00Z",
        "updated_at": "2026-08-11T00:00:00Z",
        "record_sha256": "sha256:01a2e3e2109694e24730feb958775c36000c6153cc1b92edb2735735aa67b035",
    }


def test_a_plan_file_written_by_the_batch_one_code_loads_unchanged(tmp_path):
    """Plan compatibility: the ACTIVE plan on the live machine predates symbols being a
    request-time parameter, and its exact on-disk shape — frozen self-hash included —
    must keep loading, selecting, and marking under the new code."""
    raw = _batch_one_disk_plan()
    path = probe.plan_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")

    plan = probe.read_plan(tmp_path)
    assert plan == raw
    assert plan["status"] == probe.PLAN_ACTIVE and len(plan["cells"]) == 12
    # The new code keeps WORKING the old plan, not merely parsing it.
    index = probe.select_cell(plan, symbol="ETHUSDT", regime=probe.REGIME_LOW)
    assert plan["cells"][index]["symbol"] == "ETHUSDT"
    updated = probe.mark_cell(plan, index, status=probe.CELL_OPEN, now=NOW, position_id="pos_x")
    probe.write_plan(updated, tmp_path)
    assert probe.read_plan(tmp_path)["cells"][index]["status"] == probe.CELL_OPEN


# --- cell selection -------------------------------------------------------------------

def test_select_cell_matches_symbol_and_regime():
    plan = probe.build_plan(_params(), approval_id="a", now=NOW)
    index = probe.select_cell(plan, symbol="ETHUSDT", regime=probe.REGIME_HIGH)
    cell = plan["cells"][index]
    assert (cell["symbol"], cell["regime"], cell["status"]) == (
        "ETHUSDT", probe.REGIME_HIGH, probe.CELL_EMPTY)


def test_select_cell_refuses_while_one_probe_is_open():
    plan = probe.build_plan(_params(), approval_id="a", now=NOW)
    plan = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_1")
    with pytest.raises(ToolError) as exc:
        probe.select_cell(plan, symbol="ETHUSDT", regime=probe.REGIME_HIGH)
    assert exc.value.reason_code == probe.PROBE_CELL_OPEN


def test_select_cell_regime_exhaustion_and_batch_exhaustion():
    plan = probe.build_plan(_params(), approval_id="a", now=NOW)
    # Consume both BTCUSDT low_vol repeats; the low regime is then exhausted for BTC
    # while the batch still has EMPTY cells elsewhere.
    for index, cell in enumerate(plan["cells"]):
        if cell["symbol"] == "BTCUSDT" and cell["regime"] == probe.REGIME_LOW:
            plan = probe.mark_cell(plan, index, status=probe.CELL_OPEN, now=NOW)
            plan = probe.mark_cell(plan, index, status=probe.CELL_TIMEOUT, now=NOW,
                                   outcome_id=f"out_{index}", close_reason="time_exit")
    with pytest.raises(ToolError) as exc:
        probe.select_cell(plan, symbol="BTCUSDT", regime=probe.REGIME_LOW)
    assert exc.value.reason_code == probe.PROBE_REGIME_EXHAUSTED

    for index, cell in enumerate(plan["cells"]):
        if cell["status"] == probe.CELL_EMPTY:
            plan = probe.mark_cell(plan, index, status=probe.CELL_OPEN, now=NOW)
            plan = probe.mark_cell(plan, index, status=probe.CELL_FILLED, now=NOW,
                                   outcome_id=f"out_{index}", close_reason="stop_loss",
                                   stop_slippage_bps=1.0)
    assert plan["status"] == probe.PLAN_COMPLETE
    with pytest.raises(ToolError) as exc:
        probe.select_cell(dict(plan, status=probe.PLAN_ACTIVE),
                          symbol="BTCUSDT", regime=probe.REGIME_HIGH)
    assert exc.value.reason_code == probe.PROBE_BATCH_EXHAUSTED


def test_regime_of_splits_at_the_boundary_and_refuses_unreadable():
    assert probe.regime_of(0.5) == probe.REGIME_HIGH
    assert probe.regime_of(0.49) == probe.REGIME_LOW
    with pytest.raises(ToolError) as exc:
        probe.regime_of(None)
    assert exc.value.reason_code == probe.PROBE_REGIME_UNREADABLE


def test_resolve_open_cell_reads_the_ledger_not_the_hope():
    plan = probe.build_plan(_params(), approval_id="a", now=NOW)
    plan = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_1")

    # Still on the book: honestly OPEN, nothing resolves.
    unchanged, resolution = probe.resolve_open_cell(
        plan, outcomes=[], position_open=True, now=NOW)
    assert resolution is None and unchanged["cells"][0]["status"] == probe.CELL_OPEN

    # Book clear + a stop_loss outcome with a measured figure: the sample was bought.
    stop_row = {"position_id": "pos_1", "outcome_id": "out_1",
                "close_reason": "stop_loss", "stop_slippage_bps": 3.2}
    resolved, resolution = probe.resolve_open_cell(
        plan, outcomes=[stop_row], position_open=False, now=NOW)
    assert resolution["status"] == probe.CELL_FILLED
    assert resolved["cells"][0]["stop_slippage_bps"] == 3.2

    # Book clear + an external close: no sample; the slot returns to EMPTY.
    external = {"position_id": "pos_1", "outcome_id": "out_2",
                "close_reason": "venue_external_close", "stop_slippage_bps": None}
    resolved, resolution = probe.resolve_open_cell(
        plan, outcomes=[external], position_open=False, now=NOW)
    assert resolution["status"] == probe.CELL_EMPTY
    assert resolved["cells"][0]["position_id"] is None


# --- lineage-free by construction -----------------------------------------------------

def _probe_outcome_row():
    """A probe outcome exactly as ``--fire`` produces it: the #683 builder, the PROBE
    strategy id, and NO candidate / generation / rule-hash fields."""
    return build_live_outcome_record(
        realized_pnl_usdt=-0.42, symbol="BTCUSDT", side="SELL", quantity=0.001,
        entry_price=100000.0, exit_price=99560.0, strategy_id="PROBE-probe_batch_abc",
        position_id="pos_probe", close_reason="stop_loss", opened_at_utc=NOW,
        risk_usdt=0.4, stop_price=99600.0, now=NOW,
    )


def test_probe_rows_cannot_reach_forward_confirmation():
    rows = [_probe_outcome_row() for _ in range(30)]
    candidate = {
        "candidate_id": "cand_x", "strategy_id": "S1", "generation_id": "GEN-001",
        "strategy_rule_hash": "hash_x", "strategy_spec": {"timeframe": "4h"},
    }
    verdict = forward_confirmation.judge_forward(candidate, rows)
    assert verdict["closed_count"] == 0
    assert verdict["status"] == forward_confirmation.FORWARD_INSUFFICIENT
    # And the reason, pinned at the key level: a probe row has no lineage key at all,
    # and attribution lands on the sid: tier no pool entry answers to.
    row = rows[0]
    assert forward_confirmation.lineage_keys(row) == frozenset()
    assert lifecycle.outcome_attribution_key(row) == "sid:PROBE-probe_batch_abc"
    # It still measures: the slippage reader takes the row like any other stop close.
    assert probe.probe_slippage_sample(rows)[0]["stop_slippage_bps"] == pytest.approx(4.0161, abs=1e-3)


def test_decision_readiness_opens_at_ten():
    rows = [_probe_outcome_row() for _ in range(9)]
    sample = probe.probe_slippage_sample(rows)
    assert probe.decision_readiness(sample)["ready"] is False
    rows.append(_probe_outcome_row())
    sample = probe.probe_slippage_sample(rows)
    readiness = probe.decision_readiness(sample)
    assert readiness["ready"] is True and readiness["sample_n"] == 10
    assert readiness["median_bps"] == pytest.approx(4.0161, abs=1e-3)


# --- the ask / verify / confirm round trip --------------------------------------------

def test_probe_permission_decision_builds_and_binds_the_batch():
    """The builder end to end on a SYNTHETIC bound task (the live-order tests' idiom), so
    the schema/policy/fingerprint path is exercised on core-neutral checkouts too."""
    from runtime.mvp_runtime.permission import build_slippage_probe_permission_decision

    bound = {
        "identity": {"task_id": "task_probe_unit_001", "trace_id": "trace_probe_unit_001",
                     "task_revision": 1},
        "context": {"core_context_binding_id": "ccb-unit:task_probe_unit_001:r1"},
    }
    params = _params()
    content = probe.probe_content_sha256(params)
    rec = build_slippage_probe_permission_decision(
        bound, batch_id=probe.batch_id_of(params), params=params,
        content_sha256=content, now=NOW,
    )
    assert rec["schema_version"] == "permission_decision.v0.4"
    payload = rec["fingerprint_payload"]
    assert payload["permission_scope"] == "RUNTIME_GOVERNANCE"
    assert payload["action_type"] == probe.PROBE_ACTION_TYPE
    assert payload["content_sha256"] == content
    assert rec["decision"]["permission_decision"] == "APPROVAL_REQUIRED"
    # Building the ask grants nothing: REVIEW_ONLY, every effect flag down.
    effect = rec["runtime_effect"]
    assert effect["mode"] == "REVIEW_ONLY"
    assert all(v is False for k, v in effect.items() if k != "mode")
    # A different batch is a different fingerprint — the re-pointing this hash exists to stop.
    other = _params(stop_bps=50.0)
    rec2 = build_slippage_probe_permission_decision(
        bound, batch_id=probe.batch_id_of(other), params=other,
        content_sha256=probe.probe_content_sha256(other), now=NOW,
    )
    assert rec2["action_fingerprint"] != rec["action_fingerprint"]


@requires_local_core
def test_request_builds_decision_and_pending_approval():
    prepared = probe.request_probe_batch(now=NOW)
    decision = prepared["permission_decision"]
    request = prepared["approval_request"]
    payload = decision["fingerprint_payload"]
    assert payload["permission_scope"] == "RUNTIME_GOVERNANCE"
    assert payload["action_type"] == probe.PROBE_ACTION_TYPE
    assert payload["content_sha256"] == prepared["content_sha256"]
    # A default request (no symbols named) still asks for exactly the batch-1 identity.
    assert prepared["content_sha256"] == BATCH_ONE_CONTENT_SHA256
    assert request["status"] == "PENDING"
    assert request["approved_action_snapshot"]["content_sha256"] == prepared["content_sha256"]


def test_confirm_writes_the_plan_and_verifies_content(tmp_path):
    params = _params()
    plan = probe.confirm_probe_batch(_fake_approval(params), params=params, root=tmp_path, now=NOW)
    assert plan["status"] == probe.PLAN_ACTIVE
    assert plan["approval_id"] == "approval_probe_test"
    assert probe.read_plan(tmp_path)["batch_id"] == probe.batch_id_of(params)
    assert len(plan["cells"]) == 12


@pytest.mark.parametrize("mutation,code", [
    (dict(status="PENDING"), "APPROVAL_NOT_APPROVED"),
    (dict(status="REJECTED"), "APPROVAL_NOT_APPROVED"),
    (dict(expires="2020-01-01T00:00:00Z"), "APPROVAL_EXPIRED"),
    (dict(action="crypto.strategy_pool.promotion"), "APPROVAL_WRONG_ACTION"),
    (dict(content="sha256:" + "0" * 64), "APPROVAL_CONTENT_MISMATCH"),
])
def test_verify_fails_closed(tmp_path, mutation, code):
    params = _params()
    with pytest.raises(ApprovalBlocked) as exc:
        probe.confirm_probe_batch(
            _fake_approval(params, **mutation), params=params, root=tmp_path, now=NOW)
    assert exc.value.reason_code == code
    assert probe.read_plan(tmp_path) is None  # a refused confirm writes nothing


def test_verify_missing_approval_fails(tmp_path):
    with pytest.raises(ApprovalBlocked) as exc:
        probe.confirm_probe_batch(None, params=_params(), root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_MISSING"


def test_an_approval_binds_one_parameter_set_only(tmp_path):
    # Approved at 40 bps; confirming a 50 bps batch on the same approval refuses.
    approved = _params()
    with pytest.raises(ApprovalBlocked) as exc:
        probe.confirm_probe_batch(
            _fake_approval(approved), params=_params(stop_bps=50.0), root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_CONTENT_MISMATCH"
    # Same refusal for a different SYMBOL SET: this is why --confirm takes --symbols —
    # a batch-2 approval can only confirm the exact set Thomas saw in the ask.
    with pytest.raises(ApprovalBlocked) as exc:
        probe.confirm_probe_batch(
            _fake_approval(approved),
            params=probe.build_batch_params(symbols=("BNBUSDT", "DOGEUSDT")),
            root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_CONTENT_MISMATCH"
    assert probe.read_plan(tmp_path) is None


def test_confirm_refuses_over_an_active_plan(tmp_path):
    params = _params()
    _active_plan(tmp_path, params)
    with pytest.raises(ToolError) as exc:
        probe.confirm_probe_batch(_fake_approval(params), params=params, root=tmp_path, now=NOW)
    assert exc.value.reason_code == probe.PROBE_PLAN_EXISTS


def test_confirm_over_a_complete_plan_starts_the_next_batch(tmp_path):
    """Batch 2's door, pinned: only ACTIVE blocks a confirm. A COMPLETE plan is
    replaced — its evidence lives in the ledger, not in this file — so finishing
    batch 1 is exactly what opens the BNBUSDT/DOGEUSDT ask."""
    first = _active_plan(tmp_path)
    for index in range(len(first["cells"])):
        first = probe.mark_cell(first, index, status=probe.CELL_OPEN, now=NOW)
        first = probe.mark_cell(first, index, status=probe.CELL_FILLED, now=NOW,
                                outcome_id=f"out_{index}", close_reason="stop_loss",
                                stop_slippage_bps=1.0)
    assert first["status"] == probe.PLAN_COMPLETE
    probe.write_plan(first, tmp_path)

    second = probe.build_batch_params(symbols=("BNBUSDT", "DOGEUSDT"))
    plan = probe.confirm_probe_batch(
        _fake_approval(second), params=second, root=tmp_path, now=NOW)
    assert plan["status"] == probe.PLAN_ACTIVE
    assert len(plan["cells"]) == 8
    assert probe.read_plan(tmp_path)["batch_id"] == probe.batch_id_of(second)


# --- the CLI surface: --symbols parsing and the status board --------------------------

def test_parse_symbols_splits_and_strips_only():
    assert cli._parse_symbols(None) is None
    assert cli._parse_symbols("BNBUSDT, dogeusdt") == ("BNBUSDT", "dogeusdt")
    # Empty entries survive to the builder's typed refusal — never dropped here, so a
    # typo'd list can only fail loudly, not shrink silently.
    assert cli._parse_symbols("") == ("",)
    assert cli._parse_symbols("BNBUSDT,,DOGEUSDT") == ("BNBUSDT", "", "DOGEUSDT")


def test_status_renders_the_plans_own_symbols(tmp_path, capsys):
    """The board reads the PLAN's params, not the module defaults: a batch-2 plan must
    show its own set and none of batch 1's."""
    params = probe.build_batch_params(symbols=("BNBUSDT", "DOGEUSDT"))
    probe.write_plan(probe.build_plan(params, approval_id="approval_2", now=NOW), tmp_path)
    assert cli.run_status(root=tmp_path) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "n=8 BNBUSDT/DOGEUSDT" in out
    assert out.count("BNBUSDT") >= 5  # the params line plus its four cells
    assert "BTCUSDT" not in out


# --- the fire door refuses, typed, before any venue call ------------------------------

class _VenueMustNotBeTouched:
    """A 'capable' adapter whose every venue verb is an assertion failure: the refusal
    paths under test must end BEFORE the venue."""

    tool_id = "test.adapter"
    tool_version = "0"
    network_egress = True

    def submit(self, *a, **k):  # pragma: no cover - reaching here IS the failure
        raise AssertionError("a refused probe touched the venue (submit)")

    def fetch_order(self, *a, **k):  # pragma: no cover
        raise AssertionError("a refused probe touched the venue (fetch_order)")

    def cancel_order(self, *a, **k):  # pragma: no cover
        raise AssertionError("a refused probe touched the venue (cancel_order)")


def _fire(tmp_path, **kwargs):
    return cli.run_fire(root=tmp_path, symbol=kwargs.pop("symbol", "BTCUSDT"),
                        poll_seconds=0.0, sleep=lambda s: None, **kwargs)


def _snapshot():
    return types.SimpleNamespace(positions=[], realized_windows={}, available_balance=1000.0)


def _arm_limits(monkeypatch, *, symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT")):
    limits = LiveOrderLimits(
        max_order_notional_usdt=150.0, max_daily_order_count=10,
        max_open_notional_usdt=300.0, daily_loss_limit_usdt=50.0,
        min_clean_canary_orders=3, canary_confirmation=CANARY_CONFIRMATION_PHRASE,
    )
    monkeypatch.setattr(cli, "resolve_live_order_limits",
                        lambda root, now=None: (limits, {"valid": True, "symbol_allowlist": list(symbols)}))
    return limits


def test_fire_refuses_without_a_plan(tmp_path):
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_MISSING


def test_fire_hard_refuses_when_live_trading_is_off(tmp_path):
    # conftest strips MVP_LIVE_TRADING, so the real selector returns the inert adapter;
    # the door must surface that as a typed BLOCK, never dry-run through it.
    _active_plan(tmp_path)
    assert isinstance(cli.live_execution.select_order_adapter(), DryRunOrderAdapter)
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_LIVE_TRADING_OFF


def test_fire_refuses_while_a_probe_position_is_open(tmp_path, monkeypatch):
    plan = _active_plan(tmp_path)
    plan = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_1")
    probe.write_plan(plan, tmp_path)
    # The OPEN cell's position is still on the book, so the cell cannot resolve.
    monkeypatch.setattr(cli, "load_open_live_position", lambda symbol, root=None: {"status": "OPEN"})
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_CELL_OPEN


def test_fire_refuses_on_the_daily_loss_breaker(tmp_path, monkeypatch):
    _active_plan(tmp_path)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    _arm_limits(monkeypatch)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    monkeypatch.setattr(cli, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": True, "daily_realized_pnl_usdt": -51.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_DAILY_LOSS_BREAKER


def test_fire_refuses_on_the_bracket_breaker(tmp_path, monkeypatch):
    _active_plan(tmp_path)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    _arm_limits(monkeypatch)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    monkeypatch.setattr(cli, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": True, "consecutive": 2, "limit": 2})
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_BRACKET_BREAKER


def test_fire_refuses_a_venue_minimum_above_the_plan_ceiling(tmp_path, monkeypatch):
    _active_plan(tmp_path)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    _arm_limits(monkeypatch)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    monkeypatch.setattr(cli, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": False, "consecutive": 0, "limit": 2})
    monkeypatch.setattr(cli, "resolve_risk_limits", lambda root, now=None: None)
    # The venue floor prices this symbol's minimum at 120 USDT — above the 100 USDT
    # per-probe ceiling the approval budgeted, so the fire refuses.
    monkeypatch.setattr(cli, "_read_filters", lambda *a, **k: SymbolFilters(
        step_size=0.001, min_qty=0.001, min_notional=120.0, tick_size=0.1))
    monkeypatch.setattr(cli, "_read_price", lambda *a, **k: 100000.0)
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_NOTIONAL_ABOVE_PLAN


# --- one full probe, stop filled, measured through the existing #683 path -------------

class _FakeStore:
    """The live book as a dict: gated-store shaped, zero disk."""

    filesystem_write = True

    def __init__(self):
        self.positions = {}

    def save_position(self, position):
        self.positions[position["symbol"]] = dict(position)

    def clear_position(self, symbol):
        self.positions.pop(symbol, None)


class _FakeLedger:
    filesystem_write = True

    def __init__(self):
        self.outcomes = []

    def append_outcome(self, record):
        self.outcomes.append(dict(record))


class _FakeCounter:
    def __init__(self):
        self.count = 0

    def record_submission(self, *, day=None):
        self.count += 1
        return self.count


class _HappyPathAdapter:
    """Entry fills at 100000; the stop rests, then fills 40 bps + ~4 bps past it."""

    tool_id = "test.adapter"
    tool_version = "0"
    network_egress = True

    def __init__(self):
        self.stop_reads = 0
        self.cancelled = []

    def submit(self, order_request, *, timeout_seconds=10):
        return {"orderId": 11, "algoId": 77}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        if "_SL_" in client_order_id:
            self.stop_reads += 1
            if self.stop_reads == 1:  # place_bracket_leg's confirm: resting
                return {"symbol": symbol, "status": "NEW", "orderId": 77}
            return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 77,
                    "executedQty": "0.001", "avgPrice": "99560.0", "cumQuote": "99.56"}
        return {"symbol": symbol, "side": "BUY", "status": "FILLED", "orderId": 11,
                "executedQty": "0.001", "avgPrice": "100000.0", "cumQuote": "100.0",
                "reduceOnly": False}

    def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
        self.cancelled.append(client_order_id)
        return None  # already gone — the triggered leg


def test_fire_places_measures_and_marks_one_cell(tmp_path, monkeypatch):
    _active_plan(tmp_path)
    adapter = _HappyPathAdapter()
    store, ledger, counter = _FakeStore(), _FakeLedger(), _FakeCounter()

    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: adapter)
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_HIGH)
    _arm_limits(monkeypatch)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    monkeypatch.setattr(cli, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": False, "consecutive": 0, "limit": 2})
    monkeypatch.setattr(cli, "resolve_risk_limits", lambda root, now=None: None)
    monkeypatch.setattr(cli, "_read_filters", lambda *a, **k: SymbolFilters(
        step_size=0.001, min_qty=0.001, min_notional=100.0, tick_size=0.1))
    monkeypatch.setattr(cli, "_read_price", lambda *a, **k: 100000.0)
    monkeypatch.setattr(cli, "select_live_position_store", lambda now=None, root=None: store)
    monkeypatch.setattr(cli, "select_live_ledger", lambda now=None, root=None: ledger)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    monkeypatch.setattr(cli, "load_open_live_position",
                        lambda symbol, root=None: store.positions.get(symbol))
    monkeypatch.setattr(cli.live_governance, "prepare_live_order_governance",
                        lambda intent, *, purpose, now, repo_root=None: {
                            "purpose": purpose, "bound_task": {},
                            "permission_decision": {"permission_decision_id": "pd_test"}})
    monkeypatch.setattr(cli, "_audit_order", lambda *a, **k: None)

    assert _fire(tmp_path) == cli.EXIT_OK

    # The measurement rode the existing #683 path: one outcome, stop close, slippage
    # measured against the trigger the position record carried.
    assert counter.count == 1  # the probe consumed daily order budget
    assert len(ledger.outcomes) == 1
    outcome = ledger.outcomes[0]
    assert outcome["close_reason"] == "stop_loss"
    assert outcome["strategy_id"].startswith(probe.PROBE_STRATEGY_PREFIX)
    assert outcome["candidate_id"] is None and outcome["strategy_rule_hash"] is None
    # entry 100000 -> trigger 99600 (40 bps, tick-rounded); fill 99560 -> +4.0161 bps adverse.
    assert outcome["stop_price"] == pytest.approx(99600.0)
    assert outcome["stop_slippage_bps"] == pytest.approx(4.0161, abs=1e-3)
    assert store.positions == {}  # the book is clear again

    plan = probe.read_plan(tmp_path)
    filled = [c for c in plan["cells"] if c["status"] == probe.CELL_FILLED]
    assert len(filled) == 1
    assert filled[0]["symbol"] == "BTCUSDT" and filled[0]["regime"] == probe.REGIME_HIGH
    assert filled[0]["stop_slippage_bps"] == pytest.approx(4.0161, abs=1e-3)
    assert filled[0]["outcome_id"] == outcome["outcome_id"]

    # And the probe row can never contaminate forward confirmation.
    candidate = {"candidate_id": "cand_x", "strategy_id": "S1",
                 "generation_id": "GEN-001", "strategy_rule_hash": "h",
                 "strategy_spec": {"timeframe": "4h"}}
    assert forward_confirmation.judge_forward(candidate, ledger.outcomes)["closed_count"] == 0


def test_fire_returns_the_cell_when_the_stop_will_not_rest(tmp_path, monkeypatch):
    """Rule 2 on the probe path: stop refused -> the entry is closed, the cell comes
    back EMPTY with the reason, and nothing stays open."""
    _active_plan(tmp_path)

    class _StopRefusingAdapter(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                return None  # the stop never lands
            if "_CLOSE_" in client_order_id:
                return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 12,
                        "executedQty": "0.001", "avgPrice": "99990.0", "cumQuote": "99.99",
                        "reduceOnly": True}
            return super().fetch_order(symbol, client_order_id,
                                       timeout_seconds=timeout_seconds, algo=algo)

    adapter = _StopRefusingAdapter()
    store, ledger, counter = _FakeStore(), _FakeLedger(), _FakeCounter()
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: adapter)
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    # The close guard needs the AUTONOMOUS phrase too (the naked close is a reduceOnly
    # runtime close); thread both phrases through the same resolved limits.
    monkeypatch.setattr(cli, "resolve_live_order_limits", lambda root, now=None: (
        LiveOrderLimits(
            max_order_notional_usdt=150.0, max_daily_order_count=10,
            max_open_notional_usdt=300.0, daily_loss_limit_usdt=50.0,
            min_clean_canary_orders=3, canary_confirmation=CANARY_CONFIRMATION_PHRASE,
            confirmation=LIVE_CONFIRMATION_PHRASE,
        ),
        {"valid": True, "symbol_allowlist": ["BTCUSDT", "ETHUSDT", "SOLUSDT"]},
    ))
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    monkeypatch.setattr(cli, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": False, "consecutive": 0, "limit": 2})
    monkeypatch.setattr(cli, "resolve_risk_limits", lambda root, now=None: None)
    monkeypatch.setattr(cli, "_read_filters", lambda *a, **k: SymbolFilters(
        step_size=0.001, min_qty=0.001, min_notional=100.0, tick_size=0.1))
    monkeypatch.setattr(cli, "_read_price", lambda *a, **k: 100000.0)
    monkeypatch.setattr(cli, "select_live_position_store", lambda now=None, root=None: store)
    monkeypatch.setattr(cli, "select_live_ledger", lambda now=None, root=None: ledger)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    monkeypatch.setattr(cli, "load_open_live_position",
                        lambda symbol, root=None: store.positions.get(symbol))
    monkeypatch.setattr(cli.live_governance, "prepare_live_order_governance",
                        lambda intent, *, purpose, now, repo_root=None: {
                            "purpose": purpose, "bound_task": {},
                            "permission_decision": {"permission_decision_id": "pd_test"}})
    monkeypatch.setattr(cli, "_audit_order", lambda *a, **k: None)

    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    plan = probe.read_plan(tmp_path)
    assert plan["cells"][0]["status"] == probe.CELL_EMPTY
    assert probe.PROBE_STOP_NOT_PLACED in (plan["cells"][0]["note"] or "")
    assert store.positions == {}  # nothing left open
    # The naked close still recorded its outcome (money moved; the breaker must see it).
    assert len(ledger.outcomes) == 1
    assert ledger.outcomes[0]["close_reason"] == "naked_position_close"
