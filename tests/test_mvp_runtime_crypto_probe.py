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
from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.crypto import forward_confirmation, lifecycle, live_route, probe
from runtime.mvp_runtime.crypto.live_execution import DryRunOrderAdapter
from runtime.mvp_runtime.crypto.live_order import (
    CANARY_CONFIRMATION_PHRASE,
    LIVE_CONFIRMATION_PHRASE,
    LiveOrderLimits,
)
from runtime.mvp_runtime.crypto.live_pnl import build_live_outcome_record
from runtime.mvp_runtime.crypto.live_sizing import SymbolFilters
from runtime.mvp_runtime.errors import ApprovalBlocked, MvpRuntimeError, ToolError
from tests._helpers import FakeSnapshotStore, requires_local_core

NOW = timeutil.utc_now_iso()

# Frozen on origin/main @ 2bfd610 — the last commit BEFORE symbols became a request-time
# parameter. The batch-1 identity (40 bps / 120 m): its approval was CONSUMED on
# 2026-08-11, so since the 2026-08-17 parameter re-set the DEFAULTS no longer reproduce
# it — the explicit params in `_params()` must, byte for byte, because the ledger rows
# batch 1 minted carry this id and a drift here would orphan them.
BATCH_ONE_CONTENT_SHA256 = "sha256:11a6109dc75131cd4998cbd251e4b590f4c5a0bada3d45946618ff69f61c1a2f"
BATCH_ONE_BATCH_ID = "probe_batch_900fc0ad8ea51614381a"

# Frozen 2026-08-17, the touch-rate re-set (stop 25 bps, timeout 240 m — measured 40/120
# bought 27-42% low_vol fills and three timeouts in four real probes). No standing
# approval binds the DEFAULT identity today (batch 1 consumed, batch 2 hash-bound to its
# own params), which is the only condition under which this freeze may move; the next
# default drift is still a deliberate act, caught here.
DEFAULT_BATCH_CONTENT_SHA256 = "sha256:da5ece09b5f4141f1f849c6e1f6766418e6b8bb7cf0acc4a4590a2347680488d"
DEFAULT_BATCH_BATCH_ID = "probe_batch_9e2e3f066e8dbe4376c1"


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


def _seed_plan(plan, root):
    """Write ``plan`` over whatever the store holds now, as a test's setup does (PR2c-2a: every
    write names the plan it replaces)."""
    held = probe.read_plan(root)
    return probe.write_plan(plan, root, expected_sha256=held["record_sha256"] if held else None)


def _active_plan(tmp_path, params=None):
    params = params or _params()
    return _seed_plan(
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
    """--symbols omitted must reproduce the FROZEN default identity byte for byte — a
    quiet default drift re-mints what an operator believes they are asking for. The
    identity moved once, deliberately: 2026-08-17, 40/120 → 25/240 off the measured
    touch rates, at a moment no standing approval bound the default hash. The batch-1
    identity stays reproducible from its explicit params — the ledger rows it minted
    carry that id."""
    params = probe.build_batch_params()
    assert probe.probe_content_sha256(params) == DEFAULT_BATCH_CONTENT_SHA256
    assert probe.batch_id_of(params) == DEFAULT_BATCH_BATCH_ID
    assert params["n"] == 12 and params["symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert params["stop_bps"] == 25.0 and params["timeout_minutes"] == 240
    assert probe.probe_content_sha256(_params()) == BATCH_ONE_CONTENT_SHA256
    assert probe.batch_id_of(_params()) == BATCH_ONE_BATCH_ID


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
    _seed_plan(updated, tmp_path)
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
    # A default request (no symbols named) asks for exactly the frozen default identity.
    assert prepared["content_sha256"] == DEFAULT_BATCH_CONTENT_SHA256
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


def test_a_timeout_override_must_be_repeated_at_confirm(tmp_path):
    """The batch-3 decision (Thomas 2026-08-18): 400m rides the ask as a request-time
    parameter, exactly like --symbols — the content hash binds it, so a confirm that
    falls back to the module default refuses rather than confirming a batch with a
    timeout Thomas never saw."""
    asked = probe.build_batch_params(timeout_minutes=400)
    with pytest.raises(ApprovalBlocked) as exc:
        probe.confirm_probe_batch(_fake_approval(asked), params=None, root=tmp_path, now=NOW)
    assert exc.value.reason_code == "APPROVAL_CONTENT_MISMATCH"
    assert probe.read_plan(tmp_path) is None

    plan = probe.confirm_probe_batch(_fake_approval(asked), params=asked, root=tmp_path, now=NOW)
    assert plan["status"] == probe.PLAN_ACTIVE
    assert plan["params"]["timeout_minutes"] == 400


def test_cli_request_and_confirm_derive_the_same_override_params():
    """`_override_params` is the single derivation both verbs share: a drift between the
    request's batch and the confirm's is exactly the mismatch the hash refuses, so the
    derivation itself is pinned here once."""
    assert cli._override_params(None, None) is None
    only_timeout = cli._override_params(None, 400)
    assert only_timeout == probe.build_batch_params(timeout_minutes=400)
    both = cli._override_params(("BNBUSDT", "DOGEUSDT"), 400)
    assert both == probe.build_batch_params(symbols=("BNBUSDT", "DOGEUSDT"), timeout_minutes=400)
    assert cli._override_params(("BNBUSDT", "DOGEUSDT"), None) == probe.build_batch_params(
        symbols=("BNBUSDT", "DOGEUSDT"))


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
    _seed_plan(first, tmp_path)

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
    _seed_plan(probe.build_plan(params, approval_id="approval_2", now=NOW), tmp_path)
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
    """An account read now (PR2c-1: the gate refuses one read more than a minute before it)."""
    return types.SimpleNamespace(positions=[], realized_windows={}, available_balance=1000.0,
                                 collected_at=timeutil.utc_now_iso())


def _arm_runtime(tmp_path):
    """An operator-armed runtime. A state root with no control file reads ACTIVE but UNARMED
    (Thomas decision 10, 2026-09-15), and a probe is an entry, so the happy path arms first."""
    from runtime.mvp_runtime.control import ACTIVE, ControlState, ControlStore

    ControlStore(tmp_path).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW,
                                             reason="armed", trading_armed=True))


def _arm_limits(monkeypatch, *, symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"),
                stage="LIVE_AUTONOMOUS", stage_valid=True, stage_reason=None):
    limits = LiveOrderLimits(
        max_order_notional_usdt=150.0, max_daily_order_count=10,
        max_open_notional_usdt=300.0, daily_loss_limit_usdt=50.0,
        canary_confirmation=CANARY_CONFIRMATION_PHRASE,
    )
    budget = lambda root, now=None: (limits, dict(_BUDGET, symbol_allowlist=list(symbols)))  # noqa: E731
    # The execution stage the probe resolves (PR1b). A real record needs a spent Thomas approval,
    # so the resolver is stubbed here; the stage door's own refusal has its test below. A binding
    # record names its id, hash and approval — the pre-order gate's profile requires them (PR2b).
    stage_status = lambda root=None, **kw: es.StageStatus(  # noqa: E731
        stage=stage if stage_valid else "READ_ONLY", valid=stage_valid,
        reason_code=stage_reason, recorded_stage=stage if stage_valid else None,
        **(_STAGE_IDS if stage_valid else {}))
    _stub_both(monkeypatch, "resolve_live_order_limits", budget)
    _stub_both(monkeypatch, "resolve_execution_stage", stage_status)
    return limits


def _stub_both(monkeypatch, name, value):
    """The fire reads the budget and the stage once, and its gate's re-read (PR2c-2a) reads them
    again through the route: a stub stands for both reads."""
    for module in (cli, live_route):
        monkeypatch.setattr(module, name, value)


# What a registered budget and a binding stage record carry beside their numbers (PR2b).
_BUDGET = {"valid": True, "budget_id": "budget_probe_test", "record_sha256": "sha256:" + "b" * 64}
_STAGE_IDS = {"stage_id": "stage_probe_test", "record_sha256": "sha256:" + "5" * 64,
              "approval_id": "approval_stage_probe_test"}


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
    _seed_plan(plan, tmp_path)
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
    _stub_both(monkeypatch, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": True, "daily_realized_pnl_usdt": -51.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_DAILY_LOSS_BREAKER


def test_fire_refuses_when_the_account_read_carries_no_realized_figure(tmp_path, monkeypatch):
    """A probe opens a position, so it takes the entry rule (2026-09-15): the REAL breaker, a
    snapshot with no realized windows, and the door refuses naming why."""
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_PNL_VENUE_FIGURE_MISSING

    _active_plan(tmp_path)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    _arm_limits(monkeypatch)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))   # realized_windows={}
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_DAILY_LOSS_BREAKER
    assert LIVE_PNL_VENUE_FIGURE_MISSING in str(exc.value)


