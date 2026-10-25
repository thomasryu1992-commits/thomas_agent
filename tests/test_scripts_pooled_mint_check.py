"""pooled_mint_check counts a lookahead confirmation as INVALID, not as CONFIRMED (2026-10-25)."""

from __future__ import annotations

from runtime.mvp_runtime.crypto import robustness
from scripts import pooled_mint_check as check


def _row(cid, *, oi):
    feature = "open_interest_change_pct" if oi else "rsi"
    return {"candidate_id": cid, "created_at_utc": "2026-09-01T00:00:00Z" if oi else "2026-10-22T00:00:00Z",
            "strategy_spec": {"timeframe": "4h", "symbol_scope": ["BTCUSDT", "ETHUSDT"],
                              "entry_rules": {"conditions": [{"feature": feature}]}},
            "backtest_evidence": {"holdout": {"closed_count": 40, "expectancy": 0.3, "tag": "C"}}}


def test_a_lookahead_confirmation_is_left_out(monkeypatch, capsys):
    monkeypatch.setattr(check.pool, "read_candidates", lambda root: [_row("a", oi=True), _row("b", oi=False)])
    monkeypatch.setattr(robustness, "holdout_status", lambda h: robustness.HOLDOUT_CONFIRMED)
    assert check.main([]) == 0
    out = capsys.readouterr().out
    assert "CONFIRMED: 1/2   (1 more on INVALID evidence, left out)" in out
