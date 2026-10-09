"""H6b — the shadow cash-flow ledger (Thomas 2026-10-08, PORTFOLIO_CASH_FLOW_LEDGER_V0.1.md D-H6-5–10).

Exactly once under replay and overlap; pending rows wait; a later failure is a reversal line; a row that
does not fit is HELD and the source a MISMATCH; a cross-source look-alike is HELD, never merged; an
internal transfer is not an external flow and, inside the sources' window, fails coherence; Toss
residuals are recorded SHADOW_ONLY; a broken chain refuses; nothing leaves but counts; nothing is applied.
"""

from __future__ import annotations

import ast
import datetime
import json
import pathlib
from types import SimpleNamespace

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import binance_wallet, cash_flows, combined

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-10-09T03:00:00Z"
NOW_MS = int(datetime.datetime(2026, 10, 9, 3, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
LATER = "2026-10-09T04:00:00Z"
LATER_MS = NOW_MS + 3_600_000


class Feed:
    """A fake Binance feed: ``answers[source]`` is the list each read returns."""

    def __init__(self, **answers):
        self.answers = {name: [] for name in binance_wallet.FLOW_SOURCES}
        self.answers.update(answers)
        self.reads: list[tuple[str, int, int]] = []

    def flow_history(self, source, *, start_ms, end_ms, timeout_seconds=5):
        self.reads.append((source, start_ms, end_ms))
        answer = self.answers[source]
        if isinstance(answer, Exception):
            raise answer
        return list(answer)


DEPOSIT = {"id": "d1", "coin": "USDT", "amount": "100.5", "status": 1, "insertTime": NOW_MS - 600_000}
WITHDRAW = {"id": "w1", "coin": "BTC", "amount": "0.01", "transactionFee": "0.0001", "status": 6,
            "applyTime": "2026-10-09 02:30:00", "completeTime": "2026-10-09 02:35:00"}


def _collect(tmp_path, feed, now=NOW, now_ms=NOW_MS):
    return cash_flows.collect_binance(feed, tmp_path, now=now, now_ms=now_ms)


def test_a_replayed_or_overlapped_event_is_recorded_once(tmp_path):
    feed = Feed(crypto_deposit=[DEPOSIT, DEPOSIT], crypto_withdraw=[WITHDRAW])
    assert _collect(tmp_path, feed)["written"] == 2
    assert _collect(tmp_path, feed, now=LATER, now_ms=LATER_MS)["written"] == 0     # overlap re-read
    rows = cash_flows.verify(tmp_path)
    assert [r["source_event_key"] for r in rows] == ["crypto_deposit:d1", "crypto_withdraw:w1"]
    assert rows[0]["direction"] == "in" and rows[0]["amount"] == "100.5" and rows[1]["fee"] == "0.0001"
    assert all(r["accounting_mode"] == "shadow" and r["valuation_status"] == "UNVALUED" for r in rows)


def test_the_first_read_reaches_back_seven_days_and_later_ones_overlap_a_day(tmp_path):
    feed = Feed()
    _collect(tmp_path, feed)
    first = {s: (a, b) for s, a, b in feed.reads}
    assert first["crypto_deposit"] == (NOW_MS - 8 * 86_400_000, NOW_MS)          # 7 days + the overlap
    assert first["transfer_futures_to_spot"][0] >= NOW_MS - 7 * 86_400_000       # the venue's 7-day window
    feed.reads.clear()
    _collect(tmp_path, feed, now=LATER, now_ms=LATER_MS)
    assert {s: a for s, a, _b in feed.reads}["crypto_deposit"] == NOW_MS - cash_flows.OVERLAP_MS


def test_a_pending_row_waits_and_is_recorded_once_it_settles(tmp_path):
    pending = {**DEPOSIT, "status": 0}
    _collect(tmp_path, Feed(crypto_deposit=[pending]))
    assert cash_flows.verify(tmp_path) == []
    state = cash_flows.load_state(tmp_path)["sources"]["crypto_deposit"]
    assert state["cursor_ms"] == DEPOSIT["insertTime"]                            # held behind the pending row
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT]), now=LATER, now_ms=LATER_MS)
    assert [r["source_event_key"] for r in cash_flows.verify(tmp_path)] == ["crypto_deposit:d1"]