def test_fire_refuses_on_the_bracket_breaker(tmp_path, monkeypatch):
    _active_plan(tmp_path)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    _arm_limits(monkeypatch)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    _stub_both(monkeypatch, "live_risk_snapshot", lambda **k: {
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
    _stub_both(monkeypatch, "live_risk_snapshot", lambda **k: {
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

    def clear_position(self, symbol, *, position_id=None):
        # The real store's rule: only the named position's record is removed.
        held = self.positions.get(symbol)
        if held is not None and position_id is not None and held.get("position_id") != position_id:
            raise ToolError("LIVE_POSITION_SLOT_TAKEN", "scripted: another position holds the symbol")
        self.positions.pop(symbol, None)


class _FakeLedger:
    filesystem_write = True

    def __init__(self):
        self.outcomes = []

    def append_outcome(self, record):
        self.outcomes.append(dict(record))


class _FakeCounter:
    """The daily counter as the probe spends it since PR2a: reserve-or-refuse before the send."""

    def __init__(self, count=0, adapter=None):
        self.count = count
        self.limits = []
        # The adapter's submits as seen at reservation time — "before the send" made checkable.
        self.submitted_at_reserve = None
        self._adapter = adapter

    def reserve_submission(self, *, limit, day=None):
        self.limits.append(limit)
        if self._adapter is not None:
            self.submitted_at_reserve = len(self._adapter.submitted)
        if self.count >= limit:
            raise ToolError("LIVE_DAILY_ORDER_CAP_REACHED", "scripted cap")
        self.count += 1
        return self.count


class _FakeBreaker:
    def __init__(self, error=None, raises=ToolError):
        self.failures = []
        self.successes = 0
        self._error = error
        self._raises = raises

    def record_failure(self, **kw):
        if self._error:
            if self._raises is OSError:
                raise OSError(28, "No space left on device")
            raise self._raises(self._error, "scripted breaker failure")
        self.failures.append(kw)
        return {}

    def record_success(self):
        self.successes += 1
        return {}


class _HappyPathAdapter:
    """Entry fills at 100000; the stop rests, then fills 40 bps + ~4 bps past it."""

    tool_id = "test.adapter"
    tool_version = "0"
    network_egress = True

    def __init__(self):
        self.stop_reads = 0
        self.cancelled = []
        self.submitted = []

    def submit(self, order_request, *, timeout_seconds=10):
        self.submitted.append(order_request)
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

    # Nothing rests at the venue before the probe (PR2c-3). A test that needs otherwise sets these.
    resting_plain: list = []
    resting_algo: list = []
    resting_error = None

    def open_orders(self, symbol=None, *, timeout_seconds=10):
        if self.resting_error is not None:
            raise self.resting_error
        return list(self.resting_plain)

    def algo_open_orders(self, symbol=None, *, timeout_seconds=10):
        if self.resting_error is not None:
            raise self.resting_error
        return list(self.resting_algo)


def test_fire_places_measures_and_marks_one_cell(tmp_path, monkeypatch):
    _active_plan(tmp_path)
    _arm_runtime(tmp_path)
    adapter = _HappyPathAdapter()
    store, ledger, counter = _FakeStore(), _FakeLedger(), _FakeCounter(adapter=adapter)
    breaker = _FakeBreaker()
    monkeypatch.setattr(cli, "select_live_bracket_breaker", lambda now=None, root=None: breaker)

    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: adapter)
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_HIGH)
    _arm_limits(monkeypatch)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    _stub_both(monkeypatch, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": False, "consecutive": 0, "limit": 2})
    monkeypatch.setattr(cli, "resolve_risk_limits", lambda root, now=None: None)
    monkeypatch.setattr(cli, "_read_filters", lambda *a, **k: SymbolFilters(
        step_size=0.001, min_qty=0.001, min_notional=100.0, tick_size=0.1))
    monkeypatch.setattr(cli, "_read_price", lambda *a, **k: 100000.0)
    monkeypatch.setattr(cli, "select_live_position_store", lambda now=None, root=None: store)
    # A capable adapter records its snapshot in a store that writes (PR2b review): the two
    # selectors read one switch in production, so they are wired together here.
    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store",
                        lambda now=None, root=None: FakeSnapshotStore())
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
    # ...reserved against the registered cap, before anything was sent (PR2a).
    assert counter.limits == [10] and counter.submitted_at_reserve == 0
    # A stop that rests proves the stop leg only — never the target leg the autonomous bracket
    # also needs — so a probe neither counts on nor clears the bracket-failure streak.
    assert breaker.failures == [] and breaker.successes == 0
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
    _arm_runtime(tmp_path)

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
    breaker = _FakeBreaker()
    monkeypatch.setattr(cli, "select_live_bracket_breaker", lambda now=None, root=None: breaker)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: adapter)
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    # The close guard needs the AUTONOMOUS phrase too (the naked close is a reduceOnly
    # runtime close); thread both phrases through the same resolved limits.
    _stub_both(monkeypatch, "resolve_live_order_limits", lambda root, now=None: (
        LiveOrderLimits(
            max_order_notional_usdt=150.0, max_daily_order_count=10,
            max_open_notional_usdt=300.0, daily_loss_limit_usdt=50.0,
            canary_confirmation=CANARY_CONFIRMATION_PHRASE,
            confirmation=LIVE_CONFIRMATION_PHRASE,
        ),
        dict(_BUDGET, symbol_allowlist=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    ))
    _stub_both(monkeypatch, "resolve_execution_stage", lambda root=None, **kw: es.StageStatus(
        stage="LIVE_AUTONOMOUS", valid=True, reason_code=None, recorded_stage="LIVE_AUTONOMOUS",
        **_STAGE_IDS))
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    _stub_both(monkeypatch, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": False, "consecutive": 0, "limit": 2})
    monkeypatch.setattr(cli, "resolve_risk_limits", lambda root, now=None: None)
    monkeypatch.setattr(cli, "_read_filters", lambda *a, **k: SymbolFilters(
        step_size=0.001, min_qty=0.001, min_notional=100.0, tick_size=0.1))
    monkeypatch.setattr(cli, "_read_price", lambda *a, **k: 100000.0)
    monkeypatch.setattr(cli, "select_live_position_store", lambda now=None, root=None: store)
    # A capable adapter records its snapshot in a store that writes (PR2b review): the two
    # selectors read one switch in production, so they are wired together here.
    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store",
                        lambda now=None, root=None: FakeSnapshotStore())
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
    # And the stop that would not rest counts on the bracket-failure streak (PR2a): the probe
    # hangs its stop through the autonomous placement, so a broken stop path is broken for both.
    [failure] = breaker.failures
    assert failure["symbol"] == "BTCUSDT"
    assert failure["status"] == cli.live_leg.ENTRY_NAKED_CLOSED
    assert failure["reason_codes"] == [probe.PROBE_STOP_NOT_PLACED]
    assert failure["error_detail"] and failure["error_detail"][0]["placed"] is False
    assert breaker.successes == 0


# --- PR2a: the symbol must be free, and the day's slot is reserved before the send -----

def _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter, *, book=(), venue=()):
    """Every door up to the guard open, with the book and the venue account as given."""
    from runtime.mvp_runtime.crypto.account import AccountPosition

    _active_plan(tmp_path)
    _arm_runtime(tmp_path)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter", lambda now=None, root=None: adapter)
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    _arm_limits(monkeypatch)
    positions = [AccountPosition(symbol=symbol, side="LONG", quantity=0.001, entry_price=100000.0,
                                 mark_price=100000.0, unrealized_pnl=0.0, leverage=5.0, notional=100.0)
                 for symbol in venue]
    snapshot = types.SimpleNamespace(positions=positions, realized_windows={}, available_balance=1000.0,
                                     collected_at=timeutil.utc_now_iso())
    monkeypatch.setattr(cli, "read_account", lambda **k: (snapshot, {}))
    monkeypatch.setattr(cli, "list_open_live_positions", lambda root=None: [
        {"symbol": symbol, "direction": "LONG", "quantity": 0.001, "status": "OPEN",
         "position_id": f"pos_{symbol}"} for symbol in book])
    _stub_both(monkeypatch, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": False, "consecutive": 0, "limit": 5})
    monkeypatch.setattr(cli, "resolve_risk_limits", lambda root, now=None: None)
    monkeypatch.setattr(cli, "_read_filters", lambda *a, **k: SymbolFilters(
        step_size=0.001, min_qty=0.001, min_notional=100.0, tick_size=0.1))
    monkeypatch.setattr(cli, "_read_price", lambda *a, **k: 100000.0)
    monkeypatch.setattr(cli.live_governance, "prepare_live_order_governance",
                        lambda intent, *, purpose, now, repo_root=None: {
                            "purpose": purpose, "bound_task": {},
                            "permission_decision": {"permission_decision_id": "pd_test"}})
    monkeypatch.setattr(cli, "_audit_order", lambda *a, **k: None)


@pytest.mark.parametrize("book,venue", [
    (("BTCUSDT",), ("BTCUSDT",)),            # an autonomous position is open on the symbol
    ((), ("BTCUSDT",)),                      # the venue holds one the book does not know
    (("ETHUSDT",), ()),                      # the book and the venue disagree elsewhere
    (("ETHUSDT", "SOLUSDT"), ("ETHUSDT", "SOLUSDT")),   # LP5's two slots are both taken
], ids=["same-symbol", "untracked-at-venue", "drift-elsewhere", "caps-full"])
def test_fire_refuses_a_symbol_that_is_not_free(tmp_path, monkeypatch, book, venue):
    """The book is one record per symbol and the venue nets per symbol, so a probe on a symbol
    holding a live position would overwrite that position's record and merge into its
    exposure. Refused before the breakers, the guard and the venue."""
    _wire_fire_to_the_guard(tmp_path, monkeypatch, _VenueMustNotBeTouched(), book=book, venue=venue)
    monkeypatch.setattr(cli, "live_risk_snapshot",
                        lambda **k: pytest.fail("the conflict must refuse before the breakers"))
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_POSITION_CONFLICT
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


def test_fire_sends_nothing_when_no_order_slot_can_be_reserved(tmp_path, monkeypatch):
    """The guard judged a count read earlier; the scheduler's live leg may have spent the last
    slot since. The reservation is the answer, and a refused one leaves the cell untouched."""
    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    counter = _FakeCounter(count=10, adapter=adapter)   # the cap is 10, all spent
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_ORDER_SLOT_REFUSED
    assert "LIVE_DAILY_ORDER_CAP_REACHED" in str(exc.value)
    assert adapter.submitted == []
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


def test_fire_on_the_real_counter_refuses_the_slot_the_live_leg_already_spent(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import LiveOrderCounter, count_today
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
    from tests._helpers import make_gate_authorization

    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    counter = LiveOrderCounter(root=tmp_path, authorization=auth)
    for _ in range(10):
        counter.record_submission()
    # The guard and the gate's re-read are both told a stale count, as they would be if the other
    # process spent the slot after the re-read: the reservation is what refuses.
    _stub_both(monkeypatch, "count_today", lambda root=None: 9)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_ORDER_SLOT_REFUSED
    assert adapter.submitted == []
    assert count_today(tmp_path) == 10


def test_fire_s_gate_re_reads_a_slot_the_live_leg_spent_after_the_first_read(tmp_path, monkeypatch):
    """PR2c-2a: the first read said 9 of 10; by the gate the other process had spent the tenth. The
    re-read sees it, so the gate refuses before the symbol, the slot or the cell is touched."""
    from runtime.mvp_runtime.crypto.live_order import LiveOrderCounter, count_today
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
    from tests._helpers import make_gate_authorization

    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    counter = LiveOrderCounter(root=tmp_path, authorization=auth)
    for _ in range(10):
        counter.record_submission()
    monkeypatch.setattr(cli, "count_today", lambda root=None: 9)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PRE_ORDER_GATE_REFUSED
    assert "daily_order_count_within_cap" in str(exc.value)
    assert adapter.submitted == [] and count_today(tmp_path) == 10
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


@pytest.mark.parametrize("raises,code", [
    (ToolError, "LIVE_BRACKET_BREAKER_LOCKED"),
    (OSError, "OSError"),   # what the real breaker's write raises; must not skip the cell's return
])
def test_a_breaker_that_cannot_record_the_stop_failure_is_reported(tmp_path, monkeypatch, capsys,
                                                                   raises, code):
    class _StopNeverRests(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                return None
            if "_CLOSE_" in client_order_id:
                return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 12,
                        "executedQty": "0.001", "avgPrice": "99990.0", "cumQuote": "99.99",
                        "reduceOnly": True}
            return super().fetch_order(symbol, client_order_id,
                                       timeout_seconds=timeout_seconds, algo=algo)

    adapter = _StopNeverRests()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    _stub_both(monkeypatch, "resolve_live_order_limits", lambda root, now=None: (
        LiveOrderLimits(
            max_order_notional_usdt=150.0, max_daily_order_count=10,
            max_open_notional_usdt=300.0, daily_loss_limit_usdt=50.0,
            canary_confirmation=CANARY_CONFIRMATION_PHRASE,
            confirmation=LIVE_CONFIRMATION_PHRASE,
        ),
        dict(_BUDGET, symbol_allowlist=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    ))
    store, ledger = _FakeStore(), _FakeLedger()
    monkeypatch.setattr(cli, "select_live_position_store", lambda now=None, root=None: store)
    # A capable adapter records its snapshot in a store that writes (PR2b review): the two
    # selectors read one switch in production, so they are wired together here.
    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store",
                        lambda now=None, root=None: FakeSnapshotStore())
    monkeypatch.setattr(cli, "select_live_ledger", lambda now=None, root=None: ledger)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: _FakeCounter())
    monkeypatch.setattr(cli, "load_open_live_position",
                        lambda symbol, root=None: store.positions.get(symbol))
    monkeypatch.setattr(cli, "select_live_bracket_breaker",
                        lambda now=None, root=None: _FakeBreaker(error="LIVE_BRACKET_BREAKER_LOCKED",
                                                                 raises=raises))

    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    err = capsys.readouterr().err
    assert f"BREAKER   : NOT recorded ({code})" in err
    assert probe.read_plan(tmp_path)["cells"][0]["status"] == probe.CELL_EMPTY
    assert store.positions == {}


# --- abandoning a batch early (Thomas 2026-08-11) --------------------------------------

def test_abandon_retires_an_active_plan_and_opens_the_next_confirm(tmp_path):
    """The PROBE_PLAN_EXISTS refusal's own 'resolve' verb: the invariant (one plan at a
    time) is untouched — what changes is that an operator can END a batch deliberately
    instead of firing probes nobody wants."""
    _active_plan(tmp_path)
    plan = probe.abandon_plan(reason="two cells were enough", now=NOW, root=tmp_path)
    assert plan["status"] == probe.PLAN_ABANDONED
    assert probe.read_plan(tmp_path)["status"] == probe.PLAN_ABANDONED

    second = probe.build_batch_params(symbols=("BNBUSDT", "DOGEUSDT"))
    replacement = probe.confirm_probe_batch(
        _fake_approval(second), params=second, root=tmp_path, now=NOW)
    assert replacement["status"] == probe.PLAN_ACTIVE
    assert len(replacement["cells"]) == 8


def test_abandon_refuses_while_a_probe_is_at_the_venue(tmp_path):
    """An OPEN cell means a real position may be resting; the plan that tracks it must
    not be closed out from under it."""
    plan = _active_plan(tmp_path)
    _seed_plan(
        probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_x"),
        tmp_path,
    )
    with pytest.raises(ToolError) as exc:
        probe.abandon_plan(reason="r", now=NOW, root=tmp_path)
    assert exc.value.reason_code == probe.PROBE_CELL_OPEN
    assert probe.read_plan(tmp_path)["status"] == probe.PLAN_ACTIVE


def test_abandon_refuses_without_a_plan_or_a_reason(tmp_path):
    with pytest.raises(ToolError) as exc:
        probe.abandon_plan(reason="r", now=NOW, root=tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_MISSING
    _active_plan(tmp_path)
    with pytest.raises(ToolError) as exc:
        probe.abandon_plan(reason="   ", now=NOW, root=tmp_path)
    assert exc.value.reason_code == probe.PROBE_PARAMS_INVALID


def test_abandon_is_terminal_not_repeatable(tmp_path):
    _active_plan(tmp_path)
    probe.abandon_plan(reason="done", now=NOW, root=tmp_path)
    with pytest.raises(ToolError) as exc:
        probe.abandon_plan(reason="again", now=NOW, root=tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_NOT_ACTIVE


def test_an_abandoned_plan_still_loads_and_reports(tmp_path):
    """ABANDONED is a first-class terminal status: the board must keep rendering the
    batch's history (filled cells, sample rows) after the retirement."""
    plan = _active_plan(tmp_path)
    plan = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_1")
    plan = probe.mark_cell(plan, 0, status=probe.CELL_FILLED, now=NOW,
                           outcome_id="out_1", stop_slippage_bps=0.28)
    _seed_plan(plan, tmp_path)
    probe.abandon_plan(reason="sample judged sufficient", now=NOW, root=tmp_path)
    loaded = probe.read_plan(tmp_path)
    assert loaded["status"] == probe.PLAN_ABANDONED
    assert loaded["cells"][0]["status"] == probe.CELL_FILLED
    assert loaded["cells"][0]["stop_slippage_bps"] == 0.28


def test_abandon_reconciles_a_stale_open_cell_before_refusing(tmp_path, monkeypatch):
    """The day-one incident, pinned: the operator hand-closed the probe position at the
    venue while --fire was dead. --abandon must run --fire's own reconcile (book clear +
    no ledger row -> the cell returns to EMPTY) instead of refusing on a state that is
    no longer true — without it, the only verb that could clear the cell was the one
    that places a NEW probe."""
    plan = _active_plan(tmp_path)
    plan = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_gone")
    _seed_plan(plan, tmp_path)
    monkeypatch.setattr(cli, "load_open_live_position", lambda symbol, root=None: None)
    monkeypatch.setattr(cli, "read_live_outcomes", lambda root=None: [])
    assert cli.run_abandon(reason="hand-closed; moving to batch 2", root=tmp_path, now=NOW) == cli.EXIT_OK
    loaded = probe.read_plan(tmp_path)
    assert loaded["status"] == probe.PLAN_ABANDONED
    assert loaded["cells"][0]["status"] == probe.CELL_EMPTY


def test_abandon_still_refuses_while_the_position_is_genuinely_on_the_book(tmp_path, monkeypatch):
    plan = _active_plan(tmp_path)
    plan = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_live")
    _seed_plan(plan, tmp_path)
    monkeypatch.setattr(cli, "load_open_live_position", lambda symbol, root=None: {"status": "OPEN"})
    monkeypatch.setattr(cli, "read_live_outcomes", lambda root=None: [])
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        cli.run_abandon(reason="r", root=tmp_path, now=NOW)
    assert exc.value.reason_code == probe.PROBE_CELL_OPEN
    assert probe.read_plan(tmp_path)["status"] == probe.PLAN_ACTIVE


def test_fire_refuses_below_the_execution_stage_a_real_order_needs(tmp_path, monkeypatch):
    """PR1b: a probe is a real mainnet order, so it is judged against the same rung an autonomous
    entry needs. A machine whose stage record is missing reads READ_ONLY and never reaches the
    venue."""
    _active_plan(tmp_path)
    _arm_runtime(tmp_path)
    adapter = _HappyPathAdapter()
    monkeypatch.setattr(cli.live_execution, "select_order_adapter", lambda now=None, root=None: adapter)
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_HIGH)
    _arm_limits(monkeypatch, stage_valid=False, stage_reason=es.STAGE_RECORD_MISSING)
    monkeypatch.setattr(cli, "read_account", lambda **k: (_snapshot(), {}))
    _stub_both(monkeypatch, "live_risk_snapshot", lambda **k: {
        "daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
        "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"})
    monkeypatch.setattr(cli, "bracket_breaker_status", lambda root=None: {
        "tripped": False, "consecutive": 0, "limit": 2})
    monkeypatch.setattr(cli, "resolve_risk_limits", lambda root, now=None: None)
    monkeypatch.setattr(cli, "_read_filters", lambda *a, **k: SymbolFilters(
        step_size=0.001, min_qty=0.001, min_notional=100.0, tick_size=0.1))
    monkeypatch.setattr(cli, "_read_price", lambda *a, **k: 100000.0)

    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_GUARD_REFUSED
    assert adapter.submitted == [], "the probe reached the venue below its stage"


# --- the pre-order gate on the probe (PR2b) --------------------------------------------------------

def _gate_facts(plan, **overrides):
    """Everything `gate_probe_order` re-derives from, as `--fire` holds it just before the send."""
    from runtime.mvp_runtime.crypto.live_position import live_capacity, reconcile_positions
    from runtime.mvp_runtime.crypto.live_order import build_live_order_intent

    limits = LiveOrderLimits(
        max_order_notional_usdt=150.0, max_daily_order_count=10, max_open_notional_usdt=300.0,
        daily_loss_limit_usdt=50.0, canary_confirmation=CANARY_CONFIRMATION_PHRASE,
    )
    stage = es.StageStatus(stage="LIVE_AUTONOMOUS", valid=True, reason_code=None,
                           recorded_stage="LIVE_AUTONOMOUS", **_STAGE_IDS)
    guard_kwargs = dict(gate_open=True, execution_stage=stage, runtime_active=True,
                        daily_loss_breached=False, submitted_today=0, current_open_notional_usdt=0.0,
                        limits=limits, budget_registered=True, allowed_symbols=["BTCUSDT"], canary=True)
    price, tick = 100000.0, 0.1
    stop = probe.probe_stop_price(price, tick, stop_bps=float(plan["params"]["stop_bps"]))
    intent = build_live_order_intent(
        {"direction": probe.PROBE_DIRECTION, "entry_price": price, "stop_loss": stop,
         "strategy_id": probe.probe_strategy_id(plan["batch_id"])},
        symbol="BTCUSDT", quantity=0.001, notional_usdt=100.0, now=NOW,
    )
    snapshot = types.SimpleNamespace(positions=[], realized_windows={}, available_balance=1000.0)
    facts = dict(
        plan=plan, cell_index=0, price=price, tick_size=tick, quantity=0.001, notional=100.0,
        account_readable=True, reconciliation=reconcile_positions([], snapshot, now=NOW),
        capacity=live_capacity([], symbol="BTCUSDT"),
        risk={"daily_loss_limit_breached": False, "daily_realized_pnl_usdt": 0.0,
              "daily_loss_limit_usdt": 50.0, "pnl_source": "venue"},
        breaker={"tripped": False, "consecutive": 0, "limit": 5},
        api_breaker={"tripped": False, "consecutive": 0, "limit": 5, "tripped_class": None},
        risk_verdict={"allow_new_position": True, "problems": []},
        guard_kwargs=guard_kwargs,
        profile=pre_order_gate_mod.approved_profile(
            purpose="probe", stage=stage, budget=_BUDGET, risk_limits={"source": "default"},
            authority={"kind": pre_order_gate_mod.AUTHORITY_PROBE_PLAN, "batch_id": plan["batch_id"],
                       "approval_id": plan["approval_id"], "cell_index": 0}),
        now=NOW,
        # PR2c-1: the account read at NOW, judged at NOW.
        account_collected_at=NOW, clock=NOW,
    )
    facts.update(overrides)
    return intent, facts


import runtime.mvp_runtime.crypto.pre_order_gate as pre_order_gate_mod  # noqa: E402


def test_the_probe_gate_approves_the_probe_the_facts_price(tmp_path):
    plan = _active_plan(tmp_path)
    intent, facts = _gate_facts(plan)
    snapshot = probe.gate_probe_order(intent, **facts)
    assert snapshot["approved"] is True, snapshot["failed_checks"]
    assert snapshot["purpose"] == "probe" and snapshot["lineage"]["batch_id"] == plan["batch_id"]
    names = {c["check"] for c in snapshot["checks"]}
    assert {"probe_plan_active", "probe_cell_open_for_this_order", "symbol_free",
            "venue_daily_loss_within_limit", "bracket_breaker_clear", "risk_guard_allows",
            "notional_within_plan_ceiling", "intent_matches_decision",
            "execution_stage_admits", "confirmation_phrase"} <= names


@pytest.mark.parametrize("overrides,check_id", [
    ({"risk": {"daily_loss_limit_breached": True}}, "venue_daily_loss_within_limit"),
    ({"breaker": {"tripped": True, "consecutive": 5, "limit": 5}}, "bracket_breaker_clear"),
    ({"api_breaker": {"tripped": True, "consecutive": 5, "limit": 5, "tripped_class": "write"}},
     "api_breaker_clear"),
    ({"api_breaker": {"consecutive": 0, "limit": 5}}, "api_breaker_clear"),          # says nothing
    ({"risk_verdict": {"allow_new_position": False, "problems": ["daily_loss_limit"]}}, "risk_guard_allows"),
    ({"notional": 150.0}, "notional_within_plan_ceiling"),
    ({"account_readable": False}, "account_readable"),
    ({"capacity": {"allowed": False, "blocks": ["LIVE_MAX_POSITIONS_PER_SYMBOL"]}}, "symbol_free"),
    ({"cell_index": 7}, "probe_cell_open_for_this_order"),
    ({"cell_index": 99}, "probe_cell_open_for_this_order"),
    ({"price": 90000.0}, "intent_matches_decision"),
], ids=["loss", "breaker", "api-breaker", "api-breaker-unknown", "risk-guard", "ceiling", "account",
        "symbol", "other-cell", "no-cell", "repriced"])
def test_the_probe_gate_re_derives_every_refusal(tmp_path, overrides, check_id):
    plan = _active_plan(tmp_path)
    intent, facts = _gate_facts(plan, **overrides)
    snapshot = probe.gate_probe_order(intent, **facts)
    assert snapshot["approved"] is False
    assert check_id in snapshot["failed_checks"]


@pytest.mark.parametrize("collected_at,fresh", [
    (-60, True),           # read exactly a minute before the gate
    (-61, False),          # a second past it
    (1, False),            # read after the gate: the clock went back
    (None, False),         # an account that cannot say when it was read
], ids=["a-minute", "past-a-minute", "after", "unknown"])
def test_the_probe_gate_judges_an_account_at_most_a_minute_old(tmp_path, collected_at, fresh):
    """PR2c-1: between the account read and the gate a probe makes venue calls, each with its own
    timeout; the caps and the loss breaker must not be judged on an account older than a minute."""
    plan = _active_plan(tmp_path)
    read_at = None if collected_at is None else timeutil.plus_seconds(NOW, collected_at)
    intent, facts = _gate_facts(plan, account_collected_at=read_at)
    snapshot = probe.gate_probe_order(intent, **facts)
    assert snapshot["approved"] is fresh, snapshot["failed_checks"]
    assert ("account_fresh" in snapshot["failed_checks"]) is not fresh
    [judged] = [c for c in snapshot["checks"] if c["check"] == "account_fresh"]
    assert judged["detail"]["collected_at"] == read_at and judged["detail"]["max_age_seconds"] == 60


def test_the_probe_gate_seals_when_it_judged(tmp_path):
    plan = _active_plan(tmp_path)
    judged_at = timeutil.plus_seconds(NOW, 5)
    intent, facts = _gate_facts(plan, clock=judged_at)
    snapshot = probe.gate_probe_order(intent, **facts)
    assert snapshot["approved"] is True, snapshot["failed_checks"]
    assert (snapshot["facts"]["decided_at"], snapshot["facts"]["account_collected_at"]) == (judged_at, NOW)
    assert snapshot["created_at"] == NOW


def test_fire_refuses_an_account_read_over_a_minute_before_its_gate(tmp_path, monkeypatch):
    """The fire judges its gate at the wall clock, not at its own start."""
    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    old = types.SimpleNamespace(positions=[], realized_windows={}, available_balance=1000.0,
                                collected_at=timeutil.plus_seconds(timeutil.utc_now_iso(), -120))
    monkeypatch.setattr(cli, "read_account", lambda **k: (old, {}))
    counter = _FakeCounter(adapter=adapter)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PRE_ORDER_GATE_REFUSED
    assert "account_fresh" in str(exc.value)
    assert adapter.submitted == [] and counter.count == 0
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


def test_fire_refuses_an_account_with_no_read_time(tmp_path, monkeypatch):
    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    timeless = types.SimpleNamespace(positions=[], realized_windows={}, available_balance=1000.0)
    monkeypatch.setattr(cli, "read_account", lambda **k: (timeless, {}))
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: _FakeCounter(adapter=adapter))
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PRE_ORDER_GATE_REFUSED
    assert "account_fresh" in str(exc.value) and adapter.submitted == []


def test_the_probe_gate_refuses_a_plan_that_is_no_longer_active(tmp_path):
    plan = {**_active_plan(tmp_path), "status": probe.PLAN_ABANDONED}
    intent, facts = _gate_facts(plan)
    assert "probe_plan_active" in probe.gate_probe_order(intent, **facts)["failed_checks"]


def test_the_probe_gate_refuses_a_plan_with_no_approval(tmp_path):
    plan = {**_active_plan(tmp_path), "approval_id": None}
    intent, facts = _gate_facts(plan)
    assert probe.gate_probe_order(intent, **facts)["failed_checks"] == ["approved_profile_complete"]


def test_fire_records_the_probes_snapshot_before_the_send(tmp_path, monkeypatch):
    """The whole door: the snapshot is recorded on the mainnet store before the entry leaves, and
    the booked probe names it."""
    from tests._helpers import FakeSnapshotStore

    _active_plan(tmp_path)
    _arm_runtime(tmp_path)
    adapter = _HappyPathAdapter()
    events: list[str] = []

    class _Store(FakeSnapshotStore):
        def append(self, snapshot):
            events.append(f"record:{len(adapter.submitted)}")
            return super().append(snapshot)

    store = _Store()
    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store", lambda now=None, root=None: store)
    positions, ledger = _FakeStore(), _FakeLedger()
    saved: list[dict] = []
    original_save = positions.save_position

    def _save(position):
        saved.append(dict(position))
        original_save(position)

    positions.save_position = _save
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    monkeypatch.setattr(cli, "select_live_position_store", lambda now=None, root=None: positions)
    monkeypatch.setattr(cli, "select_live_ledger", lambda now=None, root=None: ledger)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: _FakeCounter())
    monkeypatch.setattr(cli, "select_live_bracket_breaker", lambda now=None, root=None: _FakeBreaker())
    monkeypatch.setattr(cli, "load_open_live_position",
                        lambda symbol, root=None: positions.positions.get(symbol))

    assert _fire(tmp_path) == cli.EXIT_OK
    [recorded] = store.appended
    assert events[0] == "record:0", "the snapshot was recorded after the order left"
    assert recorded["purpose"] == "probe" and recorded["approved"] is True
    assert adapter.submitted[0]["newClientOrderId"] == recorded["client_order_id"]
    assert saved[0]["risk_snapshot_sha256"] == recorded["risk_snapshot_sha256"]
    assert ledger.outcomes[0]["risk_snapshot_sha256"] == recorded["risk_snapshot_sha256"]


def test_fire_refuses_before_the_venue_when_the_gate_refuses(tmp_path, monkeypatch):
    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    # A plan the gate cannot trace to an approval (as a hand-edited plan store would read).
    real_read = cli.probe.read_plan
    monkeypatch.setattr(cli.probe, "read_plan",
                        lambda root=None: {**real_read(root), "approval_id": None})
    counter = _FakeCounter(adapter=adapter)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PRE_ORDER_GATE_REFUSED
    assert "approved_profile_complete" in str(exc.value)
    assert adapter.submitted == [] and counter.count == 0
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


def test_fire_sends_nothing_when_the_snapshot_cannot_be_recorded(tmp_path, monkeypatch):
    from runtime.mvp_runtime.errors import PersistenceError
    from tests._helpers import FakeSnapshotStore

    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store",
                        lambda now=None, root=None: FakeSnapshotStore(
                            error=PersistenceError("PRE_ORDER_SNAPSHOTS_LOCKED", "scripted")))
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: _FakeCounter())
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_SNAPSHOT_NOT_RECORDED
    assert "PRE_ORDER_SNAPSHOTS_LOCKED" in str(exc.value)
    assert adapter.submitted == []
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


