"""Occupying a routing slot and being allowed to spend real money are two different facts.

They were one until #610 Part 1. `live_entry`'s check order — route, verdict, reconciliation,
capacity, filters, bracket, economics, sizing, guard — contains no per-strategy question at all;
slot 2b was Gate 0 and was removed 2026-08-03 for being unsatisfiable. Every gate left asks
whether this RUNTIME may trade. So installing a strategy into the pool armed it for real orders
on the next 15-minute cycle, and on 2026-08-08 five strategies were live-routing on evidence the
current rule recomputes to INSUFFICIENT.

Two properties are pinned here, and the second is the one that makes the first durable:

1. an entry refuses unless the pool says this strategy is armed, and refuses when it cannot tell;
2. **nothing but the operator door can arm one** — in particular the lifecycle ladder cannot,
   which is why the tier is a field and not a status.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_entry as le
from runtime.mvp_runtime.crypto import pool as pool_store


def _entry(strategy_id="S1", status="PAPER_ACTIVE", **extra):
    return {"strategy_id": strategy_id, "status": status, "strategy_spec": {"x": 1}, **extra}


def _pool(*entries):
    return {"active_strategies": list(entries)}


# --- the tier itself ----------------------------------------------------------

def test_absence_reads_as_observation():
    """Every entry promoted before this existed. None of them may spend money until somebody
    re-promotes it into the live tier — the migration is the default, not a script."""
    assert pool_store.entry_live_tier(_entry()) == pool_store.LIVE_TIER_OBSERVATION
    assert pool_store.live_routable_strategy_ids(_pool(_entry())) == set()


@pytest.mark.parametrize("value", ["live", "Live", "LIVE_TIER_LIVE", "", None, 1, True, "YES"])
def test_only_the_exact_live_value_arms_a_strategy(value):
    """A tier this code does not recognise is not a reason to allow real money. Includes the
    near-misses a hand-edited pool file would produce."""
    assert pool_store.entry_live_tier(_entry(live_tier=value)) == pool_store.LIVE_TIER_OBSERVATION


def test_the_live_value_arms_it():
    entry = _entry(live_tier=pool_store.LIVE_TIER_LIVE)
    assert pool_store.entry_live_tier(entry) == pool_store.LIVE_TIER_LIVE
    assert pool_store.live_routable_strategy_ids(_pool(entry)) == {"S1"}


def test_the_live_set_is_strictly_narrower_than_the_routable_one():
    """The two answer different questions and must not be conflated: "could this trade again at
    all" (which the drawdown baseline needs, and which an observation-tier strategy answers YES
    to) versus "may this spend money"."""
    pool = _pool(
        _entry("S1", live_tier=pool_store.LIVE_TIER_LIVE),
        _entry("S2"),
        _entry("S3", status="WARNING", live_tier=pool_store.LIVE_TIER_LIVE),
    )
    assert pool_store.routable_strategy_ids(pool) == {"S1", "S2", "S3"}
    assert pool_store.live_routable_strategy_ids(pool) == {"S1", "S3"}


def test_a_terminal_status_is_not_live_routable_whatever_its_tier():
    """The tier grants nothing on its own — it narrows the occupying set, never widens it. A
    SUSPENDED entry carrying LIVE is a stale field, not a permission."""
    for status in ("SUSPENDED", "ARCHIVED"):
        pool = _pool(_entry(status=status, live_tier=pool_store.LIVE_TIER_LIVE))
        assert pool_store.live_routable_strategy_ids(pool) == set(), status


# --- the door in live_entry ---------------------------------------------------

def _decision(**kw):
    from tests.test_mvp_runtime_crypto_live_entry import _plan  # the door-by-door helper

    return _plan(**kw)


def test_an_unarmed_strategy_is_refused():
    d = _decision(live_routable_strategy_ids=set())
    assert d["status"] == le.STATUS_REFUSED
    assert le.NOT_LIVE_ROUTABLE in d["reasons"]


def test_an_unknown_live_set_is_refused_and_says_so_differently():
    """`None` is "the pool could not be read", which is a fault whose fix is elsewhere. Empty is
    "nothing is armed", which is the expected steady state. Same refusal, different code, because
    an operator chasing one should not be handed the other."""
    d = _decision(live_routable_strategy_ids=None)
    assert d["status"] == le.STATUS_REFUSED
    assert le.LIVE_TIER_UNKNOWN in d["reasons"]
    assert le.NOT_LIVE_ROUTABLE not in d["reasons"]


def test_an_armed_strategy_passes_this_door():
    d = _decision()  # helper arms the plan's own strategy
    assert le.NOT_LIVE_ROUTABLE not in d["reasons"]
    assert le.LIVE_TIER_UNKNOWN not in d["reasons"]


def test_a_strategy_with_no_route_is_reported_as_no_route_not_as_a_permission_problem():
    """Ordering: the tier door sits after the plan check, so the absence of a signal never reads
    as a governance refusal. An operator seeing NOT_LIVE_ROUTABLE should be able to trust that a
    strategy actually wanted to trade."""
    d = _decision(plan=None, live_routable_strategy_ids=set())
    assert d["status"] == le.STATUS_NO_ROUTE
    assert d["reasons"] == [le.NO_PLAN]


def test_the_refusal_accumulates_with_the_other_cheap_doors():
    """An operator sees every closed door at once rather than fixing them one deploy at a time."""
    d = _decision(live_routable_strategy_ids=set(), verdict={"allow_new_position": False})
    assert le.NOT_LIVE_ROUTABLE in d["reasons"] and le.VERDICT_REFUSED in d["reasons"]


# --- the property that makes it durable ---------------------------------------

def test_the_lifecycle_ladder_cannot_arm_a_strategy():
    """The reason the tier is a FIELD and not a status.

    `lifecycle` recovers a WARNING strategy back to PAPER_ACTIVE. Had the observation tier been a
    status, that recovery would move a strategy the operator kept off the money path INTO it —
    an automatic promotion into real money, arriving through the one mechanism this system says
    may only ever demote. `update_statuses` writes only `status`, so the ladder cannot reach the
    tier at all. Asserted against the real function's contract rather than by inspection."""
    import inspect

    from runtime.mvp_runtime.crypto import lifecycle

    # The ladder's whole output vocabulary, and the tier is not in it.
    ladder_source = inspect.getsource(lifecycle)
    assert pool_store.LIVE_TIER_FIELD not in ladder_source, (
        "lifecycle now mentions the live tier — if it can write it, a demotion path can arm a "
        "strategy, which is the failure this split exists to make impossible"
    )
    # And the writer it feeds says the same thing from the other side.
    assert pool_store.LIVE_TIER_FIELD not in inspect.getsource(pool_store.update_statuses)


def test_the_promotion_hash_separates_the_two_asks():
    """An approval granted for an observation-tier install must not be spendable on a live one.
    Same candidates, same rules, same add/replace — different money."""
    from runtime.mvp_runtime.crypto.promotion import promotion_content_sha256

    observation = promotion_content_sha256(["c1"], ["h1"], False, pool_store.LIVE_TIER_OBSERVATION)
    live = promotion_content_sha256(["c1"], ["h1"], False, pool_store.LIVE_TIER_LIVE)
    assert observation != live


def test_an_unknown_tier_cannot_be_hashed_at_all():
    """A typo at the promotion door refuses rather than minting an approval for a tier nothing
    downstream will honour — which would read as an approved promotion that silently never arms."""
    from runtime.mvp_runtime.crypto.promotion import promotion_content_sha256
    from runtime.mvp_runtime.errors import ApprovalBlocked

    with pytest.raises(ApprovalBlocked):
        promotion_content_sha256(["c1"], ["h1"], False, "live")