def test_a_recorded_success_that_later_fails_gets_one_reversal_line(tmp_path):
    _collect(tmp_path, Feed(crypto_withdraw=[WITHDRAW]))
    failed = {**WITHDRAW, "status": 5}
    _collect(tmp_path, Feed(crypto_withdraw=[failed]), now=LATER, now_ms=LATER_MS)
    _collect(tmp_path, Feed(crypto_withdraw=[failed]), now=LATER, now_ms=LATER_MS + 1)
    rows = cash_flows.verify(tmp_path)
    assert [r["event"] for r in rows] == ["flow_recorded", "flow_reversed"]
    assert rows[1]["reverses"] == "crypto_withdraw:w1" and rows[1]["accounting_status"] == "HELD"


def test_a_row_that_does_not_fit_is_held_once_and_the_source_is_a_mismatch(tmp_path):
    odd = {"id": "x", "coin": "USDT", "status": 1}                                 # no amount, no time
    _collect(tmp_path, Feed(crypto_deposit=[odd]))
    _collect(tmp_path, Feed(crypto_deposit=[odd]), now=LATER, now_ms=LATER_MS)
    rows = cash_flows.verify(tmp_path)
    assert len(rows) == 1 and rows[0]["event"] == "malformed_source_event"
    assert rows[0]["accounting_status"] == "HELD" and rows[0]["held_reason"] == cash_flows.MALFORMED_SOURCE_EVENT
    assert cash_flows.load_state(tmp_path)["sources"]["crypto_deposit"]["schema"] == "MISMATCH"


def test_schema_is_earned_by_a_real_row(tmp_path):
    _collect(tmp_path, Feed())
    assert cash_flows.load_state(tmp_path)["sources"]["crypto_deposit"]["schema"] == "UNVERIFIED_NO_ROWS"
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT]), now=LATER, now_ms=LATER_MS)
    assert cash_flows.load_state(tmp_path)["sources"]["crypto_deposit"]["schema"] == "VERIFIED"


def test_a_cross_source_look_alike_is_held_never_merged(tmp_path):
    pay = {"transactionId": "p1", "amount": "100.5", "currency": "USDT", "transactionTime": NOW_MS - 300_000}
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT], pay=[pay]))
    rows = {r["source_event_key"]: r for r in cash_flows.verify(tmp_path)}
    assert set(rows) == {"crypto_deposit:d1", "pay:p1", "link:crypto_deposit:d1|pay:p1"}
    effective = cash_flows.effective_status(list(rows.values()))
    assert effective == {"crypto_deposit:d1": "HELD", "pay:p1": "HELD"}


@pytest.mark.parametrize("source, row, reason", [
    ("fiat_buy", {"orderNo": "f1", "cryptoCurrency": "BTC", "obtainAmount": "0.001", "sourceAmount": "100",
                  "status": "Completed", "createTime": NOW_MS}, "fiat_payment_scope_unverified"),
    ("pay", {"transactionId": "p2", "amount": "-5", "currency": "USDT", "transactionTime": NOW_MS},
     "pay_direction_unverified"),
])
def test_sources_whose_meaning_is_unverified_are_held(tmp_path, source, row, reason):
    _collect(tmp_path, Feed(**{source: [row]}))
    (recorded,) = cash_flows.verify(tmp_path)
    assert recorded["accounting_status"] == "HELD" and recorded["held_reason"] == reason


def test_an_internal_transfer_is_no_external_flow_and_is_reported_for_coherence(tmp_path):
    move = {"tranId": 7, "asset": "USDT", "amount": "50", "status": "CONFIRMED", "timestamp": NOW_MS - 120_000}
    result = _collect(tmp_path, Feed(transfer_futures_to_spot=[move]))
    assert result["internal_ms"] == [NOW_MS - 120_000]
    summary = cash_flows.summary(tmp_path)
    assert summary["external_flows"] == 0 and summary["internal_transfers"] == 1


@pytest.mark.parametrize("transfers, expected", [([], False), ([NOW_MS - 120_000], True), (None, True),
                                                 ([NOW_MS - 3 * 3_600_000], False)])
def test_a_transfer_between_the_futures_and_wallet_times_fails_coherence(tmp_path, transfers, expected):
    times = {combined.SOURCE_TOSS: NOW, combined.SOURCE_FUTURES: "2026-10-09T02:50:00Z",
             combined.SOURCE_SPOT: NOW, combined.SOURCE_EARN: NOW}
    assert combined._transfer_crossing(times, transfers, NOW) is expected