# --- PR2b-2: the probe takes the symbol before it spends anything ---------------------------------

class _RecordingMarks:
    def __init__(self, events, *, symbol_error=None):
        self.events = events
        self.taken: list[dict] = []
        self.given_back: list[dict] = []
        self._symbol_error = symbol_error

    def claim_symbol(self, **kw):
        self.events.append("take")
        if self._symbol_error:
            raise ToolError(self._symbol_error, "scripted symbol claim failure")
        self.taken.append(dict(kw))
        return {}

    def release_symbol(self, **kw):
        self.events.append("give")
        self.given_back.append(dict(kw))
        return {}


def _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events, *, snapshot_store=None):
    positions, ledger = _FakeStore(), _FakeLedger()
    counter = _FakeCounter(adapter=adapter)
    reserve, save = counter.reserve_submission, positions.save_position

    def _reserve(**kw):
        events.append("reserve")
        return reserve(**kw)

    def _save(position):
        events.append("book")
        save(position)

    counter.reserve_submission, positions.save_position = _reserve, _save
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store",
                        lambda now=None, root=None: snapshot_store or FakeSnapshotStore())
    monkeypatch.setattr(cli, "select_live_entry_marks", lambda now=None, root=None: marks)
    monkeypatch.setattr(cli, "select_live_position_store", lambda now=None, root=None: positions)
    monkeypatch.setattr(cli, "select_live_ledger", lambda now=None, root=None: _FakeLedger())
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    monkeypatch.setattr(cli, "select_live_bracket_breaker", lambda now=None, root=None: _FakeBreaker())
    monkeypatch.setattr(cli, "load_open_live_position",
                        lambda symbol, root=None: positions.positions.get(symbol))
    return counter


