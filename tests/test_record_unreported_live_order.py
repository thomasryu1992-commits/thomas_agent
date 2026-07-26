"""Recording, late, a live order that left nothing on the audit chain.

The situation this exists for happened on 2026-07-26: a canary FILLED at the venue, its registry
row was written, and the audit append then crashed — so `post_action_report_and_audit` was unmet
for money that had already moved.

What the tests pin down is the shape of the answer, because the wrong shapes are all plausible:
it must not claim to be the missing report, it must not invent the order's lost P5
PermissionDecision, it must cite the durable registry row as its evidence, it must append
FORWARD rather than pretending to fill the hole, and it must refuse to record one order twice.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.audit import AuditError, build_unreported_live_order_audit
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK
from runtime.mvp_runtime.crypto import live_governance
from runtime.mvp_runtime.store import LedgerStore
from scripts import record_unreported_live_order as rec

CANARY_ID = "canary_a30509450cea53a853b0"
CANARY_ROW = {
    "reconcile_status": "RECONCILED",
    "clean": True,
    "symbol": "BTCUSDT",
    "exchange_order_id": 1083867709068,
    "client_order_id": "TAI_BTCUSDT_LONG_58f36e18f7678054dc",
    "mismatches": [],
    "notional_usdt": 65.0,
    "recorded_at_utc": "2026-07-26T11:03:21Z",
    "stage": "live_canary",
    "provenance": "mvp_live_canary",
    "canary_order_id": CANARY_ID,
    "record_sha256": "sha256:b464a788fad2226c51e6c59a4f6c5a1f61d17dad903c2b2cb008ea34f671f157",
}
NOW = "2026-07-26T11:40:00Z"
REASON = "LedgerStore(None) crashed step 8 before the append"

_TASK = {
    "identity": {"task_id": "task_1", "task_revision": 1, "trace_id": "trace_1"},
    "context": {"core_context_binding_id": "ccb_1", "data_sensitivity": "SENSITIVE"},
}


# --- the builder: what the event may and may not claim ------------------------------

def test_the_event_does_not_claim_to_be_the_missing_report():
    event, _sha = build_unreported_live_order_audit(
        _TASK, CANARY_ROW, reason=REASON, now=NOW)
    codes = event["event"]["reason_codes"]

    # It says what it is...
    assert "LIVE_ORDER_REPORT_RECOVERED" in codes
    assert "POST_ACTION_REPORT_LATE" in codes
    assert "ORIGINAL_AUDIT_EVENT_MISSING" in codes
    # ...and it does NOT say it is the thing build_live_order_audit emits.
    assert "LIVE_ORDER_SUBMITTED" not in codes
    assert "EXECUTE_AND_REPORT" not in codes
    assert "not the missing report" in event["event"]["event_summary"]


def test_the_lost_permission_decision_is_declared_lost_not_replaced():
    """A stand-in P5 id would make this read like the report it cannot be."""
    event, _sha = build_unreported_live_order_audit(
        _TASK, CANARY_ROW, reason=REASON, now=NOW)
    assert "ORIGINAL_PERMISSION_DECISION_UNRECOVERABLE" in event["event"]["reason_codes"]
    assert not any("in_memory:" in ref for ref in event["event"]["related_record_refs"])


def test_the_evidence_is_the_self_hashed_registry_row():
    """The row survived the crash; it is the only durable basis for any claim here."""
    event, _sha = build_unreported_live_order_audit(
        _TASK, CANARY_ROW, reason=REASON, now=NOW)
    assert event["event"]["evidence_refs"] == [f"canary_order_record:{CANARY_ROW['record_sha256']}"]
    assert event["event"]["related_record_refs"] == [f"canary_order:{CANARY_ID}"]


def test_the_reason_is_recorded_verbatim():
    event, _sha = build_unreported_live_order_audit(
        _TASK, CANARY_ROW, reason=REASON, now=NOW)
    assert REASON in event["event"]["event_summary"]


def test_an_unhashed_registry_row_is_refused():
    """An unevidenced claim about real money is worse than the gap it would paper over."""
    row = {k: v for k, v in CANARY_ROW.items() if k != "record_sha256"}
    with pytest.raises(AuditError) as exc:
        build_unreported_live_order_audit(_TASK, row, reason=REASON, now=NOW)
    assert exc.value.reason_code == "UNREPORTED_ORDER_EVIDENCE_UNHASHED"


def test_an_empty_reason_is_refused():
    with pytest.raises(AuditError) as exc:
        build_unreported_live_order_audit(_TASK, CANARY_ROW, reason="   ", now=NOW)
    assert exc.value.reason_code == "UNREPORTED_ORDER_REASON_MISSING"


def test_an_unreconciled_order_does_not_read_as_a_clean_one():
    """`outcome` describes the recording, so the order's own status must ride in the codes."""
    row = {**CANARY_ROW, "reconcile_status": "UNRECONCILABLE", "clean": False}
    event, _sha = build_unreported_live_order_audit(
        _TASK, row, reason=REASON, now=NOW)
    assert "RECONCILE_UNRECONCILABLE" in event["event"]["reason_codes"]
    assert "RECONCILE_RECONCILED" not in event["event"]["reason_codes"]


