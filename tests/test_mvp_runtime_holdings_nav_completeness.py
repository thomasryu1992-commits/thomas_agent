"""H2 — valuation completeness (Thomas 2026-10-08, ``MULTI_ASSET_EXPANSION_V0.1.md`` D-H2-1…8).

The combined total is a portfolio NAV only when every declared source is in it, fully valued and fresh.
Each way it can fall short is a case below, and each one must leave the same footprint: no NAV figure,
no peak move, no drawdown verdict, no allocation verdict, no alert (D-H2-8).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import pytest

from runtime.mvp_runtime.holdings import allocation, binance_wallet, board, combined

NOW = "2026-10-08T09:00:00Z"
RATE = 1400.0
TOSS = {"known_total_krw": 10_000_000.0, "partial": False}


def _futures_file(root: pathlib.Path, *, margin: float = 100.0, as_of: str = NOW) -> None:
    path = root / combined.BINANCE_SNAPSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"record_type": combined.BINANCE_RECORD_TYPE, "configured": True, "asset": "USDT",
                                "margin_balance": margin, "as_of": as_of}), encoding="utf-8")


def _wallet(**changes) -> binance_wallet.WalletSnapshot:
    classes = {name: 0.0 for name in binance_wallet.CLASSES}
    clean = binance_wallet.WalletSnapshot(spot_usdt={**classes, "btc": 50.0}, earn_usdt={**classes, "stable": 25.0},
                                          unpriced_assets=0, collected_at=NOW, latency_ms=0)
    return dataclasses.replace(clean, **changes)


def _combine(tmp_path, *, toss=TOSS, wallet=None, wallet_status=combined.WALLET_OK, rate=RATE, now=NOW,
             toss_warnings=0, toss_as_of=NOW, recon=()):
    return combined.combine(toss, usd_krw_rate=rate, root=tmp_path, now=now, state_dir=tmp_path / "h",
                            wallet=_wallet() if wallet is None and wallet_status == combined.WALLET_OK else wallet,
                            wallet_status=wallet_status, toss_warnings=toss_warnings, toss_as_of=toss_as_of,
                            toss_reconciliation_failures=list(recon))


def test_the_declared_scope_and_the_required_checks_are_the_decided_ones():
    assert combined.PORTFOLIO_SCOPE_VERSION == "v1"
    assert combined.PORTFOLIO_SCOPE == ("toss", "binance_futures", "binance_spot", "binance_earn")   # D-H2-2
    assert combined.REQUIRED_CHECKS[:3] == ("coverage", "valuation", "freshness")                  # D-H2-4
    assert combined.REQUIRED_CHECKS[3:] == ("coherence", "reconciliation")                         # D-H2-5, H4-min
    assert set(combined.CHECKS) == set(combined.REQUIRED_CHECKS)


def test_a_complete_scope_is_a_nav_and_starts_a_scoped_peak(tmp_path):
    _futures_file(tmp_path)
    block = _combine(tmp_path)
    assert set(block) == combined.COMBINED_KEYS
    assert block["portfolio_nav_complete"] is True and block["complete"] is True
    assert all(block[key] is True for key in ("source_fetch_complete", "coverage_complete",
                                              "valuation_complete", "freshness_complete"))
    assert block["checks"] == {"coverage": "PASS", "valuation": "PASS", "freshness": "PASS",
                               "coherence": "PASS", "reconciliation": "PASS"}
    assert block["combined_total_krw"] == pytest.approx(10_000_000.0 + (100.0 + 50.0 + 25.0) * RATE)
    assert all(row == {"included": True, "reason": None} for row in block["sources"].values())
    assert block["drawdown_state"] == combined.STATE_INITIALIZED
    peak = json.loads((tmp_path / "h" / combined.PEAK_FILENAME).read_text())
    assert peak["scope_version"] == combined.PORTFOLIO_SCOPE_VERSION
    assert any("(COMPLETE, scope v1)" in line for line in board.render_combined(block))


# (case, kwargs, futures as_of, the failed check, the source excluded and why)
STALE = "2026-10-08T06:00:00Z"   # three hours before NOW; the futures window is two
CASES = [
    ("gate off", {"wallet_status": combined.WALLET_NOT_CONFIGURED}, NOW, "coverage",
     ("binance_spot", "gate_off")),
    ("wallet read failed", {"wallet_status": "failed (BINANCE_WALLET_FORBIDDEN)"}, NOW, "coverage",
     ("binance_earn", "read_failed")),
    ("earn unread", {"wallet": _wallet(earn_usdt=None)}, NOW, "coverage", ("binance_earn", "earn_unread")),
    ("unpriced asset", {"wallet": _wallet(unpriced_assets=1)}, NOW, "valuation", None),
    ("earn truncated", {"wallet": _wallet(earn_truncated=True)}, NOW, "valuation", None),
    ("invalid row", {"wallet": _wallet(invalid_rows=1)}, NOW, "valuation", None),
    ("toss partial", {"toss": {**TOSS, "partial": True}}, NOW, "valuation", None),
    ("toss parse warning", {"toss_warnings": 2}, NOW, "valuation", None),
    ("no rate", {"rate": None}, NOW, "valuation", ("binance_futures", "no_rate")),
    ("stale futures", {}, STALE, "freshness", ("binance_futures", "stale")),
]


@pytest.mark.parametrize("case, kwargs, as_of, failed, excluded", CASES, ids=[c[0] for c in CASES])
def test_every_shortfall_withholds_the_nav_the_peak_and_every_verdict(tmp_path, case, kwargs, as_of, failed,
                                                                      excluded):
    _futures_file(tmp_path, as_of=as_of)
    hdir = tmp_path / "h"
    hdir.mkdir()
    peak = {"peak_total_krw": 50_000_000.0, "peak_at": NOW, "scope_version": combined.PORTFOLIO_SCOPE_VERSION}
    (hdir / combined.PEAK_FILENAME).write_text(json.dumps(peak))
    block = _combine(tmp_path, **kwargs)
    assert set(block) == combined.COMBINED_KEYS
    assert block["portfolio_nav_complete"] is False and block["complete"] is False
    assert block["checks"][failed] == combined.FAIL
    if excluded:
        source, reason = excluded
        assert block["sources"][source] == {"included": False, "reason": reason}
    assert block["combined_total_krw"] is None                          # no figure under the NAV's name
    assert block["drawdown_state"] == combined.STATE_UNKNOWN and block["drawdown_pct"] is None
    assert json.loads((hdir / combined.PEAK_FILENAME).read_text()) == peak   # the peak did not move
    assert combined.alert(block, told=combined.STATE_CLEAR, as_of=NOW) is None
    assert combined.alert(block, told=combined.STATE_BREACHED, as_of=NOW) is None
    assert any("INCOMPLETE" in line and failed in line for line in board.render_combined(block))
    allocated = allocation.allocate(_no_holdings(), TOSS, block)
    assert allocated["complete"] is False and allocated["bands"] is None and allocated["outside_band"] is None


def _no_holdings():
    from runtime.mvp_runtime.holdings.model import HoldingsSnapshot
    return HoldingsSnapshot(account="****0000", broker="toss", domestic=None, overseas=None, holdings=(),
                            collected_at=NOW, latency_ms=0, usd_krw_rate=RATE)


def test_no_partial_sum_is_produced_under_any_name():
    """D-H2-7, kept by producing none: the parts are each on the board, and their sum is the figure most
    easily read as the whole. A key that would carry one has to be added here on purpose."""
    assert not [key for key in combined.COMBINED_KEYS if "partial" in key or "observed" in key]


def test_a_peak_from_another_scope_is_never_compared_against(tmp_path):
    """A peak written before H2 (Toss + futures, no scope) is not this portfolio's: comparing a larger
    scope with it would read as a gain, a smaller one as a drop. The first complete total starts anew."""
    _futures_file(tmp_path)
    hdir = tmp_path / "h"
    hdir.mkdir()
    (hdir / combined.PEAK_FILENAME).write_text(json.dumps({"peak_total_krw": 99_000_000.0, "peak_at": NOW}))
    block = _combine(tmp_path)
    assert block["drawdown_state"] == combined.STATE_INITIALIZED and block["drawdown_pct"] == 0.0
    assert json.loads((hdir / combined.PEAK_FILENAME).read_text())["scope_version"] == "v1"
    assert combined.alert(block, told=combined.STATE_CLEAR, as_of=NOW) is None


def test_the_wallet_status_names_the_first_valuation_gap():
    rate = RATE
    assert combined.wallet_part(_wallet(), wallet_status="ok", usd_krw_rate=rate)["crypto_wallet_status"] == "ok"
    for change, status in (({"unpriced_assets": 2}, combined.WALLET_UNPRICED),
                           ({"earn_truncated": True}, combined.WALLET_TRUNCATED),
                           ({"invalid_rows": 1}, combined.WALLET_INVALID_ROWS)):
        part = combined.wallet_part(_wallet(**change), wallet_status="ok", usd_krw_rate=rate)
        assert part["crypto_wallet_status"] == status
        assert part["crypto_spot_krw"] == pytest.approx(50.0 * rate)   # still shown, as a part


def test_an_allocation_block_without_a_complete_nav_gives_no_verdict():
    """The allocation reads the same verdict as the total, so it can never judge bands on a scope the
    total refused."""
    block = {"crypto_status": "ok", "crypto_futures_krw": 1_000_000.0, "crypto_wallet_status": "ok",
             "crypto_classes_krw": {"btc": 1.0, "eth": 0.0, "stable": 0.0, "other": 0.0},
             "portfolio_nav_complete": False}
    allocated = allocation.allocate(_no_holdings(), TOSS, block)
    assert allocated["complete"] is False and "portfolio NAV incomplete" in allocated["notes"][0]


def test_a_verdict_gap_resumes_on_the_standing_peak_and_tells_what_moved_inside_it(tmp_path):
    """The H2 gap (2026-10-08, about 4.5 h with no drawdown verdict): an incomplete stretch between two
    complete fires. The gap itself must stay silent and leave everything alone. The first complete
    fire after it compares against the peak that stood before it: no new baseline (a re-initialized
    peak would erase a drop taken inside the gap), and a breach reached inside the gap is told then."""
    from runtime.mvp_runtime.holdings import baseline_log

    _futures_file(tmp_path)
    hdir = tmp_path / "h"
    first = _combine(tmp_path, toss={**TOSS, "known_total_krw": 40_000_000.0})
    assert first["drawdown_state"] == combined.STATE_INITIALIZED
    peak = json.loads((hdir / combined.PEAK_FILENAME).read_text())
    baselines = baseline_log.verify(hdir)
    told = combined.STATE_CLEAR

    for kwargs in ({"wallet_status": combined.WALLET_NOT_CONFIGURED},   # coverage: the gate is off
                   {"toss": {**TOSS, "partial": True}},                    # valuation
                   {"rate": None}):                                        # valuation: no FX
        gap = _combine(tmp_path, **kwargs)
        assert gap["drawdown_state"] == combined.STATE_UNKNOWN and gap["combined_total_krw"] is None
        for mark in (combined.STATE_CLEAR, combined.STATE_BREACHED):     # the gap is never told as a verdict:
            assert combined.alert(gap, told=mark, as_of=NOW) is None     # not as a drop, not as a recovery
        assert json.loads((hdir / combined.PEAK_FILENAME).read_text()) == peak
        assert baseline_log.verify(hdir) == baselines                   # nor does it start a baseline

    back = _combine(tmp_path)                                           # complete again, far lower
    assert back["drawdown_state"] == combined.STATE_BREACHED            # not "initialized"
    assert back["peak_total_krw"] == peak["peak_total_krw"] and back["peak_at"] == peak["peak_at"]
    assert back["drawdown_pct"] == round((back["combined_total_krw"] / peak["peak_total_krw"] - 1) * 100, 2)
    assert baseline_log.verify(hdir) == baselines
    edge = combined.alert(back, told=told, as_of=NOW)
    assert edge is not None and edge[0] == combined.STATE_BREACHED