def test_fire_takes_the_symbol_first_and_gives_it_back_once_the_probe_is_booked(tmp_path, monkeypatch):
    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    assert _fire(tmp_path) == cli.EXIT_OK
    [taken] = marks.taken
    assert taken["door"] == "probe" and taken["symbol"] == "BTCUSDT"
    assert taken["client_order_id"] == adapter.submitted[0]["newClientOrderId"]
    assert marks.given_back == [{"symbol": "BTCUSDT", "client_order_id": taken["client_order_id"]}]
    assert events[:2] == ["take", "reserve"] and events.index("book") < events.index("give")


@pytest.mark.parametrize("code", ["LIVE_ENTRY_SYMBOL_IN_FLIGHT", "LIVE_ENTRY_SYMBOL_OCCUPIED",
                                  "LIVE_ENTRY_MARKS_UNREADABLE"])
def test_fire_refuses_a_symbol_it_cannot_take_and_spends_nothing(tmp_path, monkeypatch, code):
    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events, symbol_error=code)
    counter = _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_SYMBOL_NOT_CLAIMED and code in str(exc.value)
    assert adapter.submitted == [] and counter.count == 0 and marks.given_back == []
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


class _RebindRefused(FakeSnapshotStore):
    def append(self, snapshot):
        if self.appended:
            from runtime.mvp_runtime.errors import PersistenceError

            raise PersistenceError("PRE_ORDER_SNAPSHOTS_LOCKED", "scripted re-bind failure")
        return super().append(snapshot)


@pytest.mark.parametrize("case", ["day-full", "snapshot-not-recorded", "refused-at-the-venue-door"])
def test_fire_gives_the_symbol_back_when_it_is_refused_before_the_venue(tmp_path, monkeypatch, case):
    from runtime.mvp_runtime.errors import PersistenceError

    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events)
    store = {
        "day-full": None,
        "snapshot-not-recorded": FakeSnapshotStore(error=PersistenceError("PRE_ORDER_SNAPSHOTS_LOCKED", "x")),
        "refused-at-the-venue-door": _RebindRefused(),
    }[case]
    counter = _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events, snapshot_store=store)
    if case == "day-full":
        counter.count = 10
    with pytest.raises(cli._Refusal):
        _fire(tmp_path)
    assert adapter.submitted == []
    assert len(marks.given_back) == 1 and marks.given_back[0]["symbol"] == "BTCUSDT"


def test_fire_keeps_the_symbol_when_the_entry_did_not_confirm(tmp_path, monkeypatch):
    class _Unconfirmed(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            return None                     # the venue does not answer for the entry

    events: list[str] = []
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, _Unconfirmed(), marks, events)
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    assert marks.taken and marks.given_back == []


