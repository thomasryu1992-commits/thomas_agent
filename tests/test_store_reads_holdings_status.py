"""The Claude door's `holdings_status`: contract v1.1, freshness and H6 check states, never the board.

HOLDINGS_STATUS_READ_AND_AUTO_FLOWS R1–R3 (Thomas 2026-10-26). Synthetic snapshots under tmp_path only; no real
holdings file is read.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from runtime.mvp_runtime import read_bridge, store_reads
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import ControlBlocked
from runtime.mvp_runtime.holdings import store as holdings_store
from runtime.mvp_runtime.scheduler import ScheduleStore
from runtime.mvp_runtime.store import LedgerStore

REPO = Path(__file__).resolve().parents[1]
NOW = "2026-10-26T06:00:00Z"
CLAUDE = read_bridge.PROFILES[read_bridge.CLAUDE_PROFILE]

# What a snapshot carries beside the readiness, each marked so a leak is findable in the reply.
LEAKS = {"total_krw": 987654321, "cash_krw": 123456789, "weights": {"LEAK-ETF": 0.42}, "account": "****LEAK",
         "notes": ["LEAK-NOTE"], "holding_count": 77, "allocation": {"LEAK": 1}, "combined": {"nav": 555555555}}
READINESS = {"checks": {**{name: "PASS" for name in holdings_store.READINESS_CHECKS},
                        "toss_cash_semantics": "WAITING", "post_cutover_exceptions": "FAIL"},
             "ready": False, "shadow_days": 9.04, "shadow_success_dates": 9, "exceptions": 1,
             "toss_needs": ["buy", "sell"], "next_step": "LEAK-NEXT-STEP"}


def _snapshot(tmp_path, *, readiness=None, extra=None):
    body = {"record_type": "holdings_snapshot", "as_of": "2026-10-26T05:30:00Z", "partial": False, **LEAKS,
            "cash_flows": {"readiness": readiness or READINESS, "open_exception_keys": ["toss:residual:KRW:LEAK"],
                           "toss_note": "LEAK-TOSS-NOTE", "external_flows": 3}, **(extra or {})}
    path = holdings_store.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _read(tmp_path):
    return store_reads.read_holdings_status(now=NOW, repo_root=tmp_path, projection=store_reads.PROJECTION_V1)


def test_the_reply_carries_freshness_and_check_states_and_nothing_else(tmp_path):
    _snapshot(tmp_path)
    out = _read(tmp_path)
    data = out["data"]
    assert set(data) == {"available", "snapshot_as_of", "age_seconds", "stale", "partial", "h6", "as_of", "projection"}
    assert data["projection"] == store_reads.PROJECTION_V1
    assert data["h6"] == {"ready": False, "checks": READINESS["checks"], "open_exceptions": 1, "shadow_days": 9.0,
                          "shadow_success_dates": 9, "toss_needs": ["buy", "sell"]}
    text = json.dumps(out, ensure_ascii=False)
    for leak in ("987654321", "123456789", "LEAK", "555555555", "0.42", "77"):
        assert leak not in text, leak


def test_an_unknown_check_or_state_is_marked_partial_never_forwarded(tmp_path):
    readiness = {**READINESS, "checks": {**READINESS["checks"], "LEAK_new_check": "PASS", "ledger_chain": "LEAKY"},
                 "toss_needs": ["buy", "LEAK-need"]}
    _snapshot(tmp_path, readiness=readiness)
    data = _read(tmp_path)["data"]
    assert data["projection"] == store_reads.PROJECTION_PARTIAL
    assert "LEAK_new_check" not in data["h6"]["checks"] and data["h6"]["checks"]["ledger_chain"] == "UNKNOWN"
    assert data["h6"]["toss_needs"] == ["buy"]


def test_no_snapshot_is_a_typed_absence(tmp_path):
    data = _read(tmp_path)["data"]
    assert data == {"available": False, "reason_code": "HOLDINGS_SNAPSHOT_MISSING", "as_of": NOW,
                    "projection": store_reads.PROJECTION_V1}


def test_an_unreadable_snapshot_refuses_without_its_text(tmp_path):
    path = holdings_store.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json LEAK", encoding="utf-8")
    with pytest.raises(ControlBlocked) as exc:
        _read(tmp_path)
    assert exc.value.reason_code == "READ_UNAVAILABLE" and "LEAK" not in str(exc.value)


def test_the_schema_refuses_a_field_outside_the_contract():
    with pytest.raises(ControlBlocked) as exc:
        store_reads._checked({"available": True, "as_of": NOW, "projection": "v1", "total_krw": 1},
                             store_reads._holdings_schema(holdings_store.READINESS_CHECKS,
                                                          holdings_store.READINESS_STATUSES, holdings_store.TOSS_NEEDS))
    assert exc.value.reason_code == "REPLY_SCHEMA_INVALID"


def test_the_claude_door_answers_it_through_the_contract(tmp_path):
    (tmp_path / "governance").mkdir()
    shutil.copy(REPO / "governance" / "GOVERNANCE_POLICY.yaml", tmp_path / "governance" / "GOVERNANCE_POLICY.yaml")
    _snapshot(tmp_path)
    reply = read_bridge.apply_read({"command": "holdings_status"}, control_store=ControlStore(tmp_path),
                                   ledger=LedgerStore(tmp_path), now=NOW, repo_root=tmp_path,
                                   schedules=ScheduleStore(tmp_path), profile=CLAUDE)
    assert reply["ok"] is True and reply["data"]["h6"]["open_exceptions"] == 1
    assert "LEAK" not in json.dumps(reply, ensure_ascii=False)