# --- the script: forward append, evidence-gated, idempotent -------------------------

class _Ledger:
    """In-memory stand-in. `repo_root` has to stay the real repo (the schema lives there), so
    the ledger is isolated here rather than by pointing `--root` at a temp directory."""

    rows: list[dict] = []

    def __init__(self, root=None):
        self.root = root

    @classmethod
    def default(cls, root=None):
        return cls(root)

    def read_audit_events(self):
        return [dict(r) for r in _Ledger.rows]

    def append_audit_events(self, events):
        _Ledger.rows.extend(dict(e) for e in events)


@pytest.fixture
def wired(monkeypatch):
    """The registry returns our row; the Core binding and the ledger are stubbed."""
    _Ledger.rows = []
    monkeypatch.setattr(rec.live_promotion, "read_canary_orders", lambda root: [dict(CANARY_ROW)])
    monkeypatch.setattr(
        rec.live_governance, "prepare_unreported_order_recording",
        lambda canary_record, **kw: {"bound_task": _TASK, "canary_order_id": CANARY_ID},
    )
    monkeypatch.setattr(rec, "LedgerStore", _Ledger)
    return _Ledger


def test_recording_appends_forward_and_leaves_the_original_gap(wired, capsys):
    """The chain gains an event at its tip. Nothing is inserted at the order's moment."""
    earlier, _sha = build_unreported_live_order_audit(
        {"identity": {"task_id": "t0", "task_revision": 1, "trace_id": "tr0"},
         "context": {"core_context_binding_id": "ccb0", "data_sensitivity": "INTERNAL"}},
        {**CANARY_ROW, "canary_order_id": "canary_unrelated"},
        reason="pre-existing unrelated event", now="2026-07-26T10:00:00Z")
    _Ledger.rows = [json.loads(json.dumps(earlier))]

    assert rec.main(["--canary-order-id", CANARY_ID, "--reason", REASON]) == EXIT_OK

    assert len(_Ledger.rows) == 2
    # Appended at the tip, not spliced in before the pre-existing event.
    assert _Ledger.rows[0]["subject"]["subject_id"] == "canary_unrelated"
    assert _Ledger.rows[-1]["subject"]["subject_id"] == CANARY_ID
    assert "forward append" in capsys.readouterr().out


def test_a_second_recording_of_the_same_order_is_refused(wired, capsys):
    """Recording twice would make the chain assert the same real money twice."""
    assert rec.main(["--canary-order-id", CANARY_ID, "--reason", REASON]) == EXIT_OK
    assert rec.main(["--canary-order-id", CANARY_ID, "--reason", REASON]) == EXIT_BLOCKED
    assert "ALREADY_RECORDED" in capsys.readouterr().err
    assert len(_Ledger.rows) == 1


def test_an_order_the_registry_does_not_evidence_is_refused(wired, capsys):
    """It records orders the registry already evidences — never one asserted from a flag."""
    assert rec.main(["--canary-order-id", "canary_never_happened",
                     "--reason", REASON]) == EXIT_BLOCKED
    assert "CANARY_ORDER_NOT_FOUND" in capsys.readouterr().err
    assert _Ledger.rows == []


def test_the_script_places_no_order():
    """The load-bearing property: this module cannot reach a venue.

    Asserted structurally rather than trusted — it imports no adapter and no execution module,
    so there is nothing to gate.
    """
    import inspect

    text = inspect.getsource(rec)
    for forbidden in ("live_execution", "submit_and_reconcile", "select_order_adapter"):
        assert forbidden not in text, f"{forbidden} must not be reachable from this tool"