@pytest.mark.parametrize("close_confirms", [True, False])
def test_fire_gives_the_symbol_back_only_if_the_naked_close_confirmed(tmp_path, monkeypatch, close_confirms):
    class _StopRefused(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                return None                 # the stop never rests
            if "_CLOSE_" in client_order_id:
                if not close_confirms:
                    raise ToolError("VENUE_TIMEOUT", "scripted close read failure")
                return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 12,
                        "executedQty": "0.001", "avgPrice": "99990.0", "cumQuote": "99.99",
                        "reduceOnly": True}
            return super().fetch_order(symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo)

    events: list[str] = []
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, _StopRefused(), marks, events)
    # The naked close is a runtime close: its guard needs the autonomous phrase as well.
    _stub_both(monkeypatch, "resolve_live_order_limits", lambda root, now=None: (
        LiveOrderLimits(max_order_notional_usdt=150.0, max_daily_order_count=10,
                        max_open_notional_usdt=300.0, daily_loss_limit_usdt=50.0,
                        canary_confirmation=CANARY_CONFIRMATION_PHRASE,
                        confirmation=LIVE_CONFIRMATION_PHRASE),
        dict(_BUDGET, symbol_allowlist=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    ))
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    assert (len(marks.given_back) == 1) is close_confirms


def _both_phrases(monkeypatch):
    """A naked close is a runtime close: its guard needs the autonomous phrase as well."""
    _stub_both(monkeypatch, "resolve_live_order_limits", lambda root, now=None: (
        LiveOrderLimits(max_order_notional_usdt=150.0, max_daily_order_count=10,
                        max_open_notional_usdt=300.0, daily_loss_limit_usdt=50.0,
                        canary_confirmation=CANARY_CONFIRMATION_PHRASE,
                        confirmation=LIVE_CONFIRMATION_PHRASE),
        dict(_BUDGET, symbol_allowlist=["BTCUSDT", "ETHUSDT", "SOLUSDT"]),
    ))


@pytest.mark.parametrize("close_confirms", [True, False])
def test_fire_keeps_the_symbol_after_a_partial_fill_even_once_its_close_confirmed(
        tmp_path, monkeypatch, close_confirms):
    """A partial fill is not a terminal state: the rest of the order may still fill after the
    reported part was closed, so the symbol stays claimed until it expires (PR2b-2 review)."""
    class _PartialFill(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_CLOSE_" in client_order_id:
                if not close_confirms:
                    raise ToolError("VENUE_TIMEOUT", "scripted close read failure")
                return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 12,
                        "executedQty": "0.0005", "avgPrice": "99990.0", "cumQuote": "49.995",
                        "reduceOnly": True}
            return {"symbol": symbol, "side": "BUY", "status": "PARTIALLY_FILLED", "orderId": 11,
                    "executedQty": "0.0005", "avgPrice": "100000.0", "cumQuote": "50.0",
                    "reduceOnly": False}

    events: list[str] = []
    marks = _RecordingMarks(events)
    adapter = _PartialFill()
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    _both_phrases(monkeypatch)
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    assert any(r.get("reduceOnly") for r in adapter.submitted) is True    # the close was sent
    assert marks.taken and marks.given_back == []


def test_fire_says_so_when_the_symbol_cannot_be_given_back(tmp_path, monkeypatch, capsys):
    from runtime.mvp_runtime.errors import PersistenceError

    class _ReleaseFails(_RecordingMarks):
        def release_symbol(self, **kw):
            raise PersistenceError("LIVE_ENTRY_MARKS_LOCKED", "scripted release failure")

    events: list[str] = []
    _wire_claimed_fire(tmp_path, monkeypatch, _HappyPathAdapter(), _ReleaseFails(events), events)
    assert _fire(tmp_path) == cli.EXIT_OK
    err = capsys.readouterr().err
    assert "CLAIM     : NOT released (LIVE_ENTRY_MARKS_LOCKED)" in err and "30 min" in err


# --- PR2b-2 review -------------------------------------------------------------------------------

@pytest.mark.parametrize("timeout", [0, -1, 61, True])
def test_fire_refuses_a_call_timeout_that_could_outlive_the_claim(tmp_path, timeout):
    with pytest.raises(cli._Refusal) as exc:
        cli.run_fire(root=tmp_path, symbol="BTCUSDT", timeout_seconds=timeout, poll_seconds=0.0,
                     sleep=lambda s: None)
    assert exc.value.reason_code == probe.PROBE_CALL_TIMEOUT_REFUSED


def test_fire_accepts_a_sixty_second_call_timeout(tmp_path, monkeypatch):
    """The bound the review asked for: about ten calls at 60 s stay well inside the 30-minute claim."""
    events: list[str] = []
    _wire_claimed_fire(tmp_path, monkeypatch, _HappyPathAdapter(), _RecordingMarks(events), events)
    assert cli.run_fire(root=tmp_path, symbol="BTCUSDT", timeout_seconds=60,
                        poll_seconds=0.0, sleep=lambda s: None) == cli.EXIT_OK


@pytest.mark.parametrize("breaks", ["cell-write", "ledger-selector"])
def test_fire_gives_the_symbol_back_whatever_fails_before_the_send(tmp_path, monkeypatch, breaks):
    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    if breaks == "cell-write":
        real_write = cli.probe.write_plan

        def write_plan(plan, root=None, **kw):
            if any(c.get("status") == probe.CELL_OPEN for c in plan["cells"]):
                raise OSError(28, "No space left on device")
            return real_write(plan, root, **kw)

        monkeypatch.setattr(cli.probe, "write_plan", write_plan)
    else:
        monkeypatch.setattr(cli, "select_live_ledger",
                            lambda now=None, root=None: (_ for _ in ()).throw(RuntimeError("no ledger")))
    with pytest.raises((OSError, RuntimeError)):
        _fire(tmp_path)
    assert adapter.submitted == []
    assert len(marks.given_back) == 1


@pytest.mark.parametrize("submit_error,given_back", [("ORDER_REJECTED", True), ("ORDER_TRANSPORT", False)])
def test_fire_gives_the_symbol_back_after_a_plain_rejection_only(tmp_path, monkeypatch, submit_error,
                                                                  given_back):
    class _Refused(_HappyPathAdapter):
        def submit(self, order_request, *, timeout_seconds=10):
            self.submitted.append(order_request)
            raise ToolError(submit_error, "scripted submit failure")

        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            return None

    events: list[str] = []
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, _Refused(), marks, events)
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    assert (len(marks.given_back) == 1) is given_back


def test_fire_says_so_when_it_outlived_its_claim(tmp_path, monkeypatch, capsys):
    from runtime.mvp_runtime.crypto.live_order import LIVE_ENTRY_CLAIM_LOST

    class _Lost(_RecordingMarks):
        def release_symbol(self, **kw):
            raise ToolError(LIVE_ENTRY_CLAIM_LOST, "scripted: another order holds the symbol")

    events: list[str] = []
    _wire_claimed_fire(tmp_path, monkeypatch, _HappyPathAdapter(), _Lost(events), events)
    assert _fire(tmp_path) == cli.EXIT_OK
    assert "INCIDENT: the probe outlived its claim on BTCUSDT" in capsys.readouterr().err


@pytest.mark.parametrize("path", ["seen-at-the-poll", "seen-after-the-stop-read", "seen-at-the-timeout"])
def test_fire_never_settles_or_closes_a_position_that_is_not_the_probes(tmp_path, monkeypatch, path):
    events: list[str] = []
    positions_box = {}

    class _Adapter(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            answer = super().fetch_order(symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo)
            if path == "seen-after-the-stop-read" and "_SL_" in client_order_id and self.stop_reads == 2:
                _replace(positions_box["store"])     # the cycle settled it while this read was out
            return answer

    adapter = _Adapter()
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    _both_phrases(monkeypatch)       # so a time-close WOULD be sent, were the probe to try one
    positions = positions_box["store"] = cli.select_live_position_store()
    if path != "seen-after-the-stop-read":
        save = positions.save_position

        def _save(position):
            save(position)
            _replace(positions)

        positions.save_position = _save
    ticks = {"seen-at-the-poll": [0.0, 0.0], "seen-after-the-stop-read": [0.0, 0.0],
             "seen-at-the-timeout": [0.0, 1e12]}[path]
    clock = iter(ticks + [1e12] * 10)
    code = cli.run_fire(root=tmp_path, symbol="BTCUSDT", poll_seconds=0.0, sleep=lambda s: None,
                        clock=lambda: next(clock))
    assert code == cli.EXIT_BLOCKED          # nothing in the ledger says how the probe ended
    assert [r for r in adapter.submitted if r.get("reduceOnly")] == []
    assert positions.positions["BTCUSDT"]["position_id"] == "pos_autonomous"
    assert positions.positions["BTCUSDT"]["quantity"] == 0.002
    # Seen at the poll, before the venue is asked anything more about the probe's stop.
    assert adapter.stop_reads == (2 if path == "seen-after-the-stop-read" else 1)


def _replace(positions):
    """The cycle settled the probe and an autonomous entry booked its own position on the symbol —
    the interleaving the review found."""
    held = positions.positions["BTCUSDT"]
    positions.positions["BTCUSDT"] = {**held, "position_id": "pos_autonomous",
                                      "strategy_id": "S_AUTONOMOUS", "quantity": 0.002}


# --- PR2c-0: a probe's close that leaves a leg resting says so, and keeps the symbol ---------------

def test_fire_keeps_the_symbol_and_says_so_when_the_stop_it_could_not_rest_will_not_come_off(
        tmp_path, monkeypatch, capsys):
    class _StopRefusedCancelFails(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                return None
            if "_CLOSE_" in client_order_id:
                return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 12,
                        "executedQty": "0.001", "avgPrice": "99990.0", "cumQuote": "99.99",
                        "reduceOnly": True}
            return super().fetch_order(symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo)

        def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            raise ToolError("ORDER_TRANSPORT", "scripted cancel failure")

    events: list[str] = []
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, _StopRefusedCancelFails(), marks, events)
    _both_phrases(monkeypatch)
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    assert "ORPHAN    : protective orders may still rest at the venue for BTCUSDT" in capsys.readouterr().err
    assert marks.taken and marks.given_back == []


def test_fire_says_so_when_its_time_close_leaves_the_stop_resting(tmp_path, monkeypatch, capsys):
    class _CancelFails(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                self.stop_reads += 1
                return {"symbol": symbol, "status": "NEW", "orderId": 77}      # rests, never fills
            if "_CLOSE_" in client_order_id:
                return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 12,
                        "executedQty": "0.001", "avgPrice": "99990.0", "cumQuote": "99.99",
                        "reduceOnly": True}
            return super().fetch_order(symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo)

        def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            raise ToolError("ORDER_TRANSPORT", "scripted cancel failure")

    events: list[str] = []
    _wire_claimed_fire(tmp_path, monkeypatch, _CancelFails(), _RecordingMarks(events), events)
    _both_phrases(monkeypatch)
    clock = iter([0.0, 1e12] + [1e12] * 10)          # straight to the timeout
    cli.run_fire(root=tmp_path, symbol="BTCUSDT", poll_seconds=0.0, sleep=lambda s: None,
                 clock=lambda: next(clock))
    assert "ORPHAN    : protective orders may still rest at the venue for BTCUSDT" in capsys.readouterr().err


