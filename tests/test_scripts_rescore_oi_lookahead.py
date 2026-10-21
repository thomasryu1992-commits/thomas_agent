"""The OI-lookahead re-score (E2 of CRYPTO_ARCHIVE_BACKFILL_V0.1, "결정 (Thomas 2026-10-21)").

INVALID rows are replayed under the fixed alignment and appended as NEW rows that are BIASED: valid
numbers for the search, no confirmation for the doors. Nothing here touches the network or the store:
snapshots come from the mock collector and the store is a list.
"""

from __future__ import annotations

from runtime.mvp_runtime.cli_common import EXIT_OK, EXIT_USAGE
from runtime.mvp_runtime.crypto import candidate_ranking as cr
from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.read_only_kernel import integrity
from scripts import rescore_stale_holdout_candidates as rescore

NOW = "2026-10-20T12:00:00Z"
PRE_FIX = "2026-09-01T00:00:00Z"


def _spec(*, timeframe="4h", feature="open_interest_change_pct", scope=("BTCUSDT",)):
    raw = {
        "schema_version": "strategy_spec.v1", "strategy_id": "S-OI", "strategy_version": "1.0",
        "strategy_family": "oi_squeeze_long", "symbol_scope": list(scope), "timeframe": timeframe,
        "direction": "long",
        "entry_rules": {"operator": "AND", "conditions": [{"feature": feature, "comparison": ">", "value": 0.0}]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.5, "target_atr": 2.0, "max_holding_bars": 6},
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }
    return StrategySpec.from_dict(raw).to_dict()


def _row(cid, *, created=PRE_FIX, **kw):
    return {"candidate_id": cid, "strategy_id": "S-OI", "strategy_rule_hash": f"rh_{cid}",
            "strategy_spec": _spec(**kw), "backtest_evidence": {"holdout": {}}, "created_at_utc": created}


def _snapshot(symbol, timeframe, now, root):
    """Mock candles plus a daily OI series that flips +6% / -6% day over day."""
    snapshot, _ = collect_market_data(symbol, timeframe, collector=MockMarketDataCollector(), now=NOW, limit=1200)
    days = sorted({c["open_time"][:10] for c in snapshot["candles"]})
    level, events = 1_000_000.0, []
    for i, day in enumerate(days):
        level *= 1.06 if i % 2 else 0.94
        events.append({"timestamp": f"{day}T00:00:00Z", "open_interest": level})
    snapshot["open_interest"] = events
    return snapshot


