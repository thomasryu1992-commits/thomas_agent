"""PR3a-2 — the artifact rides from the pool entry to every record its signal writes.

PR3a bound the pool entry, the approval and the arm to one hash (Thomas decisions 31-34). A trade's
records still named only the rule: candidate, rule hash and generation say which rule fired, not
which installed strategy did — its admission evidence, score and cost basis included. This pins the
artifact beside them through the paper route, plan, position and outcome, the shadow and forward
books, and the live intent. The live side past the intent is pinned beside each live door
(`..._pre_order_gate`, `..._live_route`, `..._live_leg`) and end to end in the rehearsal.

An entry that predates the artifact still papers, and its records say so: the field is present and
None. A row written before this change carries no field at all.
"""

from __future__ import annotations

from tests._helpers import stamped_pool_entry
from tests.test_mvp_runtime_crypto_paper import (
    CTX,
    NOW,
    ROW,
    ROW_NO_MATCH,
    _pool,
    _pool_entry,
    _real_store,
    _snapshot,
    _spec_dict,
    _verdict,
)

from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import counterfactual, paper
from runtime.mvp_runtime.crypto import forward_book as fb
from runtime.mvp_runtime.crypto.live_order import build_live_order_intent
from runtime.mvp_runtime.crypto.paper import (
    DryRunPaperStore,
    build_entry_plan,
    load_open_position,
    read_outcomes,
    route_entries,
    run_paper_update,
)
from runtime.mvp_runtime.crypto.strategy_artifact import ARTIFACT_SHA256_FIELD

# The candle after the entry trades through the stop: the trade settles at -1R.
_STOP_CANDLE = {"open_time": "2026-07-22T00:00:00Z", "open": 104.0, "high": 104.5, "low": 101.0,
                "close": 103.0, "volume": 9.0, "close_time": "2026-07-23T00:00:00Z"}
_NEXT = "2026-07-23T12:00:00Z"


def _entry(strategy_id="S1", *, stamped=True, **kw):
    entry = {**_pool_entry(strategy_id=strategy_id, **kw), "candidate_id": f"cand_{strategy_id}"}
    return stamped_pool_entry(entry) if stamped else entry


def _trade(entry, root):
    """Open on ROW, settle on the stop candle; what the book held while open, the open event, and
    the outcome."""
    control_store = ControlStore(root)
    _, records = run_paper_update(_snapshot(), ROW, _pool(entry), _verdict(), store=_real_store(root),
                                  now=NOW, root=root, control_store=control_store)
    position = load_open_position(CTX, root)
    [opened] = [r for r in records if r["operation"] == "open"]
    run_paper_update(_snapshot(_STOP_CANDLE), ROW_NO_MATCH, _pool(entry), _verdict(),
                     store=_real_store(root), now=_NEXT, root=root, control_store=control_store)
    [outcome] = read_outcomes(root)
    return position, opened, outcome


