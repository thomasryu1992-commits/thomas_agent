"""HISTORICAL_FORWARD_REPLAY_HELDOUT_V0.1: the held-out contexts, their fire, shards and the judge's design."""

from __future__ import annotations

import json
from datetime import date, timedelta

from runtime.mvp_runtime.crypto import factory, market_data
from scripts.research import forward_replay as fr
from scripts.research import forward_replay_heldout as ho
from scripts.research import forward_replay_judge as judge


def test_the_held_out_symbols_are_the_preregistered_five_and_none_the_original_read():
    assert ho.HELD == ("ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "DOTUSDT")
    read_before = {*fr.POOLED, market_data.REFERENCE_SYMBOL, *market_data.CROSS_SECTION_UNIVERSE,
                   *(s for c in fr.CONTEXTS for s in c.symbols)}
    assert not set(ho.HELD) & read_before
    assert [(c.name, c.symbols, c.timeframe) for c in ho.CONTEXTS] == [
        ("heldout-4h", ho.HELD, "4h"), ("heldout-1d", ho.HELD, "1d")]


def test_each_context_takes_the_generation_id_of_the_slot_it_stands_in():
    for day in (date(2021, 12, 8), date(2024, 6, 3)):
        assert ho.generation_id(day, ho.BY_NAME["heldout-4h"]) == fr.generation_id(day, fr.BY_NAME["pooled-4h"])
        assert ho.generation_id(day, ho.BY_NAME["heldout-1d"]) == fr.generation_id(day, fr.BY_NAME["pooled-1d"])


def test_the_fire_span_ends_the_day_before_the_last_anchor():
    first, last = ho.fire_span()
    assert first == fr.fire_span()[0] and last == fr.replay_anchors()[-1] - timedelta(days=1)
    assert last < fr.fire_span()[1]


def _stub_legs(monkeypatch, module):
    monkeypatch.setattr(fr, "mining_snapshot", lambda symbol, tf, **k: {"symbol": symbol, "timeframe": tf})

    class _Cache:
        def __init__(self, collector):
            pass
    monkeypatch.setattr(fr.market_data, "PeerCandleCache", _Cache)
    monkeypatch.setattr(fr.market_data, "select_market_data_collector", lambda **k: object())


def test_the_held_out_fire_hands_run_factory_what_the_original_fire_does(monkeypatch, tmp_path):
    _stub_legs(monkeypatch, fr)
    seen = []

    def run(snapshot, **kwargs):
        cohort = [leg["symbol"] for leg in kwargs.pop("cohort_snapshots") or []]
        seen.append({**kwargs, "lead": snapshot["symbol"], "cohort": cohort, "gid": factory.next_generation_id([])})
        return {"candidates": [{"x": 1}]}
    day = date(2024, 6, 3)
    for arm in fr.ARMS:
        ours = ho.fire(ho.BY_NAME["heldout-4h"], day, [{"old": 1}], state=tmp_path, run=run, arm=arm)
        theirs = fr.fire(fr.BY_NAME["pooled-4h"], day, [{"old": 1}], state=tmp_path, run=run, arm=arm)
        assert ours["status"] == theirs["status"] == "fired" and ours["candidates"] == theirs["candidates"]
        a, b = seen[-2], seen[-1]
        assert a["lead"] == "ADAUSDT" and a["cohort"] == list(ho.HELD[1:])
        assert {k: v for k, v in a.items() if k not in ("lead", "cohort")} == \
            {k: v for k, v in b.items() if k not in ("lead", "cohort")}


def test_a_leg_the_cache_cannot_serve_cancels_the_held_out_fire(monkeypatch, tmp_path):
    monkeypatch.setattr(fr, "mining_snapshot", lambda symbol, tf, **k: None if symbol == "LTCUSDT" else {})
    monkeypatch.setattr(fr.market_data, "PeerCandleCache", lambda c: None)
    monkeypatch.setattr(fr.market_data, "select_market_data_collector", lambda **k: object())
    row = ho.fire(ho.BY_NAME["heldout-1d"], date(2024, 6, 3), [], state=tmp_path,
                  run=lambda *a, **k: {"candidates": [{"x": 1}]})
    assert row["status"] == "no_data:LTCUSDT" and row["candidates"] == []


def test_the_digest_covers_this_module_and_the_replay_it_extends(monkeypatch, tmp_path):
    assert ho.code_digest() != fr.code_digest()
    head = ho.manifest(ho.BY_NAME["heldout-4h"], "A", tmp_path)
    assert head["spec"] == ho.SPEC != fr.SPEC and head["code_digest"] == ho.code_digest()


def test_a_held_out_shard_resumes_and_refuses_an_original_shard(monkeypatch, tmp_path):
    calls = []

    def fake_fire(context, day, store, **kwargs):
        calls.append((day, len(store)))
        return {"day": day.isoformat(), "status": "fired", "candidates": [{"d": day.isoformat()}], "seconds": 0}
    monkeypatch.setattr(ho, "fire", fake_fire)
    out, ctx = tmp_path / "s.jsonl", ho.BY_NAME["heldout-1d"]
    start = date(2024, 1, 1)
    assert ho.simulate(ctx, start, start + timedelta(days=1), out, arm="A", cache_root=tmp_path, state=tmp_path) == 0
    assert ho.simulate(ctx, start, start + timedelta(days=2), out, arm="A", cache_root=tmp_path, state=tmp_path) == 0
    assert calls == [(start, 0), (start + timedelta(days=1), 1), (start + timedelta(days=2), 2)]
    original = tmp_path / "o.jsonl"
    original.write_text(json.dumps({"manifest": fr.manifest(fr.BY_NAME["pooled-1d"], "A", tmp_path)}) + "\n")
    assert ho.simulate(ctx, start, start, original, arm="A", cache_root=tmp_path, state=tmp_path) == ho.EXIT_BLOCKED


def _shard(path, spec, arm, context, days):
    head = {"shard_version": fr.SHARD_VERSION, "spec": spec, "arm": arm, "context": context, "code_digest": "d1",
            "cache_digest": "c"}
    lines = [json.dumps({"manifest": head})] + [json.dumps({"day": d, "status": "fired", "candidates": []})
                                                 for d in days]
    path.write_text("\n".join(lines) + "\n")


def _days(span):
    first, last = span()
    return [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]


def test_each_design_judges_only_its_own_shards(tmp_path):
    days = _days(ho.fire_span)
    for c in ho.CONTEXTS:
        _shard(tmp_path / f"{c.name}.A.jsonl", ho.SPEC, "A", c.name, days)
    _, digests, problems = judge.load_shards(tmp_path, design=judge.HELDOUT)
    assert problems == [] and digests == {"A": "d1"}
    assert any(f"not a shard of {fr.SPEC}" in p for p in judge.load_shards(tmp_path)[2])
    _shard(tmp_path / "pooled-1d.A.jsonl", fr.SPEC, "A", "pooled-1d", _days(fr.fire_span))
    assert any(f"not a shard of {ho.SPEC}" in p for p in judge.load_shards(tmp_path, design=judge.HELDOUT)[2])
    (tmp_path / "pooled-1d.A.jsonl").unlink()
    _shard(tmp_path / "heldout-1d.A.jsonl", ho.SPEC, "A", "heldout-1d", days[:-1])
    assert any("1 fire days missing" in p for p in judge.load_shards(tmp_path, design=judge.HELDOUT)[2])


def test_the_held_out_leak_records_cover_its_two_contexts():
    records = [{"arm": "A", "context": c.name, "day": d.isoformat(), "identical": True, "code_digest": "d1"}
               for c in ho.CONTEXTS for d in judge.check_anchors()]
    assert judge.fire_leak_ok(records, {"A": "d1"}, contexts=ho.CONTEXTS) == (True, [])
    assert judge.fire_leak_ok(records, {"A": "d1"})[0] is False          # the original's seven are not there


def _report(spec, arm, *, sanity=None):
    reading = {"anchors": 30, "mean_R": 0.1, "p_one_sided": 0.01, "years_R": {"2023": 1.0, "2024": 1.0}}
    r = {"spec": spec, "arm": arm, "code_digest": "d1", "fire_leak_ok": True, "scoring_leak": {"identical": True},
         "reading": reading, "anchors": [{"anchor": str(i), "value_R": 0.1} for i in range(30)]}
    if sanity is not None:
        r["sanity"] = {"passed": sanity}
    return r


def test_the_held_out_verdict_needs_no_real_cohort_and_the_original_still_does():
    out = judge.combine([_report(ho.SPEC, "A"), _report(ho.SPEC, "B")])
    assert out["design"] == "heldout" and out["sanity"] == "not_applicable"
    assert out["verdicts"] == {"A": "REPLAY_CONFIRMED", "B": "REPLAY_CONFIRMED"}
    original = judge.combine([_report(fr.SPEC, "A", sanity=False)])
    assert original["verdicts"] == {"A": "BLOCKED"} and original["sanity"] == "real_cohorts"
    mixed = judge.combine([_report(ho.SPEC, "A"), _report(fr.SPEC, "B")])
    assert mixed["verdict"] == "BLOCKED"