# --- PR2c-0 review ---------------------------------------------------------------------------------

def test_fire_says_so_when_the_settle_after_a_stop_fill_leaves_a_leg(tmp_path, monkeypatch, capsys):
    """The stop filled and the settle ran; its cancel of whatever else rests failed."""
    class _FillsThenCancelFails(_HappyPathAdapter):
        def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            raise ToolError("ORDER_TRANSPORT", "scripted cancel failure")

    events: list[str] = []
    _wire_claimed_fire(tmp_path, monkeypatch, _FillsThenCancelFails(), _RecordingMarks(events), events)
    monkeypatch.setattr(cli, "select_account_feed", lambda now=None, root=None: None)
    cli.run_fire(root=tmp_path, symbol="BTCUSDT", poll_seconds=0.0, sleep=lambda s: None)
    err = capsys.readouterr().err
    assert "ORPHAN    : protective orders may still rest at the venue for BTCUSDT" in err


def test_fire_names_the_stop_that_may_still_rest_when_its_naked_close_did_not_confirm(
        tmp_path, monkeypatch, capsys):
    class _StopRefusedCloseUnread(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                return None
            if "_CLOSE_" in client_order_id:
                raise ToolError("VENUE_TIMEOUT", "scripted close read failure")
            return super().fetch_order(symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo)

    events: list[str] = []
    marks = _RecordingMarks(events)
    adapter = _StopRefusedCloseUnread()
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    _both_phrases(monkeypatch)
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    err = capsys.readouterr().err
    stop_id = next(r["clientAlgoId"] for r in adapter.submitted if "clientAlgoId" in r)
    assert "the close did not confirm" in err and f"The stop {stop_id} may still rest" in err
    assert marks.given_back == []


def test_fire_says_a_confirmed_but_unpriced_time_close_withdrew_its_stop(tmp_path, monkeypatch, capsys):
    class _UnpricedClose(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                self.stop_reads += 1
                return {"symbol": symbol, "status": "NEW", "orderId": 77}
            if "_CLOSE_" in client_order_id:
                return {"symbol": symbol, "side": "SELL", "status": "FILLED", "orderId": 12,
                        "executedQty": "0.001", "avgPrice": None, "cumQuote": None, "reduceOnly": True}
            return super().fetch_order(symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo)

        def cancel_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            self.cancelled.append(client_order_id)
            return None

    events: list[str] = []
    adapter = _UnpricedClose()
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, _RecordingMarks(events), events)
    _both_phrases(monkeypatch)
    clock = iter([0.0, 1e12] + [1e12] * 10)
    assert cli.run_fire(root=tmp_path, symbol="BTCUSDT", poll_seconds=0.0, sleep=lambda s: None,
                        clock=lambda: next(clock)) == cli.EXIT_BLOCKED
    err = capsys.readouterr().err
    assert "the close confirmed but could not be priced" in err and "its stop was withdrawn" in err
    assert adapter.cancelled


class _PastTheGate(Exception):
    """Raised by the first step after an approving gate, so a test can stop the fire there."""


def _fire_to_the_gate_two_minutes_in(tmp_path, monkeypatch, *, account_read_at):
    """The fire starts at T0 and every later wall-clock read says T0 + 120 s. Returns T0."""
    start = timeutil.utc_now_iso()
    later = timeutil.plus_seconds(start, 120)
    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    read_at = timeutil.plus_seconds(start, account_read_at)
    account = types.SimpleNamespace(positions=[], realized_windows={}, available_balance=1000.0,
                                    collected_at=read_at)
    monkeypatch.setattr(cli, "read_account", lambda **k: (account, {}))

    def _past_the_gate(now=None, root=None):
        raise _PastTheGate()

    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store", _past_the_gate)
    calls = []

    def _wall():
        calls.append(None)
        return start if len(calls) == 1 else later

    monkeypatch.setattr(timeutil, "utc_now_iso", _wall)
    return adapter


def test_fire_judges_its_gate_at_the_wall_clock_not_at_its_start(tmp_path, monkeypatch):
    """An account read as the fire started is two minutes old when its gate runs (PR2c-1)."""
    adapter = _fire_to_the_gate_two_minutes_in(tmp_path, monkeypatch, account_read_at=0)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PRE_ORDER_GATE_REFUSED
    assert "account_fresh" in str(exc.value) and adapter.submitted == []


def test_fire_judges_the_account_it_read_not_its_own_start(tmp_path, monkeypatch):
    """Read ten seconds before the gate, the account is fresh however long ago the fire began."""
    _fire_to_the_gate_two_minutes_in(tmp_path, monkeypatch, account_read_at=110)
    with pytest.raises(_PastTheGate):
        _fire(tmp_path)


# --- the plan store is compare-and-set (PR2c-2a) --------------------------------------------------

def test_a_plan_write_names_the_plan_it_replaces(tmp_path):
    plan = _active_plan(tmp_path)
    moved = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_1")
    written = probe.write_plan(moved, tmp_path, expected_sha256=plan["record_sha256"])
    assert probe.read_plan(tmp_path) == written
    # A second writer still holding the first copy is refused; the store keeps the first write.
    stale = probe.mark_cell(plan, 1, status=probe.CELL_OPEN, now=NOW, position_id="pos_2")
    with pytest.raises(ToolError) as exc:
        probe.write_plan(stale, tmp_path, expected_sha256=plan["record_sha256"])
    assert exc.value.reason_code == probe.PROBE_PLAN_CHANGED
    assert probe.read_plan(tmp_path) == written


def test_a_writer_that_read_no_plan_expects_none(tmp_path):
    plan = _active_plan(tmp_path)
    fresh = probe.build_plan(_params(), approval_id="approval_2", now=NOW)
    with pytest.raises(ToolError) as exc:
        probe.write_plan(fresh, tmp_path, expected_sha256=None)
    assert exc.value.reason_code == probe.PROBE_PLAN_CHANGED
    with pytest.raises(ToolError) as missing:
        probe.write_plan(plan, tmp_path / "elsewhere", expected_sha256=plan["record_sha256"])
    assert missing.value.reason_code == probe.PROBE_PLAN_CHANGED
    assert probe.read_plan(tmp_path / "elsewhere") is None


@pytest.mark.parametrize("stored,code", [
    ("{", probe.PROBE_PLAN_UNREADABLE),
    ('{"status": "ACTIVE"}', probe.PROBE_PLAN_TAMPERED),
])
def test_a_store_that_cannot_say_what_it_holds_refuses_the_write(tmp_path, stored, code):
    plan = _active_plan(tmp_path)
    probe.plan_path(tmp_path).write_text(stored, encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        probe.write_plan(plan, tmp_path, expected_sha256=plan["record_sha256"])
    assert exc.value.reason_code == code
    assert probe.plan_path(tmp_path).read_text(encoding="utf-8") == stored


def test_confirm_replaces_only_the_finished_plan_it_read(tmp_path):
    first = _active_plan(tmp_path)
    for index in range(len(first["cells"])):
        first = probe.mark_cell(first, index, status=probe.CELL_OPEN, now=NOW)
        first = probe.mark_cell(first, index, status=probe.CELL_TIMEOUT, now=NOW, outcome_id=f"out_{index}")
    _seed_plan(first, tmp_path)
    second = probe.build_batch_params(symbols=("BNBUSDT", "DOGEUSDT"))
    real_read = probe.read_plan

    def _rewritten_after_the_read(root=None):
        # Another door rewrites the finished plan right after confirm has read it.
        held = real_read(root)
        probe.write_plan({**held, "updated_at": "2026-09-17T00:00:01Z"}, root,
                         expected_sha256=held["record_sha256"])
        return held

    import unittest.mock as mock
    with mock.patch.object(probe, "read_plan", _rewritten_after_the_read), pytest.raises(ToolError) as exc:
        probe.confirm_probe_batch(_fake_approval(second), params=second, root=tmp_path, now=NOW)
    assert exc.value.reason_code == probe.PROBE_PLAN_CHANGED
    assert real_read(tmp_path)["updated_at"] == "2026-09-17T00:00:01Z"


def test_an_abandon_that_raced_a_claim_is_refused(tmp_path):
    """The mirror image: a fire claimed a cell between the abandon's read and its write."""
    plan = _active_plan(tmp_path)
    real_read = probe.read_plan

    def _claimed_after_the_read(root=None):
        held = real_read(root)
        claimed = probe.mark_cell(held, 0, status=probe.CELL_OPEN, now=NOW, position_id="pos_1")
        probe.write_plan(claimed, root, expected_sha256=held["record_sha256"])
        return held

    import unittest.mock as mock
    with mock.patch.object(probe, "read_plan", _claimed_after_the_read), pytest.raises(ToolError) as exc:
        probe.abandon_plan(reason="operator retired the batch", now=NOW, root=tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_CHANGED
    stored = probe.read_plan(tmp_path)
    assert stored["status"] == probe.PLAN_ACTIVE and stored["cells"][0]["status"] == probe.CELL_OPEN


def test_an_abandon_beside_a_fire_is_not_undone(tmp_path, monkeypatch):
    """The fire read the plan ACTIVE; the operator abandoned it before the fire claimed its cell. The
    claim must not put the ACTIVE plan back, nothing is sent, and the symbol goes back."""
    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    governance = cli.live_governance.prepare_live_order_governance

    def _abandoned_meanwhile(intent, **kw):
        probe.abandon_plan(reason="operator retired the batch", now=NOW, root=tmp_path)
        return governance(intent, **kw)

    monkeypatch.setattr(cli.live_governance, "prepare_live_order_governance", _abandoned_meanwhile)
    with pytest.raises(ToolError) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PLAN_CHANGED
    assert probe.read_plan(tmp_path)["status"] == probe.PLAN_ABANDONED
    assert adapter.submitted == []
    assert len(marks.given_back) == 1


# --- the probe's gate re-reads what another writer can move (PR2c-2a) ----------------------------

def _refused_at_the_gate(tmp_path, adapter, counter):
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PRE_ORDER_GATE_REFUSED
    assert adapter.submitted == [] and counter.count == 0
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])
    return str(exc.value)


def _wired_with_counter(tmp_path, monkeypatch):
    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    counter = _FakeCounter(adapter=adapter)
    monkeypatch.setattr(cli, "select_live_order_counter", lambda now=None, root=None: counter)
    return adapter, counter


def test_fire_s_gate_sees_a_soft_halt_that_came_after_its_first_read(tmp_path, monkeypatch):
    from runtime.mvp_runtime.control import ACTIVE, ControlState, ControlStore

    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)

    def _halted_meanwhile(*a, **k):
        ControlStore(tmp_path).save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW,
                                                 reason="halt", trading_armed=False))
        return 100000.0

    monkeypatch.setattr(cli, "_read_price", _halted_meanwhile)
    assert "runtime_active" in _refused_at_the_gate(tmp_path, adapter, counter)


def test_fire_s_gate_sees_a_budget_re_registered_without_its_symbol(tmp_path, monkeypatch):
    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    limits, budget = cli.resolve_live_order_limits(tmp_path, now=NOW)
    monkeypatch.setattr(live_route, "resolve_live_order_limits",
                        lambda root, now=None: (limits, {**budget, "symbol_allowlist": ["ETHUSDT"]}))
    assert "symbol_allowlisted" in _refused_at_the_gate(tmp_path, adapter, counter)