def test_a_failing_source_keeps_its_cursor_and_the_rest_go_on(tmp_path):
    _collect(tmp_path, Feed())
    feed = Feed(crypto_withdraw=ToolError("BINANCE_WALLET_FORBIDDEN", "x"), crypto_deposit=[DEPOSIT])
    result = _collect(tmp_path, feed, now=LATER, now_ms=LATER_MS)
    state = cash_flows.load_state(tmp_path)["sources"]
    assert result["errors"] == {"crypto_withdraw": "BINANCE_WALLET_FORBIDDEN"}
    assert state["crypto_withdraw"]["cursor_ms"] == NOW_MS and state["crypto_withdraw"]["access"].startswith("FAIL")
    assert state["crypto_deposit"]["cursor_ms"] == LATER_MS and result["written"] == 1


def test_the_transfers_are_read_first_and_outside_the_shadow_budget(tmp_path):
    """They gate coherence (H6b-hardening): a slow day spends the budget on the shadow histories only."""
    ticks = iter([0.0, 0.0, 0.0] + [100.0] * 50)
    feed = Feed()
    result = cash_flows.collect_binance(feed, tmp_path, now=NOW, now_ms=NOW_MS, clock=lambda: next(ticks))
    assert [s for s, _a, _b in feed.reads] == list(cash_flows.CRITICAL_SOURCES)
    assert set(result["errors"]) == set(binance_wallet.FLOW_SOURCES) - set(cash_flows.CRITICAL_SOURCES)
    assert set(result["errors"].values()) == {"READ_BUDGET"}


def test_a_broken_chain_refuses_and_writes_nothing(tmp_path):
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT]))
    path = cash_flows.ledger_path(tmp_path)
    row = json.loads(path.read_text(encoding="utf-8"))
    row["amount"] = "1000"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        _collect(tmp_path, Feed(crypto_withdraw=[WITHDRAW]), now=LATER, now_ms=LATER_MS)
    assert exc.value.reason_code == cash_flows.LEDGER_TAMPERED


# --- Toss, shadow only ---------------------------------------------------------------------------

def _snap(krw, usd, rate=1400.0):
    return SimpleNamespace(usd_krw_rate=rate, domestic=SimpleNamespace(cash_krw=krw),
                           overseas=SimpleNamespace(cash_krw=usd * rate))


class Toss:
    def __init__(self, orders=()):
        self.orders = list(orders)

    def closed_orders(self, **_kw):
        return self.orders, False


def test_toss_cash_explained_by_a_fill_leaves_no_residual(tmp_path):
    cash_flows.collect_toss(Toss(), _snap(1_000_000, 100.0), tmp_path, now=NOW)
    buy = {"side": "BUY", "currency": "KRW", "execution": {"filledAmount": "700000", "commission": "1400", "tax": "0",
                                                           "filledAt": "2026-10-09T03:30:00Z"}}
    cash_flows.collect_toss(Toss([buy]), _snap(298_600, 100.0), tmp_path, now=LATER)
    assert cash_flows.verify(tmp_path) == []


def test_an_unexplained_toss_change_is_an_unresolved_shadow_residual(tmp_path):
    cash_flows.collect_toss(Toss(), _snap(1_000_000, 100.0), tmp_path, now=NOW)
    cash_flows.collect_toss(Toss(), _snap(6_000_000, 100.0), tmp_path, now=LATER)
    (row,) = cash_flows.verify(tmp_path)
    assert row["event"] == "toss_residual" and row["pocket"] == "KRW" and row["residual"] == "5000000"
    assert row["source_confidence"] == "UNRESOLVED" and row["accounting_status"] == "SHADOW_ONLY"


def test_a_krw_usd_exchange_pair_is_reconciled_internal(tmp_path):
    cash_flows.collect_toss(Toss(), _snap(1_000_000, 0.0), tmp_path, now=NOW)
    cash_flows.collect_toss(Toss(), _snap(860_000, 100.0), tmp_path, now=LATER)
    (row,) = cash_flows.verify(tmp_path)
    assert row["event"] == "internal_exchange" and row["source_confidence"] == "RECONCILED"


