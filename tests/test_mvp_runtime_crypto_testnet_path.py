"""The signed testnet execution path (PR1d-1; Thomas decisions 2 and 11, 2026-09-16).

What is pinned here: the testnet adapter cannot reach the live venue, the live adapter cannot
reach the testnet one, neither can sign with the other's key, a testnet authorization opens
nothing live, the testnet guard needs the SIGNED_TESTNET rung and refuses below it, and a cycle
counts as evidence only when the venue's own answers say the whole cycle happened — entry
reconciled, protective legs RESTING at the algo endpoint, withdrawn, exit reconciled, position
view clean."""

from __future__ import annotations

import inspect
import json

import pytest

from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.crypto import live_execution, testnet_evidence, testnet_execution
from runtime.mvp_runtime.crypto.state import VENUE_MAINNET, VENUE_TESTNET
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-09-16T00:00:00Z"


def _testnet_auth():
    return safety_gate.env_only_authorization(
        flags=testnet_execution.TESTNET_TRADING_FLAGS,
        provider_id=testnet_execution.TESTNET_PROVIDER_ID,
        env_var=testnet_execution.TESTNET_TRADING_ENV,
        opt_in_value=testnet_execution.REAL_TESTNET_TRADING,
    )


def _stage(stage="SIGNED_TESTNET", valid=True, reason=None):
    return es.StageStatus(stage=stage, valid=valid, reason_code=reason,
                          recorded_stage=stage if valid else None)


def _intent(**overrides):
    return {"status": "ORDER_INTENT_CREATED", "symbol": "BTCUSDT", "direction": "LONG",
            "quantity": 0.002, "order_notional_usdt": 50.0, "reduce_only": False,
            "connectivity_test": False, **overrides}


def _facts(**overrides):
    return {"gate_open": True, "runtime_active": True, "manual_kill_switch": False,
            "submitted_today": 0, "execution_stage": _stage(), **overrides}


# --- the two adapters cannot reach each other's venue ---------------------------------

def test_neither_adapter_can_be_pointed_at_the_other_venue():
    """The live allowlist was never widened (2026-07-25 holds), and the testnet one refuses the
    live host — the direction that matters here, because a testnet door aimed at mainnet would
    spend real money."""
    with pytest.raises(ToolError) as testnet_refuses:
        testnet_execution.BinanceTestnetOrderAdapter(base_url=live_execution.ORDER_BASE_URL)
    assert testnet_refuses.value.reason_code == testnet_execution.TESTNET_HOST_NOT_ALLOWED
    with pytest.raises(ToolError) as live_refuses:
        live_execution.BinanceFuturesOrderAdapter(base_url=testnet_execution.TESTNET_BASE_URL)
    assert live_refuses.value.reason_code == "ORDER_HOST_NOT_ALLOWED"
    assert live_execution.ALLOWED_ORDER_HOSTS & testnet_execution.ALLOWED_TESTNET_HOSTS == frozenset()


def test_the_testnet_adapter_signs_with_its_own_credentials_only(monkeypatch):
    """The sharpest leak this module exists to prevent: the live adapter reads its key from module
    constants, so inheriting its signing method would have signed testnet requests with the
    mainnet key. A missing credential is reported by NAME only, never by value."""
    monkeypatch.setenv(live_execution.ORDER_API_KEY_ENV, "live-key")
    monkeypatch.setenv(live_execution.ORDER_API_SECRET_ENV, "live-secret")
    monkeypatch.delenv(testnet_execution.TESTNET_API_KEY_ENV, raising=False)
    monkeypatch.delenv(testnet_execution.TESTNET_API_SECRET_ENV, raising=False)
    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)
    adapter = testnet_execution.select_testnet_order_adapter(now=NOW)
    assert isinstance(adapter, testnet_execution.BinanceTestnetOrderAdapter)
    with pytest.raises(ToolError) as exc:
        adapter.fetch_order("BTCUSDT", "cid")
    assert exc.value.reason_code == "NO_ORDER_API_KEY"
    message = str(exc.value)
    assert testnet_execution.TESTNET_API_KEY_ENV in message
    assert "live-key" not in message and "live-secret" not in message
    # The live method is not reused: the two read different env names.
    assert live_execution.ORDER_API_KEY_ENV != testnet_execution.TESTNET_API_KEY_ENV
    source = inspect.getsource(testnet_execution.BinanceTestnetOrderAdapter._signed_request)
    assert "TESTNET_API_KEY_ENV" in source and "ORDER_API_KEY_ENV," not in source