def test_a_paper_trade_names_its_artifact_from_the_route_to_the_outcome(tmp_path):
    entry = _entry()
    stamp = entry[ARTIFACT_SHA256_FIELD]
    assert stamp.startswith("sha256:")
    route = route_entries(_pool(entry), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["primary_strategy_artifact_sha256"] == stamp
    assert build_entry_plan(route, ROW, now=NOW)[ARTIFACT_SHA256_FIELD] == stamp

    position, opened, outcome = _trade(entry, tmp_path)
    assert position[ARTIFACT_SHA256_FIELD] == stamp
    assert opened[ARTIFACT_SHA256_FIELD] == stamp
    assert outcome[ARTIFACT_SHA256_FIELD] == stamp
    assert outcome["close_reason"] == "stop_loss"


def test_an_entry_that_predates_the_artifact_papers_as_before_and_its_records_say_so(tmp_path):
    """Decision 33: an unstamped entry papers. The stamp changes no number and no id of the trade,
    only the name its records give the strategy."""
    legacy = _trade(_entry(stamped=False), tmp_path / "legacy")
    stamped = _trade(_entry(), tmp_path / "stamped")
    for old, new in zip(legacy, stamped):
        assert ARTIFACT_SHA256_FIELD in old and old[ARTIFACT_SHA256_FIELD] is None
        assert {k: v for k, v in old.items() if k not in (ARTIFACT_SHA256_FIELD, "record_sha256")} \
            == {k: v for k, v in new.items() if k not in (ARTIFACT_SHA256_FIELD, "record_sha256")}


def test_the_bench_behind_the_primary_carries_its_own_artifact_into_the_shadow_book(tmp_path):
    weak, strong = _entry("S_weak", champion_score=0.1), _entry("S_strong", champion_score=0.9)
    assert weak[ARTIFACT_SHA256_FIELD] != strong[ARTIFACT_SHA256_FIELD]
    route = route_entries(_pool(weak, strong), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["primary_strategy_artifact_sha256"] == strong[ARTIFACT_SHA256_FIELD]
    [bench] = route["supporting"]
    assert bench[ARTIFACT_SHA256_FIELD] == weak[ARTIFACT_SHA256_FIELD]

    summary, _ = run_paper_update(_snapshot(), ROW, _pool(weak, strong), _verdict(),
                                  store=DryRunPaperStore(), now=NOW, root=tmp_path,
                                  control_store=ControlStore(tmp_path))
    [shadow] = summary["supporting_plans"]
    assert shadow["strategy_id"] == "S_weak"
    assert shadow[ARTIFACT_SHA256_FIELD] == weak[ARTIFACT_SHA256_FIELD]


def test_each_side_of_an_unresolved_conflict_carries_its_own_artifact():
    short_spec = _spec_dict(strategy_id="S_short", direction="short", entry_rules={
        "operator": "AND", "conditions": [{"feature": "adx", "comparison": ">=", "value": 20.0}],
    })
    long_e, short_e = _entry("S_long"), _entry("S_short", spec=short_spec)
    route = route_entries(_pool(long_e, short_e), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW)
    assert route["block_reason"] == paper.BLOCK_DIRECTION_CONFLICT
    assert {m["strategy_id"]: m[ARTIFACT_SHA256_FIELD] for m in route["conflict_matches"]} == {
        "S_long": long_e[ARTIFACT_SHA256_FIELD], "S_short": short_e[ARTIFACT_SHA256_FIELD]}


def test_a_shadow_position_and_its_outcome_name_the_artifact():
    entry = _entry()
    plan = build_entry_plan(route_entries(_pool(entry), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW),
                            ROW, now=NOW)
    shadow = counterfactual.build_shadow_plan(plan, block_reasons=["DAILY_LOSS_LIMIT"], now=NOW)
    assert shadow[ARTIFACT_SHA256_FIELD] == entry[ARTIFACT_SHA256_FIELD]
    outcome = counterfactual.build_counterfactual_outcome_record(
        shadow, close_reason="stop_loss", exit_price=shadow["stop_loss"], result_r=-1.0, now=_NEXT)
    assert outcome[ARTIFACT_SHA256_FIELD] == entry[ARTIFACT_SHA256_FIELD]


def test_a_forward_book_row_names_the_artifact_of_the_entry_it_traded(tmp_path):
    from tests.test_mvp_runtime_crypto_forward_book import (
        ROW as FB_ROW, ROW_NO_MATCH as FB_ROW_NO_MATCH, _candle, _pool_entry as _fb_entry, _update,
    )

    entry = stamped_pool_entry(_fb_entry())
    _update(_pool(entry), FB_ROW, _candle("2026-08-29T00:00:00Z"), tmp_path)
    [state] = fb.load_book(tmp_path)["entries"].values()
    assert state["position"][ARTIFACT_SHA256_FIELD] == entry[ARTIFACT_SHA256_FIELD]
    _update(_pool(entry), FB_ROW_NO_MATCH, _candle("2026-08-30T00:00:00Z", low=101.0), tmp_path,
            now="2026-08-30T12:00:00Z")
    [row] = fb.read_forward_outcomes(tmp_path)
    assert row[ARTIFACT_SHA256_FIELD] == entry[ARTIFACT_SHA256_FIELD]


def test_a_live_intent_names_the_plans_artifact_and_keeps_the_bars_identity():
    """The artifact is the order's lineage, not its venue identity: the same bar under another
    artifact is the same order at the venue, so a pool re-stamped between two passes cannot mint a
    second order for one bar."""
    plan = build_entry_plan(route_entries(_pool(_entry()), ROW, symbol="BTCUSDT", timeframe="1d", now=NOW),
                            ROW, now=NOW)
    intent = build_live_order_intent(plan, symbol="BTCUSDT", quantity=0.001, notional_usdt=60.0, now=NOW)
    assert intent[ARTIFACT_SHA256_FIELD] == plan[ARTIFACT_SHA256_FIELD]
    other = build_live_order_intent({**plan, ARTIFACT_SHA256_FIELD: "sha256:" + "e" * 64},
                                    symbol="BTCUSDT", quantity=0.001, notional_usdt=60.0, now=NOW)
    assert other["client_order_id"] == intent["client_order_id"]
    assert other["idempotency_key"] == intent["idempotency_key"]
