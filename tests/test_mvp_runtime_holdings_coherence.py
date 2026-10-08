"""H4-min — snapshot coherence and reconciliation join the NAV's required checks (Thomas 2026-10-08).

Coherence: the included sources' own times lie within MAX_SNAPSHOT_SKEW_SECONDS of each other, and a
missing time fails. Reconciliation: Toss's per-market aggregate agrees with its items. Either failing
leaves the H2 footprint: no NAV, no peak move, no verdict.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from runtime.mvp_runtime.holdings import binance_wallet, board, combined
from runtime.mvp_runtime.holdings.model import Holding, HoldingsSnapshot, MarketTotals

NOW = "2026-10-08T09:00:00Z"
TOSS = {"known_total_krw": 10_000_000.0, "partial": False}


def _futures_file(root: pathlib.Path, *, as_of: str = NOW) -> None:
    path = root / combined.BINANCE_SNAPSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"record_type": combined.BINANCE_RECORD_TYPE, "configured": True, "asset": "USDT",
                                "margin_balance": 100.0, "as_of": as_of}), encoding="utf-8")


def _wallet(at: str = NOW):
    zero = {name: 0.0 for name in binance_wallet.CLASSES}
    return binance_wallet.WalletSnapshot(spot_usdt=zero, earn_usdt=dict(zero), unpriced_assets=0,
                                         collected_at=at, latency_ms=0)


def _combine(tmp_path, *, toss_as_of=NOW, wallet_at=NOW, recon=()):
    return combined.combine(TOSS, usd_krw_rate=1400.0, root=tmp_path, now=NOW, state_dir=tmp_path / "h",
                            wallet=_wallet(wallet_at), wallet_status=combined.WALLET_OK, toss_as_of=toss_as_of,
                            toss_reconciliation_failures=None if recon is None else list(recon))


def _peaked(tmp_path):
    hdir = tmp_path / "h"
    hdir.mkdir()
    peak = {"peak_total_krw": 50_000_000.0, "peak_at": NOW, "scope_version": combined.PORTFOLIO_SCOPE_VERSION}
    (hdir / combined.PEAK_FILENAME).write_text(json.dumps(peak))
    return hdir, peak


def test_both_h4_checks_are_required_now():
    assert combined.CHECK_COHERENCE in combined.REQUIRED_CHECKS
    assert combined.CHECK_RECONCILIATION in combined.REQUIRED_CHECKS
    assert combined.MAX_SNAPSHOT_SKEW_SECONDS == 30 * 60


def test_sources_within_the_window_cohere(tmp_path):
    _futures_file(tmp_path, as_of="2026-10-08T08:45:00Z")          # one 15-minute write behind
    block = _combine(tmp_path)
    assert block["checks"]["coherence"] == combined.PASS and block["snapshot_skew_seconds"] == 900.0
    assert block["portfolio_nav_complete"] is True
    assert set(block["source_as_of"]) == set(combined.PORTFOLIO_SCOPE)


@pytest.mark.parametrize("case, kwargs, futures_at", [
    ("futures 45 min behind", {}, "2026-10-08T08:15:00Z"),
    ("wallet 31 min behind", {"wallet_at": "2026-10-08T08:29:00Z"}, NOW),
    ("toss time missing", {"toss_as_of": None}, NOW),
    ("toss time unreadable", {"toss_as_of": "yesterday"}, NOW),
])
def test_an_incoherent_snapshot_withholds_the_nav_and_the_peak(tmp_path, case, kwargs, futures_at):
    _futures_file(tmp_path, as_of=futures_at)
    hdir, peak = _peaked(tmp_path)
    block = _combine(tmp_path, **kwargs)
    assert block["checks"]["coherence"] == combined.FAIL
    assert block["portfolio_nav_complete"] is False and block["combined_total_krw"] is None
    assert block["drawdown_state"] == combined.STATE_UNKNOWN
    assert json.loads((hdir / combined.PEAK_FILENAME).read_text()) == peak
    assert any(line.startswith("source skew") for line in board.render_combined(block))


@pytest.mark.parametrize("recon", [["domestic_mismatch"], None], ids=["mismatch", "not-computed"])
def test_an_unreconciled_toss_read_withholds_the_nav(tmp_path, recon):
    _futures_file(tmp_path)
    hdir, peak = _peaked(tmp_path)
    block = _combine(tmp_path, recon=recon)
    assert block["checks"]["reconciliation"] == combined.FAIL
    assert block["reconciliation_failures"] == (recon or ["not_computed"])
    assert block["portfolio_nav_complete"] is False
    assert json.loads((hdir / combined.PEAK_FILENAME).read_text()) == peak
    assert any(line.startswith("reconcile") for line in board.render_combined(block))


# --- the Toss reconciliation itself ---------------------------------------------------------------

RATE = 1400.0


def _toss_snapshot(*, domestic_total=7_200_000.0, overseas_usd=1785.0, items=None, rate=RATE):
    items = items if items is not None else (
        Holding(market="domestic", symbol="A", name="a", quantity=1.0, value=7_200_000.0, unrealized_pnl=0.0,
                currency="KRW"),
        Holding(market="overseas", symbol="B", name="b", quantity=1.0, value=1785.0, unrealized_pnl=0.0,
                currency="USD"),
    )
    return HoldingsSnapshot(
        account="****", broker="toss",
        domestic=MarketTotals(market="domestic", holdings_value_krw=domestic_total, unrealized_pnl_krw=0.0,
                              cash_krw=0.0),
        overseas=MarketTotals(market="overseas",
                              holdings_value_krw=None if rate is None else overseas_usd * rate,
                              unrealized_pnl_krw=0.0, cash_krw=0.0),
        holdings=tuple(items), collected_at=NOW, latency_ms=0, usd_krw_rate=rate)


def test_an_aggregate_that_matches_its_items_reconciles():
    assert combined.toss_reconciliation(_toss_snapshot()) == []
    assert combined.toss_reconciliation(_toss_snapshot(domestic_total=7_200_000.4)) == []   # rounding


def test_cash_only_reconciles():
    assert combined.toss_reconciliation(_toss_snapshot(domestic_total=0.0, overseas_usd=0.0, items=())) == []


@pytest.mark.parametrize("change, code", [
    ({"domestic_total": 7_300_000.0}, "domestic_mismatch"),
    ({"overseas_usd": 1900.0}, "overseas_mismatch"),
    ({"domestic_total": None}, "domestic_total_unread"),
    ({"rate": None}, "overseas_total_unread"),
])
def test_a_disagreeing_or_unread_aggregate_fails(change, code):
    assert code in combined.toss_reconciliation(_toss_snapshot(**change))


def test_an_item_without_a_value_or_in_the_other_currency_fails():
    base = _toss_snapshot()
    no_value = dataclasses.replace(base, holdings=(dataclasses.replace(base.holdings[0], value=None),
                                                   base.holdings[1]))
    assert "domestic_item_unreadable" in combined.toss_reconciliation(no_value)
    wrong = dataclasses.replace(base, holdings=(base.holdings[0],
                                                dataclasses.replace(base.holdings[1], currency="KRW")))
    assert "overseas_item_unreadable" in combined.toss_reconciliation(wrong)


def test_the_new_block_keys_carry_no_amount(tmp_path):
    _futures_file(tmp_path)
    block = _combine(tmp_path)
    for key in ("source_as_of", "snapshot_skew_seconds", "max_snapshot_skew_seconds", "reconciliation_failures"):
        assert key in combined.COMBINED_KEYS
    text = json.dumps({k: block[k] for k in ("source_as_of", "reconciliation_failures")})
    assert "10000000" not in text and "1400" not in text