def test_the_switches_are_independent(monkeypatch):
    """`MVP_LIVE_TRADING` neither enables nor disables the testnet path, and the reverse."""
    from runtime.mvp_runtime.crypto import live_pnl

    monkeypatch.setenv(live_pnl.LIVE_TRADING_ENV, live_pnl.REAL_LIVE_TRADING)
    monkeypatch.delenv(testnet_execution.TESTNET_TRADING_ENV, raising=False)
    assert isinstance(testnet_execution.select_testnet_order_adapter(now=NOW),
                      testnet_execution.DryRunTestnetOrderAdapter)
    monkeypatch.delenv(live_pnl.LIVE_TRADING_ENV, raising=False)
    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)
    assert isinstance(testnet_execution.select_testnet_order_adapter(now=NOW),
                      testnet_execution.BinanceTestnetOrderAdapter)
    assert isinstance(live_execution.select_order_adapter(now=NOW), live_execution.DryRunOrderAdapter)


def test_a_testnet_authorization_opens_nothing_live(monkeypatch):
    """`assert_authorization` compares the provider id, so the ids being different IS the
    separation — a testnet grant handed to a live store refuses."""
    from runtime.mvp_runtime.crypto import live_pnl

    assert testnet_execution.TESTNET_PROVIDER_ID != live_pnl.LIVE_TRADING_PROVIDER_ID
    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)
    auth = safety_gate.env_only_authorization(
        flags=testnet_execution.TESTNET_TRADING_FLAGS,
        provider_id=testnet_execution.TESTNET_PROVIDER_ID,
        env_var=testnet_execution.TESTNET_TRADING_ENV,
        opt_in_value=testnet_execution.REAL_TESTNET_TRADING,
    )
    ledger = live_pnl.RealLiveLedger(root=None, authorization=auth)
    with pytest.raises(safety_gate.SafetyGateBlocked):
        ledger.append_outcome({"settlement_id": "s"})


# --- the guard -------------------------------------------------------------------------

def test_the_testnet_rung_is_what_a_testnet_order_needs():
    """The circularity PR1d-1 had to break: every purpose required LIVE_AUTONOMOUS, including the
    order that exists to earn it."""
    assert es.required_stage(es.PURPOSE_TESTNET) == "SIGNED_TESTNET"
    assert testnet_execution.evaluate_testnet_order_guard(_intent(), **_facts())["approved"] is True
    for stage in ("READ_ONLY", "SHADOW", "PAPER"):
        verdict = testnet_execution.evaluate_testnet_order_guard(
            _intent(), **_facts(execution_stage=_stage(stage)))
        assert verdict["approved"] is False
        assert any("execution stage" in block for block in verdict["blocks"]), stage
    # A record that does not bind admits nothing, whatever rung it claims.
    unbound = _stage("READ_ONLY", valid=False, reason=es.STAGE_APPROVAL_NOT_CONSUMED)
    refused = testnet_execution.evaluate_testnet_order_guard(_intent(), **_facts(execution_stage=unbound))
    assert refused["approved"] is False
    assert any(es.STAGE_APPROVAL_NOT_CONSUMED in block for block in refused["blocks"])
    # LIVE rungs are above it and still admit a testnet order.
    assert testnet_execution.evaluate_testnet_order_guard(
        _intent(), **_facts(execution_stage=_stage("LIVE_AUTONOMOUS")))["approved"] is True


@pytest.mark.parametrize("closed,block", [
    ({"gate_open": False}, "signed testnet trading is not enabled"),
    ({"manual_kill_switch": True}, "manual kill switch"),
    ({"runtime_active": False}, "runtime is not ACTIVE"),
    ({"submitted_today": testnet_execution.TESTNET_MAX_DAILY_ORDERS}, "daily order cap"),
])
def test_every_other_door_the_testnet_guard_keeps(closed, block):
    verdict = testnet_execution.evaluate_testnet_order_guard(_intent(), **_facts(**closed))
    assert verdict["approved"] is False
    assert any(block in b for b in verdict["blocks"]), verdict["blocks"]


