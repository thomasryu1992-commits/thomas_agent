"""HISTORICAL_FORWARD_REPLAY_V0.1, the judge: the shard set, the scoring window, the leak and sanity checks."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from scripts.research import forward_replay as fr
from scripts.research import forward_replay_judge as judge


def _write(path, arm, context, days, digest="d1", rows=None):
    head = {"shard_version": fr.SHARD_VERSION, "spec": fr.SPEC, "arm": arm, "context": context,
            "code_digest": digest, "cache_digest": "c"}
    lines = [json.dumps({"manifest": head})]
    lines += [json.dumps({"day": d, "status": "fired", "candidates": rows or []}) for d in days]
    path.write_text("\n".join(lines) + "\n")


def _all_days():
    first, last = fr.fire_span()
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def test_a_complete_arm_loads_and_anything_short_of_it_is_named(tmp_path):
    days = _all_days()
    for c in fr.CONTEXTS:
        _write(tmp_path / f"{c.name}.A.jsonl", "A", c.name, days, rows=[{"c": c.name}])
    stores, digests, problems = judge.load_shards(tmp_path)
    assert problems == [] and digests == {"A": "d1"} and len(stores["A"]) == len(days) * 7
    _write(tmp_path / "pooled-1d.A.jsonl", "A", "pooled-1d", days[:-3])
    assert any("3 fire days missing" in p for p in judge.load_shards(tmp_path)[2])
    _write(tmp_path / "pooled-1d.A.jsonl", "A", "pooled-1d", days, digest="d2")
    assert any("2 code digests" in p for p in judge.load_shards(tmp_path)[2])
    (tmp_path / "pooled-1d.A.jsonl").unlink()
    assert any("no shard for ['pooled-1d']" in p for p in judge.load_shards(tmp_path)[2])
    _write(tmp_path / "again.A.jsonl", "A", "pooled-4h", days)
    assert any("a second shard" in p for p in judge.load_shards(tmp_path)[2])


def test_the_window_reads_twenty_eight_days_and_the_hold_allowance():
    assert judge.window_end(date(2024, 1, 1), "1d") == "2024-03-17T00:00:00Z"        # 28 + 48 days
    assert judge.window_end(date(2024, 1, 1), "1h") == "2024-01-31T00:00:00Z"        # 28 days + 48 hours
    assert judge.scoring_bars("1h") == judge.WARMUP_BARS + 28 * 24 + 48 + 1
    assert judge.scoring_bars("1d") == judge.WARMUP_BARS + 28 + 48 + 1


def test_frames_are_built_once_per_symbol_and_timeframe_at_the_windows_end(tmp_path):
    built = []

    def build(symbol, timeframe, *, now, state, arm):
        built.append((symbol, timeframe, now, arm))
        return None if symbol == "SOLUSDT" else object()
    frames_for = judge.frames_source("B", date(2024, 1, 1), state=tmp_path, build=build)
    assert len(frames_for(["BTCUSDT", "SOLUSDT"], "4h", "x")) == 1
    frames_for(["BTCUSDT"], "4h", "x")
    assert built == [("BTCUSDT", "4h", judge.window_end(date(2024, 1, 1), "4h"), "B"),
                     ("SOLUSDT", "4h", judge.window_end(date(2024, 1, 1), "4h"), "B")]


def test_the_fire_leak_records_must_cover_every_arm_context_and_check_anchor():
    records = [{"arm": a, "context": c.name, "day": d.isoformat(), "identical": True, "code_digest": "d1"}
               for a in ("A",) for c in fr.CONTEXTS for d in judge.check_anchors()]
    assert judge.fire_leak_ok(records, {"A": "d1"}) == (True, [])
    assert judge.fire_leak_ok(records[1:], {"A": "d1"})[0] is False
    flipped = [{**records[0], "identical": False}, *records[1:]]
    assert judge.fire_leak_ok(flipped, {"A": "d1"})[0] is False
    assert judge.fire_leak_ok(records, {"A": "other"})[0] is False
    assert judge.check_anchors() == [fr.replay_anchors()[0], fr.replay_anchors()[27], fr.replay_anchors()[-1]]


def _m(cid, family):
    return {"candidate_id": cid, "strategy_family": family, "timeframe": "4h", "symbol_scope": ["BTCUSDT"]}


def test_the_sanity_rule_freezes_at_the_real_instant_and_bounds_size_and_families():
    store = [{"created_at_utc": "2026-09-20T08:10:00Z"}, {"created_at_utc": "2026-09-25T08:10:00Z"}]
    asked = []

    def eligible(rows, pool, *, exclude_candidate_ids, exclude_lineages):
        asked.append((len(rows), exclude_candidate_ids))
        return [_m(f"x{i}", "breakout") for i in range(10)]
    replayed = [{"anchor": date(2026, 7, 27), "members": [_m("held", "breakout")]}]
    real = [{"cohort_id": "r1", "frozen_at_utc": "2026-09-23T07:03:48Z",
             "members": [_m(f"r{i}", "breakout") for i in range(20)]}]
    got = judge.sanity(store, replayed, real, eligible=eligible)
    assert got["passed"] is True and asked == [(1, frozenset({"held"}))]
    small = [{**real[0], "members": [_m(f"r{i}", "breakout") for i in range(41)]}]
    assert judge.sanity(store, replayed, small, eligible=eligible)["passed"] is False      # 10 < 41 / 4
    other = [{**real[0], "members": [_m("r", "mean_reversion")] * 20}]
    assert judge.sanity(store, replayed, other, eligible=eligible)["passed"] is False
    assert judge.sanity(store, replayed, [], eligible=eligible)["passed"] is False         # nothing compared


def test_b_is_better_only_when_its_paired_excess_over_a_is_significant():
    a = [{"anchor": str(i), "value_R": 0.0} for i in range(30)]
    b = [{"anchor": str(i), "value_R": 0.1 + (0.02 if i % 2 else -0.02)} for i in range(30)]
    assert judge.b_against_a(a, b)["b_better"] is True
    assert judge.b_against_a(b, a)["b_better"] is False
    assert judge.b_against_a(a, [{"anchor": "0", "value_R": None}])["b_better"] is False


def test_the_judge_refuses_outside_the_sandbox(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fr, "_in_sandbox", lambda: False)
    assert judge.main(["--shards", str(tmp_path), "--leak-records", str(tmp_path)]) == judge.EXIT_USAGE
    assert "sandbox" in capsys.readouterr().err
