"""The governance record a live order owes — closing the ``post_action_report_and_audit`` gap.

``p5_policy_gate`` in the canonical policy lists six requirements for
``FINANCIAL_APPROVED_TRADING_USE``. Five were enforced in code; the sixth was not, so a real
order left nothing on the hash chain while ``financial_transaction_execution_implemented`` was
already ``true``. These tests pin the two records that close it, and — more importantly — pin
that neither can be skipped.
"""

from __future__ import annotations

import re

import pytest

from runtime.mvp_runtime import audit as audit_module
from runtime.mvp_runtime.audit import AuditError
from runtime.mvp_runtime.crypto import live_governance as lg
from runtime.mvp_runtime.crypto.live_order import build_live_order_intent
from tests._helpers import requires_local_core

NOW = "2026-07-25T12:00:00Z"

INTENT = build_live_order_intent(
    {"direction": "LONG"}, symbol="BTCUSDT", quantity=0.001, notional_usdt=60.0, now=NOW,
)

RESULT = {
    "reconcile_status": "RECONCILED",
    "mismatches": [],
    "exchange_order_id": 12345,
    "client_order_id": INTENT["client_order_id"],
    "symbol": "BTCUSDT",
    "order_type": "MARKET",
    "reduce_only": False,
    "fill": {"avg_price": 60000.0, "executed_qty": 0.001, "cum_quote": 60.0},
    "created_at": NOW,
}

BOUND_TASK = {
    "identity": {"task_id": "task_1", "task_revision": 1, "trace_id": "trace_1"},
    "context": {"core_context_binding_id": "ccb_1", "data_sensitivity": "SENSITIVE"},
}
PERMDEC = {"permission_decision_id": "permdec_1"}
APPROVED = {"approved": True, "status": "READY"}
TIP_HASH = "sha256:" + "ab" * 32


# --- the fingerprint binds the decision to THIS order --------------------------

def test_the_fingerprint_is_the_venue_idempotency_key():
    """The governance record and the order the venue dedupes on cannot drift apart if they are
    derived from the same value."""
    fingerprint = lg.order_fingerprint(INTENT)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint), fingerprint
    # Derived FROM the venue's dedupe key: changing it changes the fingerprint.
    other = {**INTENT, "idempotency_key": INTENT["idempotency_key"] + "x"}
    assert lg.order_fingerprint(other) != fingerprint


def test_a_different_order_fingerprints_differently():
    other = build_live_order_intent(
        {"direction": "SHORT"}, symbol="ETHUSDT", quantity=0.01, notional_usdt=50.0, now=NOW,
    )
    assert lg.order_fingerprint(other) != lg.order_fingerprint(INTENT)


def test_an_unidentifiable_intent_refuses():
    with pytest.raises(ValueError):
        lg.order_fingerprint({"symbol": "BTCUSDT"})


# --- the audit event ------------------------------------------------------------

def _audit(result=None, **kw):
    return audit_module.build_live_order_audit(
        kw.pop("bound_task", BOUND_TASK),
        kw.pop("permission_decision", PERMDEC),
        result if result is not None else RESULT,
        guard_verdict=kw.pop("guard_verdict", APPROVED),
        purpose=kw.pop("purpose", lg.PURPOSE_CANARY),
        now=NOW,
        **kw,
    )


def test_a_live_order_produces_one_chained_audit_event():
    record, sha = _audit()
    assert isinstance(sha, str) and sha
    assert record["event_type"] == "OTHER"
    assert "LIVE_ORDER_SUBMITTED" in record["event"]["reason_codes"]
    assert "FINANCIAL_APPROVED_TRADING_USE" in record["event"]["reason_codes"]
    assert "EXECUTE_AND_REPORT" in record["event"]["reason_codes"]


def test_the_event_reports_what_the_VENUE_said_not_what_was_intended():
    record, _ = _audit()
    assert "RECONCILE_RECONCILED" in record["event"]["reason_codes"]
    assert "12345" in record["subject"]["subject_ref"] or "12345" in record["event"]["event_summary"]


@pytest.mark.parametrize("status", ["MISMATCH", "NOT_FOUND", "UNRECONCILABLE"])
def test_an_unconfirmed_order_is_FAILED_not_RECORDED(status):
    """Reporting an unconfirmed order as recorded is the one thing this event exists to
    prevent — those are exactly the cases someone will need the trail for."""
    record, _ = _audit({**RESULT, "reconcile_status": status})
    assert record["event"]["outcome"] == "FAILED"
    assert f"RECONCILE_{status}" in record["event"]["reason_codes"]


def test_a_reconciled_order_is_recorded():
    assert _audit()[0]["event"]["outcome"] == "RECORDED"


def test_the_purpose_distinguishes_a_canary_from_an_autonomous_order():
    """They are authorized by different confirmation phrases and the canary is exempt from the
    promotion gate, so the trail must say which capability was exercised."""
    canary, _ = _audit(purpose=lg.PURPOSE_CANARY)
    autonomous, _ = _audit(purpose=lg.PURPOSE_AUTONOMOUS)
    assert "PURPOSE_CANARY" in canary["event"]["reason_codes"]
    assert "PURPOSE_AUTONOMOUS" in autonomous["event"]["reason_codes"]