def test_the_testnet_guard_bounds_the_order_and_refuses_a_connectivity_test():
    over = _intent(order_notional_usdt=testnet_execution.TESTNET_MAX_ORDER_NOTIONAL_USDT + 1)
    assert testnet_execution.evaluate_testnet_order_guard(over, **_facts())["approved"] is False
    probe = _intent(connectivity_test=True)
    assert testnet_execution.evaluate_testnet_order_guard(probe, **_facts())["approved"] is False


# --- the evidence registry -------------------------------------------------------------

def _leg(name="SL", **overrides):
    """One protective leg as the venue answered it. The stop is the conditional (algo) one; the
    target is a plain LIMIT and must not claim the algo endpoint."""
    return {"leg": name, "algo": name == "SL", "order_type": "STOP_MARKET" if name == "SL" else "LIMIT",
            "observed_status": "NEW", "withdrawn": True, **overrides}


def _legs(**overrides):
    return [_leg("SL", **overrides), _leg("TP", **overrides)]


def _cycle(**overrides):
    fields = {
        "cycle_id": "cyc_1", "symbol": "BTCUSDT",
        "entry": {"reconcile_status": "RECONCILED", "mismatches": []},
        "protective_legs": _legs(),
        "exit_result": {"reconcile_status": "RECONCILED", "reduce_only": True},
        "position_reconciliation": {"status": "RECONCILED"},
        "adapter_tool_id": testnet_execution.TESTNET_ADAPTER_TOOL_ID,
        "base_url_host": "testnet.binancefuture.com",
        "started_at": NOW, "completed_at": NOW,
    }
    fields.update(overrides)
    return testnet_evidence.build_cycle_record(**fields)


def test_a_complete_cycle_is_the_whole_cycle(tmp_path):
    testnet_evidence.append_cycle(_cycle(), tmp_path)
    assert [r["cycle_id"] for r in testnet_evidence.complete_cycles(tmp_path)] == ["cyc_1"]
    assert testnet_evidence.assert_complete_cycle("cyc_1", tmp_path)["symbol"] == "BTCUSDT"


@pytest.mark.parametrize("broken,expected", [
    ({"entry": {"reconcile_status": "NOT_FOUND", "mismatches": []}}, "entry did not reconcile"),
    ({"entry": {"reconcile_status": "RECONCILED", "mismatches": ["quantity"]}}, "mismatches"),
    ({"protective_legs": []}, "no SL leg was placed"),
    ({"protective_legs": [_leg("SL")]}, "no TP leg was placed"),
    ({"protective_legs": _legs(observed_status="REJECTED")}, "not confirmed resting"),
    ({"protective_legs": [_leg("SL", algo=False), _leg("TP")]}, "which is not where a"),
    ({"protective_legs": [_leg("SL"), _leg("TP", algo=True)]}, "which is not where a"),
    ({"protective_legs": _legs(withdrawn=False)}, "not withdrawn"),
    ({"venue": "binance_futures"}, "not from the testnet venue"),
    ({"failure": "ORDER_TRANSPORT"}, "stopped at ORDER_TRANSPORT"),
    ({"exit_result": {"reconcile_status": "UNRECONCILABLE", "reduce_only": True}}, "exit did not reconcile"),
    ({"exit_result": {"reconcile_status": "RECONCILED", "reduce_only": False}}, "not reduceOnly"),
    ({"position_reconciliation": {"status": "DRIFT"}}, "position view did not reconcile"),
])
def test_each_half_of_the_cycle_that_history_says_must_be_proven(tmp_path, broken, expected):
    """2026-08-02 (both protective legs refused, conditional orders had moved to the Algo API),
    08-03 (a stop accepted but unfindable, never withdrawn) and 08-05 (an algo fill the settle
    path could not read) all happened after the entry. An entry-only rehearsal reproduces none."""
    # A field the builder does not take is edited into the row and re-hashed, which is what a
    # writer of the state directory can do — the verdict must not depend on the builder.
    edits = {k: v for k, v in broken.items() if k in {"venue", "venue_host", "schema_version"}}
    record = _cycle(cycle_id="cyc_broken", **{k: v for k, v in broken.items() if k not in edits})
    if edits:
        from runtime.read_only_kernel import integrity

        body = {k: v for k, v in record.items() if k != "record_sha256"}
        body.update(edits)
        body["record_sha256"] = integrity.sha256_record(body)
        record = body
    findings = testnet_evidence.cycle_findings(record)
    assert any(expected in f for f in findings), findings
    testnet_evidence.append_cycle(record, tmp_path)
    assert testnet_evidence.complete_cycles(tmp_path) == []
    with pytest.raises(ToolError) as exc:
        testnet_evidence.assert_complete_cycle("cyc_broken", tmp_path)
    assert exc.value.reason_code == testnet_evidence.EVIDENCE_INCOMPLETE