# --- the boundary --------------------------------------------------------------------------------

def test_the_summary_carries_counts_and_statuses_only(tmp_path):
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT], crypto_withdraw=[WITHDRAW]))
    text = json.dumps(cash_flows.summary(tmp_path))
    for leak in ("USDT", "BTC", "100.5", "0.01", "d1", "w1"):
        assert leak not in text, leak


def test_nothing_is_applied_and_no_unit_exists(tmp_path):
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT], crypto_withdraw=[WITHDRAW]))
    rows = cash_flows.verify(tmp_path)
    assert not [r for r in rows if r.get("accounting_status") == "APPLIED"]
    assert not [k for r in rows for k in r if "unit" in k]


def test_no_door_module_reads_the_ledger():
    allowed = ({ROOT / "runtime" / "mvp_runtime" / "holdings" / n for n in ("cash_flows.py", "store.py")}
               | {ROOT / "scripts" / "holdings_board.py"})      # the terminal's cutover command, not a door
    offenders = []
    for path in [*(ROOT / "runtime").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        literal = any(isinstance(n, ast.Constant) and n.value in (cash_flows.FILENAME, cash_flows.STATE_FILENAME)
                      for n in ast.walk(tree))
        imports = any(isinstance(n, ast.ImportFrom) and ((n.module or "").endswith("cash_flows")
                      or any(a.name == "cash_flows" for a in n.names)) for n in ast.walk(tree))
        if literal or imports:
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_a_transfer_in_the_window_withholds_the_nav_through_combine(tmp_path):
    """The wiring, not just the rule: a fire whose futures file predates a transfer the wallet read
    already reflects gives no NAV, no peak move, no verdict."""
    futures = tmp_path / combined.BINANCE_SNAPSHOT_REL
    futures.parent.mkdir(parents=True, exist_ok=True)
    futures.write_text(json.dumps({"record_type": combined.BINANCE_RECORD_TYPE, "configured": True, "asset": "USDT",
                                   "margin_balance": 100.0, "as_of": "2026-10-09T02:50:00Z"}), encoding="utf-8")
    zero = {name: 0.0 for name in binance_wallet.CLASSES}
    wallet = binance_wallet.WalletSnapshot(spot_usdt=zero, earn_usdt=dict(zero), unpriced_assets=0,
                                           collected_at=NOW, latency_ms=0)

    def fire(transfers, hdir):
        return combined.combine({"known_total_krw": 1e7, "partial": False}, usd_krw_rate=1400.0, root=tmp_path,
                                now=NOW, state_dir=tmp_path / hdir, wallet=wallet, wallet_status=combined.WALLET_OK,
                                toss_as_of=NOW, toss_reconciliation_failures=[], internal_transfer_ms=transfers)

    clean = fire([], "a")
    assert clean["portfolio_nav_complete"] is True and clean["internal_transfer_in_window"] is False
    crossed = fire([NOW_MS - 120_000], "b")
    assert crossed["checks"]["coherence"] == combined.FAIL and crossed["internal_transfer_in_window"] is True
    assert crossed["portfolio_nav_complete"] is False and crossed["drawdown_state"] == combined.STATE_UNKNOWN
    assert fire(None, "c")["checks"]["coherence"] == combined.FAIL                  # transfers unknown


# --- H6b-hardening (Thomas 2026-10-09) -------------------------------------------------------------

def test_without_a_cutover_every_event_is_observed_only(tmp_path):
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT]))
    (row,) = cash_flows.verify(tmp_path)
    assert row["cutover_phase"] == "NO_CUTOVER" and row["accounting_eligibility"] == "OBSERVED_ONLY"
    assert not cash_flows.load_state(tmp_path).get("cutover_at")                 # a fire never sets it


