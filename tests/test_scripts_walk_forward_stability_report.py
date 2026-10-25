"""The walk-forward report's discrimination split reads evidence standing (2026-10-25)."""

from __future__ import annotations

from runtime.mvp_runtime.crypto import robustness
from scripts import walk_forward_stability_report as report


def _record(cid, *, positive_periods, holdout_tag, oi=False):
    """A row whose would-be temporal_stability is ``positive_periods / 20``, holdout tagged for the stub."""
    feature = "open_interest_change_pct" if oi else "rsi"
    return {
        "candidate_id": cid,
        "created_at_utc": "2026-09-01T00:00:00Z" if oi else "2026-10-22T00:00:00Z",
        "strategy_spec": {"timeframe": "1h", "entry_rules": {"conditions": [{"feature": feature}]}},
        "backtest_evidence": {
            "walk_forward": {"period_r": [1.0] * positive_periods + [-1.0] * (20 - positive_periods),
                             "period_trades": [3] * 20},
            "holdout": {"tag": holdout_tag},
        },
    }


def test_invalid_evidence_is_left_out_of_the_discrimination_split(monkeypatch, capsys):
    records = [
        _record("c_oi_1", positive_periods=18, holdout_tag="C", oi=True),   # lookahead: lifts both sides
        _record("c_oi_2", positive_periods=17, holdout_tag="C", oi=True),
        _record("c_ok", positive_periods=8, holdout_tag="C"),
        _record("c_bad", positive_periods=9, holdout_tag="X"),
    ]
    monkeypatch.setattr(report.pool, "read_candidates", lambda root: records)
    monkeypatch.setattr(report.robustness, "holdout_status", lambda h: robustness.HOLDOUT_CONFIRMED
                        if h and h.get("tag") == "C" else robustness.HOLDOUT_CONTRADICTED)
    assert report.main([]) == 0
    out = capsys.readouterr().out
    assert "2 with INVALID evidence left out" in out
    confirmed = next(line for line in out.splitlines() if line.strip().startswith(robustness.HOLDOUT_CONFIRMED))
    assert "n=1 " in confirmed and "p50 0.400" in confirmed          # only c_ok: 8 of 20 periods positive