def test_the_event_carries_the_permission_decision_it_was_placed_under():
    record, _ = _audit()
    assert "in_memory:permdec_1" in record["event"]["related_record_refs"]


def test_the_event_never_carries_a_key_or_a_signature():
    """The credential posture: an audit record is a durable artifact."""
    record, _ = _audit()
    blob = str(record).lower()
    for forbidden in ("signature", "x-mbx-apikey", "api_key", "secret"):
        assert forbidden not in blob


def test_a_guard_that_did_not_approve_is_named_in_the_event():
    record, _ = _audit(guard_verdict={"approved": False})
    assert "GUARD_NOT_APPROVED" in record["event"]["reason_codes"]


def test_an_event_without_a_permission_decision_refuses():
    """The whole point of the record: an order with no P5 decision cannot be audited against
    the gate that required it."""
    with pytest.raises(AuditError) as excinfo:
        _audit(permission_decision={})
    assert excinfo.value.reason_code == "LIVE_ORDER_PERMDEC_MISSING"


@pytest.mark.parametrize("task", [{}, {"identity": {}}, {"context": {}}])
def test_an_unbound_task_refuses(task):
    with pytest.raises((AuditError, KeyError, TypeError)):
        _audit(bound_task=task)


def test_two_orders_get_distinct_audit_ids():
    a, _ = _audit()
    b, _ = _audit({**RESULT, "client_order_id": "TAI_BTCUSDT_LONG_other"})
    assert a["audit_event_id"] != b["audit_event_id"]


def test_the_event_chains_onto_a_prior_tip():
    record, _ = _audit(previous_hash=TIP_HASH, previous_audit_id="audit_prev")
    assert record["lineage"]["previous_event_sha256"] == TIP_HASH
    assert record["lineage"]["parent_audit_event_ids"] == ["audit_prev"]


# --- the preparation half -------------------------------------------------------

def test_an_unknown_purpose_refuses():
    with pytest.raises(ValueError):
        lg.prepare_live_order_governance(INTENT, purpose="whatever", now=NOW)


def test_preparation_fails_closed_without_an_active_core(monkeypatch):
    """A live order under no bound Core is exactly the exception this repository does not make.
    The binding call is what enforces it, so a machine with no active pointer refuses before the
    order rather than sending an unbindable one."""
    from runtime.mvp_runtime.errors import MvpRuntimeError

    def _no_core(task, **kw):
        raise MvpRuntimeError("CORE_NOT_ACTIVATED", "active Core pointer not found")

    monkeypatch.setattr(lg, "bind_task_to_core", _no_core)
    with pytest.raises(MvpRuntimeError) as excinfo:
        lg.prepare_live_order_governance(INTENT, purpose=lg.PURPOSE_CANARY, now=NOW)
    assert excinfo.value.reason_code == "CORE_NOT_ACTIVATED"


@requires_local_core
def test_preparation_builds_a_real_P5_decision_bound_to_this_order():
    governance = lg.prepare_live_order_governance(
        INTENT, purpose=lg.PURPOSE_CANARY, now=NOW,
    )
    record = governance["permission_decision"]
    assert record["decision"]["permission_decision"] == "EXECUTE_AND_REPORT"
    assert record["authority"]["required_permission_level"] == "P5"
    assert record["authority"]["authority_sufficient"] is True
    assert governance["order_fingerprint"] == lg.order_fingerprint(INTENT)
    # Bound to THIS order: the fingerprint is an INPUT to the action fingerprint, so a
    # different size or side yields a different decision rather than reusing this one.
    other = build_live_order_intent(
        {"direction": "LONG"}, symbol="BTCUSDT", quantity=0.002, notional_usdt=120.0, now=NOW,
    )
    other_decision = lg.prepare_live_order_governance(
        other, purpose=lg.PURPOSE_CANARY, now=NOW,
    )["permission_decision"]
    assert other_decision["action_fingerprint"] != record["action_fingerprint"]
    # The task is bound to the active Core: the record cannot exist without one.
    assert governance["bound_task"]["context"]["core_context_binding_id"]


@requires_local_core
def test_the_decision_is_review_only_planning_evidence():
    """Building it grants nothing and sends nothing."""
    decision = lg.prepare_live_order_governance(
        INTENT, purpose=lg.PURPOSE_CANARY, now=NOW,
    )["permission_decision"]
    runtime_flags = decision.get("runtime_effect") or {}
    assert runtime_flags
    assert all(value is False for value in runtime_flags.values() if isinstance(value, bool))


def test_the_report_helper_delegates_to_the_audit_builder():
    record, sha = lg.report_live_order(
        {"bound_task": BOUND_TASK, "permission_decision": PERMDEC, "purpose": lg.PURPOSE_AUTONOMOUS},
        RESULT, guard_verdict=APPROVED, now=NOW,
    )
    assert record["event_type"] == "OTHER" and sha
    assert "PURPOSE_AUTONOMOUS" in record["event"]["reason_codes"]
