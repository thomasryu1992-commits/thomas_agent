"""H6d-min — readiness on current health, its invariants, and the LOCAL_ONLY boundary (scenarios 36-44).

A past PASS never stands for this fire; a resolution is not H6c; told state follows the ledger; nothing
leaves the process but counts. Synthetic fixtures only; no real holdings file is read.
"""

from __future__ import annotations

import json

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import (CUTOVER, CURRENT_OK, Feed, collect, ledger, ms, order, pay, posix_only, ready_fire,
                                resolve, set_cutover)
from tests.test_mvp_runtime_holdings_cash_flows import _store_fire

DEPOSIT = {"id": "d1", "coin": "USDT", "amount": "100.5", "status": 1, "insertTime": ms(CUTOVER) - 600_000}
READY_AT = "2026-10-17T00:00:00Z"


def _ready_baseline(tmp_path):
    """Every piece in by READY_AT: the boundary, a real row, epoch-0 counters, eight verified dates."""
    set_cutover(tmp_path)
    collect(tmp_path, Feed(crypto_deposit=[DEPOSIT]), now="2026-10-09T04:00:00Z")
    state = cash_flows.load_state(tmp_path)
    state["toss_evidence"] = {"buy_explained": 1, "sell_explained": 1, "activity_unexplained": 0,
                              "settlement_days": {"2026-10-10": {"windows": 20, "unexplained": 0}}}
    cash_flows._save_state(tmp_path, state)
    for day in range(9, 17):
        cash_flows.mark_fire_verified(tmp_path, now=f"2026-10-{day:02d}T04:00:00Z")
    ready = ready_fire(tmp_path, READY_AT)
    assert ready["ready"] is True, ready["checks"]
    return ready


# 36
def test_a_past_pass_does_not_cover_a_source_failing_now(tmp_path):
    _ready_baseline(tmp_path)
    failing = Feed(pay=ToolError("TOOL_TRANSPORT", "x"))
    ready = ready_fire(tmp_path, "2026-10-17T01:00:00Z", failing, current={**CURRENT_OK, "binance": False})
    assert ready["checks"]["binance_access"] == "FAIL" and ready["ready"] is False
    skipped = cash_flows.readiness(tmp_path, now="2026-10-17T02:00:00Z", current=CURRENT_OK)   # no fire at 02:00
    assert skipped["checks"]["binance_access"] == "UNCHECKED" and skipped["ready"] is False
    for part in ("toss", "freshness", "coherence"):
        late = ready_fire(tmp_path, "2026-10-17T03:00:00Z", current={**CURRENT_OK, part: False})
        assert late["checks"]["current_health"] == "FAIL" and late["ready"] is False, part


def test_a_source_skipped_for_the_budget_is_not_a_pass(tmp_path):
    _ready_baseline(tmp_path)
    ticks = iter([0.0, 0.0, 0.0] + [100.0] * 50)
    cash_flows.collect_binance(Feed(), tmp_path, now="2026-10-17T01:00:00Z", now_ms=ms("2026-10-17T01:00:00Z"),
                               clock=lambda: next(ticks))
    ready = cash_flows.readiness(tmp_path, now="2026-10-17T01:00:00Z", current=CURRENT_OK)
    assert ready["checks"]["binance_access"] == "FAIL" and ready["ready"] is False


# 37
def test_a_past_pass_does_not_cover_a_schema_mismatch_now(tmp_path):
    _ready_baseline(tmp_path)
    odd = Feed(crypto_deposit=[{"id": "x", "coin": "USDT", "status": 1}])
    ready = ready_fire(tmp_path, "2026-10-17T01:00:00Z", odd)
    assert ready["checks"]["binance_real_schema"] == "FAIL" and ready["ready"] is False


# 38
def test_readiness_returns_once_the_present_is_healthy_again(tmp_path):
    _ready_baseline(tmp_path)
    assert ready_fire(tmp_path, "2026-10-17T01:00:00Z", Feed(pay=ToolError("TOOL_TRANSPORT", "x")),
                      current={**CURRENT_OK, "binance": False})["ready"] is False
    assert ready_fire(tmp_path, "2026-10-17T02:00:00Z")["ready"] is True


# 39
def test_readiness_that_breaks_and_returns_is_a_new_epoch_told_again(tmp_path):
    _ready_baseline(tmp_path)

    def fire(at, feed=None, current=CURRENT_OK):
        collect(tmp_path, feed, now=at)
        return cash_flows.update_readiness(tmp_path, now=at, current=current)

    first = fire("2026-10-17T01:00:00Z")
    assert first["ready"] and first["epoch"] == 1 and not first["told"]
    cash_flows.mark_readiness_told(tmp_path, now="2026-10-17T01:00:00Z")
    assert fire("2026-10-17T02:00:00Z")["told"] is True
    broken = fire("2026-10-17T03:00:00Z", Feed(pay=ToolError("X", "x")), {**CURRENT_OK, "binance": False})
    assert broken["ready"] is False
    again = fire("2026-10-17T04:00:00Z")
    assert again["ready"] and again["epoch"] == 2 and not again["told"]


