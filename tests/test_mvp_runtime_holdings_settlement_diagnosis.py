"""Why the Toss check's settlement day is not clean yet, as counts and closed reasons (Thomas 2026-11-07).

``cash_flows.settlement_diagnosis`` mirrors ``toss_evidence`` per KST day without changing it (its output is
inside the reconciliation digest), so ``clean`` is taken from ``toss_evidence`` and must agree with it. The
door carries the diagnosis as ``holdings_status`` v1.3 ``h6.settlement``. Synthetic ledgers under tmp_path only.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import store_reads
from runtime.mvp_runtime.holdings import cash_flows
from runtime.mvp_runtime.holdings import store as holdings_store
from tests._h6d_support import hourly, ledger, order, start_epoch, toss_fire

DAY = "2026-10-10"                   # the KST day the windows below end on
AFTER_DAY = "2026-10-11"
SETTLES = order("s9", "SELL", "50000", "2026-10-08T01:00:00Z", settlement=DAY)


def _fires(tmp_path, count, *, sell=SETTLES, residual_at=None):
    for i, at in enumerate(hourly("2026-10-09T15:30:00Z", count)):
        cash = 1_000_000 + (5_000 if residual_at is not None and i >= residual_at else 0)
        toss_fire(tmp_path, cash, at, [sell])


def _diagnosis(tmp_path, today=AFTER_DAY):
    return cash_flows.settlement_diagnosis(ledger(tmp_path), cash_flows.load_state(tmp_path), today_kst=today)


def _day(diagnosis, name=DAY):
    return next(row for row in diagnosis["days"] if row["day"] == name)


def _clean_days(tmp_path, today=AFTER_DAY):
    rows = ledger(tmp_path)
    return cash_flows.toss_evidence(rows, epoch=cash_flows.toss_epoch(rows), today_kst=today)["clean_settlement_days"]


def test_eleven_settling_windows_are_too_few_and_twelve_are_clean(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 12)                                                 # the first fire opens no window: 11
    row = _day(_diagnosis(tmp_path))
    assert (row["windows"], row["settlement_windows"], row["clean"]) == (11, 11, False)
    assert row["reasons"] == ["too_few_windows"]
    toss_fire(tmp_path, 1_000_000, "2026-10-10T14:30:00Z", [SETTLES])     # 23:30 KST, still the day
    out = _diagnosis(tmp_path)
    assert _day(out) == {"day": DAY, "windows": 12, "settlement_windows": 12, "clean": True, "reasons": []}
    assert out["epoch"] == 1 and out["judged_by"] == "windows" and out["clean_days"] == 1
    assert "legacy_clean_days" not in out


def test_a_residual_on_the_day_is_named(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 15, residual_at=5)
    row = _day(_diagnosis(tmp_path))
    assert row["clean"] is False and "residual_open" in row["reasons"]


def test_the_day_itself_has_not_passed(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 14)
    row = _day(_diagnosis(tmp_path, today=DAY))
    assert row["clean"] is False and "not_yet_passed" in row["reasons"]


@pytest.mark.parametrize("shape", ["20261010", "2026-10-10T00:00:00+09:00", "2026/10/10"])
def test_a_settlement_date_in_another_shape_records_nothing_while_the_sell_still_counts(tmp_path, shape):
    """The signature of a format mismatch: the sell is evidence, yet no window ever records its settlement."""
    start_epoch(tmp_path)
    toss_fire(tmp_path, 1_000_000, "2026-10-08T00:00:00Z")
    toss_fire(tmp_path, 1_050_000, "2026-10-08T02:00:00Z",
              [order("s9", "SELL", "50000", "2026-10-08T01:00:00Z", settlement=shape)])
    toss_fire(tmp_path, 1_050_000, "2026-10-09T15:00:00Z")
    for at in hourly("2026-10-09T15:30:00Z", 14):
        toss_fire(tmp_path, 1_050_000, at, [order("s9", "SELL", "50000", "2026-10-08T01:00:00Z", settlement=shape)])
    out = _diagnosis(tmp_path)
    assert out["sells_explained"] == 1 and out["settlement_entries"] == 0 and out["clean_days"] == 0
    assert _day(out)["reasons"] == ["no_settlement_record"]


def test_clean_always_agrees_with_toss_evidence(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 15, residual_at=13)
    out = _diagnosis(tmp_path)
    assert sorted(r["day"] for r in out["days"] if r["clean"]) == _clean_days(tmp_path)
    assert out["clean_days"] == len(_clean_days(tmp_path))


def test_epoch_zero_names_the_frozen_counters_as_the_judge(tmp_path):
    _fires(tmp_path, 14)
    out = _diagnosis(tmp_path)
    assert out["epoch"] == 0 and out["judged_by"] == "legacy_counters"


def test_the_diagnosis_keeps_no_hash_time_or_amount(tmp_path):
    start_epoch(tmp_path)
    _fires(tmp_path, 14, residual_at=5)
    text = json.dumps(_diagnosis(tmp_path))
    hashes = {h for w in ledger(tmp_path) if w.get("event") == "toss_window_observed"
              for entry in w.get("settlements") or [] for h in entry["order"]}
    assert hashes
    for leak in (*hashes, "toss:window", "T15:30", "50000", "1005000", "5000"):
        assert leak not in text, leak
    assert set(r for row in _diagnosis(tmp_path)["days"] for r in row["reasons"]) <= set(cash_flows.SETTLEMENT_REASONS)


def test_the_diagnosis_leaves_the_reconciliation_digest_alone():
    """A changed digest is an epoch transition; the diagnosis must not be part of what it hashes."""
    assert "settlement_diagnosis" not in json.dumps(cash_flows.reconciliation_digest())
    assert cash_flows.reconciliation_digest() == cash_flows.reconciliation_digest()



def test_a_broken_diagnosis_never_costs_the_readiness(tmp_path, monkeypatch):
    """The diagnosis explains the check; a defect in it leaves it out and the verdict stands."""
    start_epoch(tmp_path)
    _fires(tmp_path, 14)

    def broken(*_a, **_kw):
        raise RuntimeError("diagnosis defect")

    monkeypatch.setattr(cash_flows, "settlement_diagnosis", broken)
    ready = cash_flows.readiness(tmp_path, now="2026-10-11T03:00:00Z")
    assert ready["toss_diagnosis"] is None and "toss_cash_semantics" in ready["checks"]


# --- the door: holdings_status v1.3 ------------------------------------------------------------------------

NOW = "2026-11-07T06:00:00Z"
CHECKS = {name: "PASS" for name in holdings_store.READINESS_CHECKS}
GOOD = {"epoch": 1, "judged_by": "windows", "settlement_entries": 30, "sells_explained": 1, "clean_days": 0,
        "days": [{"day": "2026-11-05", "windows": 24, "settlement_windows": 9, "clean": False,
                  "reasons": ["too_few_windows", "residual_open"]}]}


def _project(diagnosis):
    view = {"available": True, "as_of": "2026-11-07T05:30:00Z", "age_seconds": 1800.0, "stale": False,
            "partial": False, "cash_flows": {"readiness": {"checks": CHECKS, "ready": False, "exceptions": 0,
                                                           "toss_needs": ["settlement_day"],
                                                           "toss_diagnosis": diagnosis}}}
    return store_reads.project_holdings_status(view, now=NOW)


def test_the_door_carries_the_diagnosis_whole():
    data = _project(GOOD)
    assert data["projection"] == store_reads.PROJECTION_V1
    assert data["h6"]["settlement"] == GOOD


def test_the_door_drops_what_the_contract_does_not_name():
    leaky = {**GOOD, "order_hashes": ["LEAK-HASH"], "residual_krw": 987654,
             "days": [{**GOOD["days"][0], "window_end": "2026-11-05T01:00:00Z", "reasons": ["residual_open", "LEAK"]},
                      {"day": "2026-11-05T01:00:00Z", "windows": 1, "settlement_windows": 0, "clean": False,
                       "reasons": []}]}
    data = _project(leaky)
    assert data["projection"] == store_reads.PROJECTION_PARTIAL
    assert data["h6"]["settlement"]["days"] == [{**GOOD["days"][0], "reasons": ["residual_open"]}]
    text = json.dumps(data)
    for leak in ("LEAK", "987654", "T01:00"):
        assert leak not in text, leak


def test_an_unknown_judge_is_partial_and_an_unusable_diagnosis_is_left_out():
    data = _project({**GOOD, "judged_by": "LEAK-judge"})
    assert data["h6"]["settlement"]["judged_by"] == "UNKNOWN" and data["projection"] == store_reads.PROJECTION_PARTIAL
    data = _project({"days": []})
    assert "settlement" not in data["h6"] and data["projection"] == store_reads.PROJECTION_PARTIAL


def test_the_door_keeps_at_most_the_diagnosis_window():
    days = [{"day": f"2026-10-{d:02d}", "windows": 24, "settlement_windows": 0, "clean": False,
             "reasons": ["no_settlement_record"]} for d in range(1, 21)]
    data = _project({**GOOD, "days": days})
    assert len(data["h6"]["settlement"]["days"]) == cash_flows.DIAGNOSIS_DAYS
    assert data["h6"]["settlement"]["days"][-1]["day"] == "2026-10-20"
    assert data["projection"] == store_reads.PROJECTION_PARTIAL