def test_the_cutover_is_set_once_and_splits_events_by_their_own_time(tmp_path):
    cash_flows.set_cutover(tmp_path, at=NOW, requested_by="claude", shadow_started_at="2026-10-09T03:33:54Z")
    before = {**DEPOSIT, "id": "before", "insertTime": NOW_MS - 60_000}
    after = {**DEPOSIT, "id": "after", "insertTime": NOW_MS + 60_000}
    _collect(tmp_path, Feed(crypto_deposit=[before, after]), now=LATER, now_ms=LATER_MS)
    rows = {r["source_event_key"]: r for r in cash_flows.verify(tmp_path)}
    assert rows["cutover_set:" + NOW]["cutover_at"] == NOW
    assert rows["crypto_deposit:before"]["accounting_eligibility"] == "OBSERVED_ONLY"
    assert rows["crypto_deposit:after"]["cutover_phase"] == "POST_CUTOVER"
    assert rows["crypto_deposit:after"]["accounting_eligibility"] == "ELIGIBLE"
    state = cash_flows.load_state(tmp_path)
    assert state["first_verified_fire_at"] == LATER and state["shadow_started_at"] == "2026-10-09T03:33:54Z"
    _collect(tmp_path, Feed(), now="2026-10-09T05:00:00Z", now_ms=LATER_MS + 3_600_000)   # a restart, a redeploy
    assert cash_flows.load_state(tmp_path)["cutover_at"] == NOW


def test_moving_the_cutover_is_a_migration_with_a_reason(tmp_path):
    cash_flows.set_cutover(tmp_path, at=NOW, requested_by="claude")
    with pytest.raises(ToolError) as exc:
        cash_flows.set_cutover(tmp_path, at=LATER, requested_by="claude")
    assert exc.value.reason_code == cash_flows.CUTOVER_REFUSED
    with pytest.raises(ToolError):
        cash_flows.set_cutover(tmp_path, at=LATER, requested_by="thomas", migrate=True)
    cash_flows.set_cutover(tmp_path, at=LATER, requested_by="thomas", migrate=True, reason="ledger repair")
    last = cash_flows.verify(tmp_path)[-1]
    assert last["event"] == "cutover_migrated" and last["previous_cutover_at"] == NOW
    assert last["reason"] == "ledger repair" and cash_flows.load_state(tmp_path)["cutover_at"] == LATER


def test_a_look_alike_holds_the_older_ready_event_too(tmp_path):
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT]))
    (first,) = cash_flows.verify(tmp_path)
    assert first["accounting_status"] == "READY"
    pay = {"transactionId": "p9", "amount": "100.50000000", "currency": "USDT", "transactionTime": NOW_MS - 300_000}
    _collect(tmp_path, Feed(pay=[pay]), now=LATER, now_ms=LATER_MS)
    rows = cash_flows.verify(tmp_path)
    assert rows[0] == first                                                  # never edited
    assert cash_flows.effective_status(rows)["crypto_deposit:d1"] == "HELD"  # but held by the link line
    assert cash_flows.summary(tmp_path)["held"] == 2


def test_amounts_compare_as_numbers():
    assert cash_flows._canon("1") == cash_flows._canon("1.00000000") == cash_flows._canon("1.0")
    assert cash_flows._canon("1.1") != cash_flows._canon("1.01")


def _toss_window(tmp_path, *, side, residual_krw=0):
    cash_flows.collect_toss(Toss(), _snap(1_000_000, 0.0), tmp_path, now=NOW)
    sign = -1 if side == "BUY" else 1
    fill = {"side": side, "currency": "KRW", "execution": {"filledAmount": "100000", "commission": "0", "tax": "0",
                                                           "filledAt": "2026-10-09T03:30:00Z"}}
    cash_flows.collect_toss(Toss([fill]), _snap(1_000_000 + sign * 100_000 + residual_krw, 0.0), tmp_path, now=LATER)
    return cash_flows.load_state(tmp_path)["toss_evidence"]


def test_an_explained_fill_is_toss_evidence(tmp_path):
    assert _toss_window(tmp_path, side="BUY")["buy_explained"] == 1


def test_a_fill_that_leaves_a_residual_makes_the_toss_semantics_mixed(tmp_path):
    evidence = _toss_window(tmp_path, side="SELL", residual_krw=5_000)
    assert evidence["activity_unexplained"] == 1
    state, _why = cash_flows.toss_semantics(cash_flows.load_state(tmp_path), today_kst="2026-10-10")
    assert state == "MIXED"


def test_readiness_waits_and_enables_nothing(tmp_path):
    _collect(tmp_path, Feed())
    ready = cash_flows.readiness(tmp_path, now=LATER)
    assert ready["ready"] is False
    assert ready["checks"]["binance_real_schema"] == "WAITING" and ready["checks"]["toss_cash_semantics"] == "WAITING"
    assert ready["checks"]["shadow_observation"] == "RUNNING" and ready["checks"]["internal_transfer_guard"] == "PASS"