# 40
def test_nothing_turns_h6c_on(tmp_path):
    ready = _ready_baseline(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1", at="2026-10-17T00:30:00Z")]), now="2026-10-17T01:00:00Z")
    resolve(tmp_path, "pay:p1", "deposit")
    rows = ledger(tmp_path)
    assert cash_flows.effective_status(rows)["pay:p1"] == "HELD"                     # a resolution is not READY
    assert all(r["accounting_mode"] == "shadow" for r in rows)
    assert not [r for r in rows if r.get("accounting_status") == "APPLIED"]
    def keys(value):
        if isinstance(value, dict):
            return set(value) | {k for v in value.values() for k in keys(v)}
        return {k for v in value for k in keys(v)} if isinstance(value, list) else set()

    fields = keys([rows, cash_flows.load_state(tmp_path), ready, cash_flows.summary(tmp_path)])
    assert not [k for k in fields if "unit" in k.lower() or "nav" in k.lower()]          # no unit, no NAV field
    assert ready["next_step"].startswith("H6b-shadow")


def test_a_message_marks_only_the_exceptions_it_named(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now="2026-10-09T04:00:00Z")
    in_flight = cash_flows.update_readiness(tmp_path, now="2026-10-09T04:00:00Z")
    collect(tmp_path, Feed(pay=[pay("p2", "6", at="2026-10-09T04:10:00Z")]), now="2026-10-09T04:20:00Z")
    cash_flows.mark_exceptions_told(tmp_path, token=in_flight["exceptions_token"], now="2026-10-09T04:20:00Z")
    after = cash_flows.update_readiness(tmp_path, now="2026-10-09T04:20:00Z")
    assert after["exceptions"] == 2 and after["exceptions_new"] == 1                   # p2 still owed
    cash_flows.mark_exceptions_told(tmp_path, token="not-a-token", now="2026-10-09T04:20:00Z")
    assert cash_flows.update_readiness(tmp_path, now="2026-10-09T04:20:00Z")["exceptions_new"] == 1


def test_the_old_told_count_carries_over_without_a_repeat(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now="2026-10-09T04:00:00Z")
    state = cash_flows.load_state(tmp_path)
    state["exceptions"] = {"count": 1, "told_count": 1}                                # as H6b left it
    cash_flows._save_state(tmp_path, state)
    assert cash_flows.update_readiness(tmp_path, now="2026-10-09T04:00:00Z")["exceptions_untold"] is False


# 41 and 42
def test_nothing_local_leaves_through_the_snapshot_or_the_message(monkeypatch, tmp_path):
    from runtime.mvp_runtime.holdings import store

    cash_flows.set_cutover(store.state_dir(tmp_path), at="2026-10-09T02:00:00Z", requested_by="test")
    secret = pay("p1", "123.45", at="2026-10-09T02:59:00Z", currency="SECRETCOIN")
    secret_order = order("ORD-SECRET-1", "BUY", "777777", "2026-10-09T02:30:00Z")
    from tests.test_mvp_runtime_holdings_cash_flows import Feed as StoreFeed
    _store_fire(monkeypatch, tmp_path, flows=StoreFeed(pay=[secret]), now="2026-10-09T02:00:00Z")
    result = _store_fire(monkeypatch, tmp_path, flows=StoreFeed(pay=[secret]), toss_orders=[secret_order],
                         now="2026-10-09T03:00:00Z")
    rows = cash_flows.verify(store.state_dir(tmp_path))
    hashes = [h for r in rows if r["event"] == "toss_window_observed" for p in r["buy_orders"].values()
              for entry in p for h in entry]
    outward = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    message = json.dumps(result.get("exception_alert"), ensure_ascii=False)
    for leak in ("SECRETCOIN", "123.45", "ORD-SECRET", "777777", "pay:p1", "toss:window", *hashes):
        assert leak not in outward and leak not in message, leak
    assert "SECRETCOIN" not in json.dumps(cash_flows.summary(store.state_dir(tmp_path)))


# 43
@posix_only
def test_the_terminal_only_modes_are_refused_by_the_claude_guard(tmp_path):
    from tests.test_claude_local_only_guard import _decision, _payload

    for flag in ("--flows", "--resolve pay:p1 --kind deposit --by t --reason r",
                 "--semantics-epoch --change-ref '#1' --by t --reason r"):
        command = f"docker exec -it thomas-scheduler-maint python -m scripts.holdings_board {flag}"
        assert _decision(_payload("Bash", command=command), tmp_path) == "deny", flag


# 44
@posix_only
def test_the_aggregate_view_still_passes_the_guard_and_keeps_its_keys(monkeypatch, tmp_path):
    from runtime.mvp_runtime.holdings import store
    from tests.test_claude_local_only_guard import _decision, _payload

    for command in ("docker exec thomas-scheduler python -m scripts.holdings_board",
                    "docker exec thomas-scheduler python -m scripts.holdings_board --json"):
        assert _decision(_payload("Bash", command=command), tmp_path) == "allow"
    _store_fire(monkeypatch, tmp_path)
    stored = json.loads(store.snapshot_path(tmp_path).read_text(encoding="utf-8"))
    assert set(stored["cash_flows"]["readiness"]) == {"checks", "ready", "shadow_days", "shadow_success_dates",
                                                      "exceptions", "next_step"}