def test_fire_s_gate_sees_risk_limits_re_registered_after_its_verdict(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import guards

    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    registered = guards.RiskLimits(**{**guards.DEFAULT_RISK_LIMITS.__dict__, "source": "registered",
                                      "limits_id": "limits_new", "record_sha256": "sha256:" + "9" * 64})
    monkeypatch.setattr(live_route, "resolve_risk_limits", lambda root=None, *, now: registered)
    assert "risk_guard_allows" in _refused_at_the_gate(tmp_path, adapter, counter)


def test_fire_s_gate_sees_a_bracket_breaker_another_door_tripped(tmp_path, monkeypatch):
    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    monkeypatch.setattr(live_route, "bracket_breaker_status",
                        lambda root=None: {"tripped": True, "consecutive": 5, "limit": 5})
    assert "bracket_breaker_clear" in _refused_at_the_gate(tmp_path, adapter, counter)


def test_fire_s_gate_judges_a_legacy_budget_window_at_its_own_clock(tmp_path, monkeypatch):
    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    limits, budget = cli.resolve_live_order_limits(tmp_path, now=NOW)
    start = timeutil.utc_now_iso()

    def _expires_by_the_gate(root, now=None):
        inside = str(now) <= start
        return limits, {**budget, "valid": inside, "valid_from": "2020-01-01T00:00:00Z", "valid_until": start}

    monkeypatch.setattr(live_route, "resolve_live_order_limits", _expires_by_the_gate)
    real_now = timeutil.utc_now_iso
    calls = []

    def _wall():
        calls.append(None)
        return start if len(calls) == 1 else timeutil.plus_seconds(start, 30)

    monkeypatch.setattr(timeutil, "utc_now_iso", _wall)
    refused = _refused_at_the_gate(tmp_path, adapter, counter)
    assert "budget_registered" in refused and "approved_profile_complete" in refused
    monkeypatch.setattr(timeutil, "utc_now_iso", real_now)


def test_fire_s_re_read_leaves_the_pool_alone(tmp_path, monkeypatch):
    """No pool entry authorizes a probe, so a pool that cannot be read does not stop one."""
    from runtime.mvp_runtime.crypto import pool as pool_store

    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    pool_store.pool_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    pool_store.pool_path(tmp_path).write_text("{", encoding="utf-8")

    def _past_the_gate(now=None, root=None):
        raise _PastTheGate()

    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store", _past_the_gate)
    with pytest.raises(_PastTheGate):
        _fire(tmp_path)


def test_fire_s_gate_records_the_higher_breaker_streak(tmp_path, monkeypatch):
    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    monkeypatch.setattr(live_route, "bracket_breaker_status",
                        lambda root=None: {"tripped": False, "consecutive": 3, "limit": 5})
    sealed = []
    real_gate = probe.gate_probe_order

    def _recording(intent, **kw):
        sealed.append(real_gate(intent, **kw))
        return sealed[-1]

    monkeypatch.setattr(probe, "gate_probe_order", _recording)

    def _past_the_gate(now=None, root=None):
        raise _PastTheGate()

    monkeypatch.setattr(cli.live_execution, "select_pre_order_snapshot_store", _past_the_gate)
    with pytest.raises(_PastTheGate):
        _fire(tmp_path)
    [breaker] = [c for c in sealed[0]["checks"] if c["check"] == "bracket_breaker_clear"]
    assert breaker["ok"] is True and breaker["detail"]["consecutive"] == 3


def test_fire_refuses_when_its_gate_cannot_read_again(tmp_path, monkeypatch):
    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)

    def _unreadable(root=None):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(live_route, "bracket_breaker_status", _unreadable)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_REREAD_FAILED
    assert "OSError" in str(exc.value)
    assert adapter.submitted == [] and counter.count == 0


# --- review of #886: after the send, a plan write never ends the fire ---------------------------

def _store_changes_after_the_entry(adapter_cls, root):
    """An adapter whose entry submit is followed by another door rewriting the plan store."""
    class _Changed(adapter_cls):
        rewritten = False

        def submit(self, order_request, *, timeout_seconds=10):
            out = super().submit(order_request, timeout_seconds=timeout_seconds)
            if "clientAlgoId" not in order_request and not self.rewritten:
                type(self).rewritten = True
                held = probe.read_plan(root)
                probe.write_plan({**held, "updated_at": "2026-09-17T00:00:09Z"}, root,
                                 expected_sha256=held["record_sha256"])
            return out

    return _Changed()


def test_a_plan_changed_under_a_fire_does_not_end_its_supervision(tmp_path, monkeypatch, capsys):
    """The stop still rests, the fire still waits for it and settles it; only the exit says the plan
    does not know."""
    events: list[str] = []
    adapter = _store_changes_after_the_entry(_HappyPathAdapter, tmp_path)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, _RecordingMarks(events), events)
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    captured = capsys.readouterr()
    assert "FILLED    : stop filled" in captured.out
    assert captured.err.count("PLAN      : NOT recorded") == 2        # the booking, then FILLED
    assert f"BLOCKED {probe.PROBE_PLAN_NOT_RECORDED}" in captured.err
    assert adapter.stop_reads >= 2


def test_a_refused_stop_still_raises_its_incident_when_the_plan_cannot_be_written(
        tmp_path, monkeypatch, capsys):
    class _StopRefusedCloseUnread(_HappyPathAdapter):
        def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10, algo=False):
            if "_SL_" in client_order_id:
                return None
            if "_CLOSE_" in client_order_id:
                raise ToolError("VENUE_TIMEOUT", "scripted close read failure")
            return super().fetch_order(symbol, client_order_id, timeout_seconds=timeout_seconds, algo=algo)

    events: list[str] = []
    adapter = _store_changes_after_the_entry(_StopRefusedCloseUnread, tmp_path)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, _RecordingMarks(events), events)
    _both_phrases(monkeypatch)
    assert _fire(tmp_path) == cli.EXIT_BLOCKED
    err = capsys.readouterr().err
    assert "INCIDENT: the probe stop was refused AND the close did not confirm" in err
    assert "PLAN      : NOT recorded" in err and f"BLOCKED {probe.PROBE_PLAN_NOT_RECORDED}" in err


def test_a_refusal_before_the_venue_gives_the_symbol_back_even_if_the_cell_cannot_be_returned(
        tmp_path, monkeypatch):
    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)

    def _refused(intent, **kw):
        held = probe.read_plan(tmp_path)
        probe.write_plan({**held, "updated_at": "2026-09-17T00:00:09Z"}, tmp_path,
                         expected_sha256=held["record_sha256"])
        raise cli.live_execution.SubmitRefused("RISK_SNAPSHOT_STALE", "scripted")

    monkeypatch.setattr(cli.live_execution, "submit_and_reconcile", _refused)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == "RISK_SNAPSHOT_STALE"
    assert f"the cell could not be returned ({probe.PROBE_PLAN_CHANGED})" in str(exc.value)
    assert len(marks.given_back) == 1 and adapter.submitted == []


# --- review of #886: a cell whose fire is still sending is not finished --------------------------

def _cell_in_flight(tmp_path, *, claimed_by="TAI_BTCUSDT_LONG_inflight"):
    from runtime.mvp_runtime.crypto.live_order import LiveEntryMarks
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
    from tests._helpers import make_gate_authorization

    plan = _active_plan(tmp_path)
    cell = plan["cells"][0]
    opened = probe.mark_cell(plan, 0, status=probe.CELL_OPEN, now=NOW, opened_at=NOW,
                             entry_client_order_id="TAI_BTCUSDT_LONG_inflight")
    _seed_plan(opened, tmp_path)
    auth = make_gate_authorization(flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID)
    LiveEntryMarks(root=tmp_path, authorization=auth).claim_symbol(
        door="probe", now=NOW, symbol=cell["symbol"], client_order_id=claimed_by, notional_usdt=100.0,
        exposure={"open_notional_usdt": 0.0, "position_ids": [], "cap_usdt": 300.0})
    return opened


def _nothing_booked(monkeypatch):
    monkeypatch.setattr(cli, "load_open_live_position", lambda symbol, root=None: None)
    monkeypatch.setattr(cli, "read_live_outcomes", lambda root=None: [])


def test_an_abandon_leaves_a_cell_whose_fire_is_still_sending(tmp_path, monkeypatch):
    opened = _cell_in_flight(tmp_path)
    _nothing_booked(monkeypatch)
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        cli.run_abandon(reason="r", root=tmp_path, now=NOW)
    assert exc.value.reason_code == probe.PROBE_CELL_OPEN
    assert probe.read_plan(tmp_path) == opened


def test_a_fire_leaves_a_cell_another_fire_is_still_sending(tmp_path, monkeypatch):
    opened = _cell_in_flight(tmp_path)
    _nothing_booked(monkeypatch)
    monkeypatch.setattr(cli.live_execution, "select_order_adapter",
                        lambda now=None, root=None: _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "_read_regime", lambda *a, **k: probe.REGIME_LOW)
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_CELL_OPEN
    assert probe.read_plan(tmp_path) == opened


@pytest.mark.parametrize("case", ["expired", "another-entry"])
def test_a_cell_whose_claim_is_gone_or_another_s_is_resolved_as_before(tmp_path, monkeypatch, case):
    _cell_in_flight(tmp_path, claimed_by="TAI_BTCUSDT_LONG_other" if case == "another-entry"
                    else "TAI_BTCUSDT_LONG_inflight")
    _nothing_booked(monkeypatch)
    later = timeutil.plus_minutes(timeutil.utc_now_iso(), 31) if case == "expired" else NOW
    assert cli.run_abandon(reason="r", root=tmp_path, now=later) == cli.EXIT_OK
    loaded = probe.read_plan(tmp_path)
    assert loaded["status"] == probe.PLAN_ABANDONED and loaded["cells"][0]["status"] == probe.CELL_EMPTY


def test_unreadable_marks_cannot_show_a_cell_is_finished(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_order import ENTRY_MARKS_FILENAME
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    opened = _cell_in_flight(tmp_path)
    (venue_state_dir(tmp_path) / ENTRY_MARKS_FILENAME).write_text("{", encoding="utf-8")
    _nothing_booked(monkeypatch)
    with pytest.raises((cli._Refusal, MvpRuntimeError)) as exc:
        cli.run_abandon(reason="r", root=tmp_path, now=NOW)
    assert exc.value.reason_code == probe.PROBE_CELL_OPEN
    assert probe.read_plan(tmp_path) == opened


# --- review of #886: the loss limit and the slot on the re-read ----------------------------------

def test_fire_s_gate_judges_today_s_loss_against_a_limit_lowered_since(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto.live_pnl import live_risk_snapshot as real_risk

    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    lost = types.SimpleNamespace(positions=[], available_balance=1000.0, collected_at=timeutil.utc_now_iso(),
                                 realized_windows={"today": {"net": -10.0}, "1d": {"net": -10.0}})
    monkeypatch.setattr(cli, "read_account", lambda **k: (lost, {}))
    monkeypatch.setattr(live_route, "live_risk_snapshot", real_risk)
    limits, budget = cli.resolve_live_order_limits(tmp_path, now=NOW)
    lowered = LiveOrderLimits(**{**limits.__dict__, "daily_loss_limit_usdt": 5.0})
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: (lowered, budget))
    refused = _refused_at_the_gate(tmp_path, adapter, counter)
    assert "venue_daily_loss_within_limit" in refused and "daily_loss_within_limit" in refused


@pytest.mark.parametrize("fresh_cap,reserved", [(3, 3), (20, 10)], ids=["lowered", "raised"])
def test_fire_reserves_its_slot_against_the_stricter_cap(tmp_path, monkeypatch, fresh_cap, reserved):
    adapter, counter = _wired_with_counter(tmp_path, monkeypatch)
    limits, budget = cli.resolve_live_order_limits(tmp_path, now=NOW)
    assert limits.max_daily_order_count == 10
    changed = LiveOrderLimits(**{**limits.__dict__, "max_daily_order_count": fresh_cap})
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: (changed, budget))
    monkeypatch.setattr(cli, "select_live_entry_marks",
                        lambda now=None, root=None: _RecordingMarks([]))

    def _past_the_slot(*a, **k):
        raise _PastTheGate()

    # The first step after the reservation fails: the slot has been judged, nothing is sent.
    monkeypatch.setattr(cli.pre_order_gate, "verify_and_persist", _past_the_slot)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_SNAPSHOT_NOT_RECORDED
    assert counter.limits == [reserved] and adapter.submitted == []