def test_readiness_is_ready_only_when_every_piece_is_in(tmp_path):
    cash_flows.set_cutover(tmp_path, at=NOW, requested_by="claude")
    _collect(tmp_path, Feed(crypto_deposit=[DEPOSIT]))
    state = cash_flows.load_state(tmp_path)
    state["toss_evidence"] = {"buy_explained": 1, "sell_explained": 1, "activity_unexplained": 0,
                              "settlement_days": {"2026-10-10": {"windows": 20, "unexplained": 0}}}
    cash_flows._save_state(tmp_path, state)
    assert cash_flows.readiness(tmp_path, now="2026-10-12T00:00:00Z")["ready"] is False   # 3 days of shadow
    ready = cash_flows.readiness(tmp_path, now="2026-10-17T00:00:00Z")
    assert ready["ready"] is True and set(ready["checks"].values()) == {"PASS"}


def test_readiness_tells_once_per_epoch_and_again_after_it_breaks(tmp_path, monkeypatch):
    sequence = iter([True, True, False, True])
    monkeypatch.setattr(cash_flows, "readiness",
                        lambda _d, *, now: {"ready": next(sequence), "checks": {}, "shadow_days": 7, "toss_note": "",
                                            "next_step": "x"})
    first = cash_flows.update_readiness(tmp_path, now=NOW)
    assert first["ready"] and first["epoch"] == 1 and not first["told"]
    cash_flows.mark_readiness_told(tmp_path, now=NOW)
    assert cash_flows.update_readiness(tmp_path, now=NOW)["told"] is True          # still ready: not again
    assert cash_flows.update_readiness(tmp_path, now=NOW)["ready"] is False        # it broke
    again = cash_flows.update_readiness(tmp_path, now=NOW)
    assert again["ready"] and again["epoch"] == 2 and not again["told"]           # back: told again


def test_a_failed_transfer_read_fails_this_fires_coherence(monkeypatch, tmp_path):
    """Through the store: the transfers are unknown, so the fire cannot rule out double counting."""
    from runtime.mvp_runtime.holdings import store
    from runtime.mvp_runtime.holdings.model import HoldingsSnapshot, MarketTotals

    futures = tmp_path / combined.BINANCE_SNAPSHOT_REL
    futures.parent.mkdir(parents=True, exist_ok=True)
    futures.write_text(json.dumps({"record_type": combined.BINANCE_RECORD_TYPE, "configured": True, "asset": "USDT",
                                   "margin_balance": 100.0, "as_of": NOW}), encoding="utf-8")
    zero = {name: 0.0 for name in binance_wallet.CLASSES}
    wallet = binance_wallet.WalletSnapshot(spot_usdt=zero, earn_usdt=dict(zero), unpriced_assets=0,
                                           collected_at=NOW, latency_ms=0)
    toss = HoldingsSnapshot(account="****", broker="toss",
                            domestic=MarketTotals(market="domestic", holdings_value_krw=0.0, unrealized_pnl_krw=0.0,
                                                  cash_krw=1_000_000.0),
                            overseas=MarketTotals(market="overseas", holdings_value_krw=0.0, unrealized_pnl_krw=0.0,
                                                  cash_krw=0.0),
                            holdings=(), collected_at=NOW, latency_ms=1, usd_krw_rate=1400.0)
    monkeypatch.setattr(store, "read_holdings", lambda **_: (toss, None))
    monkeypatch.setattr(store, "_feed", lambda: None)
    monkeypatch.setattr(binance_wallet, "read_wallet", lambda **_: (wallet, None))
    capable = binance_wallet.BinanceWalletFeed()
    failing = Feed(transfer_futures_to_spot=ToolError("TOOL_TRANSPORT", "x"))
    monkeypatch.setattr(capable, "flow_history", failing.flow_history)
    monkeypatch.setattr(binance_wallet, "select_wallet_feed", lambda: capable)
    store.refresh(now=NOW, root=tmp_path)
    block = json.loads(store.snapshot_path(tmp_path).read_text(encoding="utf-8"))["combined"]
    assert block["checks"]["coherence"] == "FAIL" and block["internal_transfer_in_window"] is True
    assert block["portfolio_nav_complete"] is False