def test_the_verdict_is_derived_not_stored(tmp_path):
    """The retired canary gate counted a stored flag on a frozen file (PR1r). A row that CLAIMS
    it is complete proves nothing: the finding comes from the venue's own answers."""
    record = _cycle(cycle_id="cyc_claim", protective_legs=_legs(observed_status="REJECTED"))
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    body["complete"] = True            # a claim nobody reads
    from runtime.read_only_kernel import integrity

    body["record_sha256"] = integrity.sha256_record(body)
    path = testnet_evidence.evidence_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body) + "\n", encoding="utf-8")
    assert testnet_evidence.complete_cycles(tmp_path) == []


def test_an_unverifiable_registry_refuses_rather_than_reading_short(tmp_path):
    path = testnet_evidence.evidence_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = _cycle()
    tampered = {**record, "symbol": "ETHUSDT"}          # hash left alone
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        testnet_evidence.read_cycles(tmp_path)
    assert exc.value.reason_code == testnet_evidence.EVIDENCE_TAMPERED
    path.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as dup:
        testnet_evidence.read_cycles(tmp_path)
    assert dup.value.reason_code == testnet_evidence.EVIDENCE_DUPLICATE
    assert testnet_evidence.read_cycles(tmp_path / "empty") == []


def test_the_evidence_lives_on_the_testnet_venue_only(tmp_path):
    from runtime.mvp_runtime.crypto.state import venue_state_dir

    testnet_evidence.append_cycle(_cycle(), tmp_path)
    assert testnet_evidence.evidence_path(tmp_path).is_relative_to(
        venue_state_dir(tmp_path, venue=VENUE_TESTNET))
    assert not testnet_evidence.evidence_path(tmp_path).is_relative_to(
        venue_state_dir(tmp_path, venue=VENUE_MAINNET) / "live_positions")


# --- the door ---------------------------------------------------------------------------

def test_the_door_refuses_before_the_venue_when_the_stage_is_too_low(tmp_path, monkeypatch):
    from scripts import run_signed_testnet_cycle as door

    sent: list = []

    class _Adapter(testnet_execution.DryRunTestnetOrderAdapter):
        network_egress = True

        def submit(self, order_request, *, timeout_seconds=10):
            sent.append(order_request)
            return super().submit(order_request, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter", lambda **kw: _Adapter())
    monkeypatch.setattr(door, "_price", lambda symbol, **kw: 50000.0)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: _stage("PAPER"))
    with pytest.raises(door._Refusal) as exc:
        door.run_cycle(symbol="BTCUSDT", quantity=0.001, operator="thomas", reason="r",
                       root=tmp_path, now=NOW)
    assert exc.value.reason_code == "TESTNET_GUARD_REFUSED"
    assert sent == [], "the door reached the venue below its rung"
    assert testnet_evidence.read_cycles(tmp_path) == []


def test_a_soft_halt_does_not_stop_the_rehearsal_but_a_kill_does(tmp_path, monkeypatch):
    """A testnet cycle needs the runtime running, not live trading ARMED: requiring the arm would
    mean arming real trading in order to earn the evidence for arming real trading. A kill or a
    pause still stops it — a machine its operator halted sends nothing anywhere."""
    from runtime.mvp_runtime.control import ACTIVE, KILLED, ControlState, ControlStore
    from scripts import run_signed_testnet_cycle as door

    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter",
                        lambda **kw: testnet_execution.DryRunTestnetOrderAdapter())
    monkeypatch.setattr(door, "_price", lambda symbol, **kw: 50000.0)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: _stage())
    store = ControlStore(tmp_path)
    store.save(ControlState(mode=ACTIVE, updated_by="op", updated_at=NOW, reason="soft halt",
                            trading_armed=False))
    planned = door.plan_cycle(symbol="BTCUSDT", quantity=0.001, root=tmp_path, now=NOW)
    assert not any("runtime is not ACTIVE" in b for b in planned["verdict"]["blocks"])
    store.save(ControlState(mode=KILLED, updated_by="op", updated_at=NOW, reason="kill",
                            trading_armed=False))
    killed = door.plan_cycle(symbol="BTCUSDT", quantity=0.001, root=tmp_path, now=NOW)
    assert any("runtime is not ACTIVE" in b for b in killed["verdict"]["blocks"])