# --- PR2c-3: what rests at the venue, and what the claim adds ------------------------------------

@pytest.mark.parametrize("rests", ["plain", "algo", "unreadable"])
def test_fire_refuses_while_orders_rest_on_the_symbol_and_spends_nothing(tmp_path, monkeypatch, rests):
    """Decision 25: a stop another probe left behind would close this probe's position."""
    events: list[str] = []
    adapter = _HappyPathAdapter()
    if rests == "unreadable":
        adapter.resting_error = ToolError("VENUE_TIMEOUT", "scripted")
    else:
        setattr(adapter, f"resting_{rests}", [{"clientOrderId": f"TAI_BTCUSDT_SL_{rests}", "symbol": "BTCUSDT"}])
    marks = _RecordingMarks(events)
    counter = _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_RESTING_ORDERS
    if rests != "unreadable":
        assert f"TAI_BTCUSDT_SL_{rests}" in str(exc.value)
    assert adapter.submitted == [] and adapter.cancelled == [] and counter.count == 0
    assert events == ["take", "give"]
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


def test_fire_tells_the_claim_its_notional_and_the_exposure_it_was_judged_against(tmp_path, monkeypatch):
    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    lowered = LiveOrderLimits(**{**cli.resolve_live_order_limits(tmp_path, now=NOW)[0].__dict__,
                                 "max_open_notional_usdt": 150.0})
    budget = cli.resolve_live_order_limits(tmp_path, now=NOW)[1]
    monkeypatch.setattr(live_route, "resolve_live_order_limits", lambda root, now=None: (lowered, budget))
    assert _fire(tmp_path) == cli.EXIT_OK
    [taken] = marks.taken
    assert taken["notional_usdt"] == pytest.approx(100.0)
    # The venue account the fire read, and the cap the gate judged (the re-read lowered it).
    assert taken["exposure"] == {"open_notional_usdt": 0.0, "position_ids": [], "cap_usdt": 150.0}


def test_fire_tells_the_claim_the_open_exposure_its_guard_judged(tmp_path, monkeypatch):
    """Not a flat account: the figure the claim starts from is the one the guard was given."""
    events: list[str] = []
    adapter = _HappyPathAdapter()
    marks = _RecordingMarks(events)
    _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    judged = []
    real_guard = cli.evaluate_live_order_guard

    def _recording_guard(intent, **kw):
        judged.append(kw["current_open_notional_usdt"])
        return real_guard(intent, **kw)

    monkeypatch.setattr(cli, "compute_open_notional_usdt", lambda snapshot, at_cap: 42.5)
    monkeypatch.setattr(cli, "evaluate_live_order_guard", _recording_guard)
    assert _fire(tmp_path) == cli.EXIT_OK
    [taken] = marks.taken
    assert judged and set(judged) == {42.5}
    assert taken["exposure"]["open_notional_usdt"] == 42.5


def test_fire_refuses_a_decision_that_aged_out_during_the_resting_reads_and_spends_no_slot(tmp_path, monkeypatch):
    """Review of #888: a probe call may wait a minute; two of them outlast the decision's bound."""
    from runtime.mvp_runtime.crypto import pre_order_gate

    events: list[str] = []

    class _Slow(_HappyPathAdapter):
        def algo_open_orders(self, symbol=None, *, timeout_seconds=10):
            monkeypatch.setattr(pre_order_gate, "_send_clock",
                                lambda: timeutil.plus_seconds(timeutil.utc_now_iso(), 3600))
            return []

    adapter = _Slow()
    marks = _RecordingMarks(events)
    counter = _wire_claimed_fire(tmp_path, monkeypatch, adapter, marks, events)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == pre_order_gate.RISK_SNAPSHOT_STALE
    assert adapter.submitted == [] and counter.count == 0 and events == ["take", "give"]


# === the API error breaker (PR2d-1) ===============================================

def _tripped_api(root=None):
    return {"tripped": True, "tripped_class": "write", "tripped_at": NOW, "consecutive": 5, "limit": 5}


def _durable_api_breaker(tmp_path):
    from runtime.mvp_runtime.crypto.live_order import LiveApiErrorBreaker
    from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
    from tests._helpers import make_gate_authorization

    return LiveApiErrorBreaker(root=tmp_path, authorization=make_gate_authorization(
        flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID))


def test_fire_refuses_while_the_api_breaker_is_tripped(tmp_path, monkeypatch):
    _wire_fire_to_the_guard(tmp_path, monkeypatch, _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "api_breaker_status", _tripped_api)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_API_BREAKER
    assert "scripts/clear_api_breaker.py" in str(exc.value)
    assert all(c["status"] == probe.CELL_EMPTY for c in probe.read_plan(tmp_path)["cells"])


def test_a_breaker_that_latches_before_the_gate_refuses_the_probe(tmp_path, monkeypatch):
    """The probe reads the breaker, then the facts again before its gate — through the live
    leg's own re-read. A latch in between is refused by the gate and nothing is sent."""
    adapter = _HappyPathAdapter()
    _wire_fire_to_the_guard(tmp_path, monkeypatch, adapter)
    monkeypatch.setattr(live_route, "api_breaker_status", _tripped_api)
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_PRE_ORDER_GATE_REFUSED
    assert "api_breaker_clear" in str(exc.value)
    assert adapter.submitted == []


def test_a_fire_whose_call_latches_the_breaker_says_so_and_tells_the_operator(tmp_path, monkeypatch, capsys):
    """The probe is the other door with signed calls. When one of them latches the breaker, the
    operator hears it once, as from the live leg — the next cycle reads it as already tripped."""
    from runtime.mvp_runtime import operator as operator_mod
    from runtime.mvp_runtime.crypto.live_order import MAX_CONSECUTIVE_API_ERRORS

    _wire_fire_to_the_guard(tmp_path, monkeypatch, _VenueMustNotBeTouched())
    breaker = _durable_api_breaker(tmp_path)
    for _ in range(MAX_CONSECUTIVE_API_ERRORS - 1):
        breaker.record_failure(call_class="read", call="open_orders", at=NOW, reason_code="TOOL_RATE_LIMITED")
    monkeypatch.setattr(cli, "select_live_api_breaker", lambda now=None, root=None: breaker)
    monkeypatch.setattr(cli, "read_account", lambda **k: (None, {
        "degraded": True, "degraded_reason_code": "ACCOUNT_DATA_DEGRADED",
        "error_reason_code": "TOOL_TRANSPORT"}))
    told: list[str] = []
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda now=None, root=None: "chat")
    monkeypatch.setattr(operator_mod, "notify_operator",
                        lambda channel, text, repo_root=None: told.append(text))
    with pytest.raises(cli._Refusal) as exc:
        _fire(tmp_path)
    assert exc.value.reason_code == probe.PROBE_ACCOUNT_UNREADABLE
    err = capsys.readouterr().err
    assert "API BREAKER TRIPPED" in err and "class    : read" in err
    [text] = told
    assert "last     : read_account TOOL_TRANSPORT" in text


def test_a_trip_the_chat_cannot_hear_is_still_said_here(tmp_path, monkeypatch, capsys):
    from runtime.mvp_runtime import operator as operator_mod

    def _unregistered(channel, text, repo_root=None):
        raise ToolError("OPERATOR_NOT_REGISTERED", "nobody to tell")

    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda now=None, root=None: "chat")
    monkeypatch.setattr(operator_mod, "notify_operator", _unregistered)
    cli._report_api_trip({"tripped_class": "write", "limit": 5, "tripped_at": NOW,
                          "write": {"consecutive": 5, "last_call": "submit",
                                    "last_reason_code": "ORDER_TRANSPORT", "last_venue_code": None}},
                         root=tmp_path)
    err = capsys.readouterr().err
    assert "API BREAKER TRIPPED" in err and "last     : submit ORDER_TRANSPORT" in err
    assert "the operator chat was not told (OPERATOR_NOT_REGISTERED)" in err


def test_a_breaker_write_that_fails_is_said_on_the_spot(tmp_path, monkeypatch, capsys):
    class _Locked:
        def record_success(self, **kw):
            raise ToolError("LIVE_API_BREAKER_LOCKED", "held")

        def record_failure(self, **kw):
            raise ToolError("LIVE_API_BREAKER_LOCKED", "held")

    _wire_fire_to_the_guard(tmp_path, monkeypatch, _VenueMustNotBeTouched())
    monkeypatch.setattr(cli, "select_live_api_breaker", lambda now=None, root=None: _Locked())
    monkeypatch.setattr(cli, "api_breaker_status", _tripped_api)
    with pytest.raises(cli._Refusal):
        _fire(tmp_path)
    assert "API BREAKER: a signed call was not recorded (LIVE_API_BREAKER_LOCKED)" in capsys.readouterr().err


def test_the_fire_s_settlement_counts_its_fill_history_read(tmp_path, monkeypatch):
    """The fill history a settlement falls back to is a signed read of the fire too: the probe
    hands its settlement the feed recorded into the same breaker as its adapter."""
    from runtime.mvp_runtime.crypto.live_order import api_breaker_status

    class _Feed:
        network_egress = True

        def fill_history(self, symbol, *, start_ms, timeout_seconds):
            raise ToolError("TOOL_TRANSPORT", "scripted: the account did not answer")

    events: list[str] = []
    _wire_claimed_fire(tmp_path, monkeypatch, _HappyPathAdapter(), _RecordingMarks(events), events)
    breaker = _durable_api_breaker(tmp_path)
    monkeypatch.setattr(cli, "select_live_api_breaker", lambda now=None, root=None: breaker)
    monkeypatch.setattr(cli, "select_account_feed", lambda now=None, root=None: _Feed())
    real_settle = cli.live_leg.settle_venue_closed_position

    def _settle(position, *, account_feed, **kw):
        with pytest.raises(ToolError):
            account_feed.fill_history(position["symbol"], start_ms=0, timeout_seconds=1)
        return real_settle(position, account_feed=None, **kw)

    monkeypatch.setattr(cli.live_leg, "settle_venue_closed_position", _settle)
    assert _fire(tmp_path) == cli.EXIT_OK
    # Counted once; the settlement's own leg reads after it answered, and ended the streak.
    read = api_breaker_status(tmp_path)["read"]
    assert (read["total"], read["last_call"], read["last_reason_code"]) == (
        1, "fill_history", "TOOL_TRANSPORT")
    assert read["consecutive"] == 0