def _store(monkeypatch, tmp_path, rows):
    store = list(rows)
    built: list[tuple[str, str]] = []

    def build(symbol, timeframe, *, now, root):
        built.append((symbol, timeframe))
        return _snapshot(symbol, timeframe, now, root)

    monkeypatch.setattr(rescore, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(rescore.pool_store, "read_candidates", lambda root: list(store))
    monkeypatch.setattr(rescore.pool_store, "append_candidates",
                        lambda records, root: store.extend(records) or len(records))
    monkeypatch.setattr(rescore, "_build_snapshot", build)
    return store, built


def test_the_selection_is_the_invalid_oi_rows_on_1h_and_4h_only():
    rows = [
        _row("c_4h"), _row("c_1h", timeframe="1h"), _row("c_15m", timeframe="15m"),
        _row("c_1d", timeframe="1d"),                          # 1d never looked ahead
        _row("c_rsi", feature="rsi"),                          # reads no OI
        _row("c_post", created="2026-10-21T00:00:00Z"),        # minted after the fix
    ]
    chosen = rescore.select_oi_lookahead(rows, timeframes=rescore.OI_RESCORE_TIMEFRAMES, candidate_ids=None)
    assert [r["candidate_id"] for r in chosen] == ["c_1h", "c_4h"]


def test_a_rescore_is_biased_never_invalid_and_its_tail_confirms_nothing(monkeypatch, tmp_path, capsys):
    store, _ = _store(monkeypatch, tmp_path, [_row("c_single")])
    assert rescore.main(["--oi-lookahead", "--apply"]) == EXIT_OK
    new = [r for r in store if r.get("rescored_from_candidate_id") == "c_single"]
    assert len(new) == 1
    row = new[0]
    assert row["rescore_reason"] == cr.OI_LOOKAHEAD_RESCORE_REASON and row["created_at_utc"] > PRE_FIX
    assert cr.evidence_standing(row) == {"invalid": (), "biased": (cr.REASON_OI_LOOKAHEAD_RESCORE,)}
    assert cr.candidate_quality(row)["holdout_status"] != "CONFIRMED"
    assert row["candidate_id"] != "c_single" and row["strategy_rule_hash"] == "rh_c_single"


def test_a_pooled_scope_is_replayed_pooled_and_hashed_the_way_the_factory_hashes_it(monkeypatch, tmp_path):
    pooled = ("BTCUSDT", "ETHUSDT")
    store, built = _store(monkeypatch, tmp_path, [_row("c_pool", scope=pooled), _row("c_eth", scope=("ETHUSDT",))])
    assert rescore.main(["--oi-lookahead", "--apply"]) == EXIT_OK
    assert sorted(built) == [("BTCUSDT", "4h"), ("ETHUSDT", "4h")]      # the single context reuses ETH
    row = next(r for r in store if r.get("rescored_from_candidate_id") == "c_pool")
    legs = [_snapshot(s, "4h", NOW, tmp_path) for s in pooled]
    assert row["evidence_input_sha256"] == integrity.sha256_record({"candles": [leg["candles"] for leg in legs]})
    from runtime.mvp_runtime.crypto.backtest import backtest_spec_pooled
    from runtime.mvp_runtime.crypto.factory import build_replay_frame
    expected = backtest_spec_pooled(StrategySpec.from_dict(_spec(scope=pooled)), legs,
                                    frames=[build_replay_frame(leg) for leg in legs])
    assert row["backtest_evidence"] == expected


def test_a_dry_run_writes_nothing_and_a_second_apply_finds_nothing_left(monkeypatch, tmp_path, capsys):
    store, _ = _store(monkeypatch, tmp_path, [_row("c_a"), _row("c_b", timeframe="1h")])
    assert rescore.main(["--oi-lookahead"]) == EXIT_OK and len(store) == 2
    assert rescore.main(["--oi-lookahead", "--apply"]) == EXIT_OK and len(store) == 4
    capsys.readouterr()
    assert rescore.main(["--oi-lookahead", "--apply"]) == EXIT_OK and len(store) == 4
    assert "nothing to do" in capsys.readouterr().out


def test_15m_is_refused(monkeypatch, tmp_path):
    _store(monkeypatch, tmp_path, [_row("c_15m", timeframe="15m")])
    assert rescore.main(["--oi-lookahead", "--timeframe", "15m"]) == EXIT_USAGE


# --- compute in the sandbox (--emit), append in production (--import) --------------------------------

def _emitted(monkeypatch, tmp_path, rows):
    store, _ = _store(monkeypatch, tmp_path, rows)
    out = tmp_path / "rescored.jsonl"
    assert rescore.main(["--oi-lookahead", "--emit", str(out)]) == EXIT_OK
    assert len(store) == len(rows)                       # --emit never writes the store
    return store, out


def test_an_emitted_file_imports_cleanly_and_only_with_apply(monkeypatch, tmp_path, capsys):
    store, out = _emitted(monkeypatch, tmp_path, [_row("c_a"), _row("c_b", timeframe="1h")])
    assert rescore.main(["--import", str(out)]) == EXIT_OK and len(store) == 2
    assert rescore.main(["--import", str(out), "--apply"]) == EXIT_OK and len(store) == 4
    capsys.readouterr()
    # The same file again: both sources are re-scored now.
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    assert rescore.main(["--import", str(out), "--apply"]) == EXIT_BLOCKED and len(store) == 4
    assert "already re-scored" in capsys.readouterr().err


def _tampered(out, **changes):
    import json
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    rows[0].update(changes)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_a_file_that_would_be_more_than_a_re_measurement_is_refused_whole(monkeypatch, tmp_path, capsys):
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    cases = {
        "strategy_spec differs": {"strategy_spec": _spec(feature="open_interest_zscore")},
        "rescore_reason is not": {"rescore_reason": "something else"},
        "strategy_rule_hash differs": {"strategy_rule_hash": "rh_other"},
        "candidate_id does not derive": {"candidate_id": "cand_forged"},
        "not after the OI fix": {"created_at_utc": PRE_FIX},
        "no such source": {"rescored_from_candidate_id": "c_missing"},
    }
    for needle, change in cases.items():
        store, out = _emitted(monkeypatch, tmp_path, [_row("c_a"), _row("c_b", timeframe="1h")])
        _tampered(out, **change)
        capsys.readouterr()
        assert rescore.main(["--import", str(out), "--apply"]) == EXIT_BLOCKED, needle
        assert needle in capsys.readouterr().err, needle
        assert len(store) == 2, needle                   # all-or-nothing: the clean row is not written either


def test_a_source_that_is_not_invalid_cannot_be_imported_against(monkeypatch, tmp_path, capsys):
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    store, out = _emitted(monkeypatch, tmp_path, [_row("c_a")])
    store[0] = {**store[0], "created_at_utc": "2026-10-21T00:00:00Z"}   # the source now reads post-fix
    assert rescore.main(["--import", str(out), "--apply"]) == EXIT_BLOCKED and len(store) == 1
    assert "not INVALID" in capsys.readouterr().err