def test_the_door_records_one_cycle_and_counts_it_on_the_testnet_venue(tmp_path, monkeypatch):
    from runtime.mvp_runtime.crypto import live_order
    from scripts import run_signed_testnet_cycle as door

    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)

    class _Adapter(testnet_execution.DryRunTestnetOrderAdapter):
        network_egress = True
        _authorization = _testnet_auth()

    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter", lambda **kw: _Adapter())
    monkeypatch.setattr(door, "_price", lambda symbol, **kw: 50000.0)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: _stage())
    out = door.run_cycle(symbol="BTCUSDT", quantity=0.001, operator="thomas",
                         reason="evidence", root=tmp_path, now=NOW)
    rows = testnet_evidence.read_cycles(tmp_path)
    assert len(rows) == 1 and rows[0]["venue"] == VENUE_TESTNET
    # The row a happy cycle leaves must actually BE evidence — the first draft recorded the exit's
    # own status as the position reconciliation and called every leg an algo order, so a row could
    # read complete having proven neither (review of #877).
    assert out["complete"] is True, out["findings"]
    assert testnet_evidence.cycle_findings(rows[0]) == []
    assert [leg["algo"] for leg in rows[0]["protective_legs"]] == [True, False]
    assert rows[0]["position_reconciliation"]["status"] == testnet_evidence.RECONCILED
    assert "venue_positions" in rows[0]["position_reconciliation"], "the venue was never asked"
    assert rows[0]["operator"] == "thomas" and rows[0]["failure"] is None
    # The orders counted against the TESTNET venue's counter, never the live one.
    assert live_order.count_today(tmp_path, venue=VENUE_TESTNET) >= 1
    assert live_order.count_today(tmp_path) == 0


def test_a_venue_that_answers_badly_cannot_produce_evidence(tmp_path, monkeypatch):
    """Each of the three live incidents, as the venue would answer them, must land in the row as a
    finding rather than being smoothed into a complete cycle."""
    from scripts import run_signed_testnet_cycle as door

    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)

    class _RefusesTheStop(testnet_execution.DryRunTestnetOrderAdapter):
        """2026-08-02: the conditional leg is refused (-4120 lived here)."""

        network_egress = True
        _authorization = _testnet_auth()

        def submit(self, order_request, *, timeout_seconds=10):
            if order_request.get("algoType"):
                raise ToolError("ORDER_REJECTED", "venue rejected the order (code -4120)")
            return super().submit(order_request, timeout_seconds=timeout_seconds)

    class _LeavesThePositionOpen(testnet_execution.DryRunTestnetOrderAdapter):
        """2026-08-05's shape: the exit reconciles but the venue still shows the position."""

        network_egress = True
        _authorization = _testnet_auth()

        def open_positions(self, symbol=None, *, timeout_seconds=10):
            return [{"symbol": symbol or "BTCUSDT", "positionAmt": "0.001"}]

    monkeypatch.setattr(door, "_price", lambda symbol, **kw: 50000.0)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: _stage())

    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter", lambda **kw: _RefusesTheStop())
    refused = door.run_cycle(symbol="BTCUSDT", quantity=0.001, operator="t", reason="r",
                             root=tmp_path, now=NOW)
    assert refused["complete"] is False
    assert any("SL" in f for f in refused["findings"]), refused["findings"]

    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter",
                        lambda **kw: _LeavesThePositionOpen())
    still_open = door.run_cycle(symbol="BTCUSDT", quantity=0.002, operator="t", reason="r",
                                root=tmp_path, now="2026-09-16T00:01:00Z")
    assert still_open["complete"] is False
    assert any("position view did not reconcile" in f for f in still_open["findings"])
    assert testnet_evidence.complete_cycles(tmp_path) == []


def test_a_cycle_interrupted_after_the_entry_is_still_recorded(tmp_path, monkeypatch):
    """The venue has been reached: what happened has to be written down, and the row is what says
    a position may still be open (review of #877)."""
    from scripts import run_signed_testnet_cycle as door

    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)

    class _DiesAfterTheEntry(testnet_execution.DryRunTestnetOrderAdapter):
        network_egress = True
        _authorization = _testnet_auth()

        def submit(self, order_request, *, timeout_seconds=10):
            if order_request.get("algoType"):
                raise safety_gate.SafetyGateBlocked("ENV_OPT_IN_WITHDRAWN", "the opt-in went away")
            return super().submit(order_request, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter", lambda **kw: _DiesAfterTheEntry())
    monkeypatch.setattr(door, "_price", lambda symbol, **kw: 50000.0)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: _stage())
    monkeypatch.setattr(door, "place_bracket_leg",
                        lambda intent, **kw: (_ for _ in ()).throw(
                            safety_gate.SafetyGateBlocked("ENV_OPT_IN_WITHDRAWN", "gone")))
    with pytest.raises(door._Refusal) as exc:
        door.run_cycle(symbol="BTCUSDT", quantity=0.001, operator="t", reason="r",
                       root=tmp_path, now=NOW)
    assert exc.value.reason_code == "TESTNET_CYCLE_INCOMPLETE"
    rows = testnet_evidence.read_cycles(tmp_path)
    assert len(rows) == 1 and rows[0]["failure"] == "ENV_OPT_IN_WITHDRAWN"
    assert rows[0]["entry"]["reconcile_status"] == testnet_evidence.RECONCILED
    assert testnet_evidence.complete_cycles(tmp_path) == []


def test_an_earlier_bad_row_does_not_stop_the_door_recording_this_one(tmp_path):
    """The verified read is strict on purpose; the WRITE must not inherit it, or a door that has
    already reached the venue loses the record of what it did (review of #877)."""
    path = testnet_evidence.evidence_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tampered = {**_cycle(cycle_id="cyc_old"), "symbol": "ETHUSDT"}   # hash left alone
    path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    testnet_evidence.append_cycle(_cycle(cycle_id="cyc_new"), tmp_path)
    assert "cyc_new" in path.read_text(encoding="utf-8")
    with pytest.raises(ToolError):
        testnet_evidence.read_cycles(tmp_path)



def test_the_entry_and_the_exit_are_two_orders_to_the_venue(tmp_path, monkeypatch):
    """Found by the PR2 investigation (2026-09-16): the entry and the exit hashed the same identity
    inputs into ONE client order id. The venue refuses a reused id (-4116), so the reconcile would
    read the ENTRY back as the exit and no cycle could ever complete — the LIVE climb's evidence
    path, broken at the source, and hidden because the inert adapter overwrote the entry."""
    from scripts import run_signed_testnet_cycle as door

    monkeypatch.setenv(testnet_execution.TESTNET_TRADING_ENV, testnet_execution.REAL_TESTNET_TRADING)
    sent: list[dict] = []

    class _Recording(testnet_execution.DryRunTestnetOrderAdapter):
        network_egress = True
        _authorization = _testnet_auth()

        def submit(self, order_request, *, timeout_seconds=10):
            sent.append(dict(order_request))
            return super().submit(order_request, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(door.testnet, "select_testnet_order_adapter", lambda **kw: _Recording())
    monkeypatch.setattr(door, "_price", lambda symbol, **kw: 50000.0)
    monkeypatch.setattr(door, "resolve_execution_stage", lambda root=None, **kw: _stage())
    out = door.run_cycle(symbol="BTCUSDT", quantity=0.001, operator="t", reason="r",
                         root=tmp_path, now=NOW)
    ids = [r.get("newClientOrderId") or r.get("clientAlgoId") for r in sent]
    assert len(ids) == len(set(ids)) == 4, ids          # entry, SL, TP, exit — four distinct orders
    market = [r for r in sent if r.get("type") == "MARKET"]
    assert [bool(r.get("reduceOnly")) for r in market] == [False, True]
    assert out["complete"] is True, out["findings"]
