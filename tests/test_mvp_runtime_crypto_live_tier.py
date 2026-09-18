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

    observation = promotion_content_sha256(["c1"], ["h1"], False, pool_store.LIVE_TIER_OBSERVATION,
                                           artifact_sha256s=["x"])
    live = promotion_content_sha256(["c1"], ["h1"], False, pool_store.LIVE_TIER_LIVE, artifact_sha256s=["x"])
    assert observation != live


def test_an_unknown_tier_cannot_be_hashed_at_all():
    """A typo at the promotion door refuses rather than minting an approval for a tier nothing
    downstream will honour — which would read as an approved promotion that silently never arms."""
    from runtime.mvp_runtime.crypto.promotion import promotion_content_sha256
    from runtime.mvp_runtime.errors import ApprovalBlocked

    with pytest.raises(ApprovalBlocked):
        promotion_content_sha256(["c1"], ["h1"], False, "live", artifact_sha256s=["x"])


# --- the approval an entry was armed under (PR2b, decision 17) -------------------------------------

def _traded(**entry):
    """An entry that trades the spec its label names (review of #887)."""
    from runtime.mvp_runtime.crypto.strategy import StrategySpec
    from tests.test_mvp_runtime_crypto_evidence_depth import _spec_dict

    spec = StrategySpec.from_dict(_spec_dict()).to_dict()
    return {"strategy_spec": spec, "strategy_rule_hash": spec["strategy_rule_hash"], **entry}


def _stamped(entry):
    """``entry`` installed as an artifact (PR3a): only a stamped entry may be armed LIVE."""
    from tests._helpers import stamped_pool_entry

    return stamped_pool_entry(entry)


def test_the_arming_approvals_are_read_for_live_routable_entries_only():
    armed = pool_store.live_arm_approvals(_pool(
        _stamped(_entry("S1", live_tier="LIVE", live_tier_approval_id="appr_1", **_traded())),
        _stamped(_entry("S2", live_tier="LIVE", **_traded())),        # armed with no approval named
        _stamped(_entry("S3", live_tier="LIVE", live_tier_approval_id="  ", **_traded())),
        _stamped(_entry("S4", live_tier="OBSERVATION", live_tier_approval_id="appr_4", **_traded())),
        _stamped(_entry("S5", status="SUSPENDED", live_tier="LIVE", live_tier_approval_id="appr_5", **_traded())),
    ))
    assert armed == {"S1": "appr_1", "S2": None, "S3": None}
    assert set(armed) == pool_store.live_routable_strategy_ids(_pool(
        _entry("S1", live_tier="LIVE"), _entry("S2", live_tier="LIVE"), _entry("S3", live_tier="LIVE"),
        _entry("S4", live_tier="OBSERVATION"), _entry("S5", status="SUSPENDED", live_tier="LIVE"),
    ))


def test_the_arm_facts_name_the_lineage_and_install_time_of_live_routable_entries_only():
    """PR2c-2b: what the gate needs to verify an arm, for the same membership as the tier."""
    traded = _traded()
    s1 = _stamped(_entry("S1", live_tier="LIVE", live_tier_approval_id=" appr_1 ", candidate_id="c1",
                         promoted_at="2026-09-17T00:00:00Z", **traded))
    pool = _pool(
        s1,
        _entry("S2", live_tier="LIVE"),
        _entry("S4", live_tier="OBSERVATION", live_tier_approval_id="appr_4", candidate_id="c4"),
        _entry("S5", status="SUSPENDED", live_tier="LIVE", live_tier_approval_id="appr_5"),
    )
    entries = pool_store.live_arm_entries(pool)
    rule = traded["strategy_rule_hash"]
    assert entries["S1"] == {"approval_id": "appr_1", "candidate_id": "c1", "strategy_rule_hash": rule,
                             "spec_rule_hash": rule,
                             "strategy_artifact_sha256": s1["strategy_artifact_sha256"],
                             "promoted_at": "2026-09-17T00:00:00Z", "disarmed_at": None}
    assert set(entries) == {"S1", "S2"}
    assert entries["S2"]["approval_id"] is None and entries["S2"]["spec_rule_hash"] is None
    # An entry that predates the artifact names none (PR3a).
    assert entries["S2"]["strategy_artifact_sha256"] is None
    assert pool_store.live_arm_approvals(pool) == {"S1": "appr_1", "S2": None}


@pytest.mark.parametrize("change,unsound", [
    ({}, None),
    ({"strategy_rule_hash": "h_other"}, "spec"),                     # the label names another rule
    ({"strategy_rule_hash": None}, "spec"),
    ({"strategy_spec": {"x": 1}}, "spec"),                          # a spec that does not parse
    ({"strategy_spec": None}, "spec"),
    ({"strategy_spec": None, "strategy_rule_hash": None}, "spec"),  # nothing to compare is no rule
    ({"live_tier_updated_at": "2026-09-17T00:00:00Z"}, "disarmed"),  # put back in the tier by hand
    # PR3a, decision 33: an entry the door did not install as an artifact arms nothing.
    ({"strategy_artifact_sha256": None}, "unbound"),
    ({"strategy_artifact_sha256": ""}, "unbound"),
    # Both: the hand edit is named, not the missing stamp (PR3a review).
    ({"strategy_artifact_sha256": None, "live_tier_updated_at": "2026-09-17T00:00:00Z"}, "disarmed"),
], ids=["sound", "label", "no-label", "garbled-spec", "no-spec", "neither", "disarm-trace",
        "no-stamp", "empty-stamp", "no-stamp-and-disarm-trace"])
def test_an_unsound_arm_names_no_approval_whatever_it_carries(change, unsound):
    """Review of #887: the router trades the spec and the approval is checked against the label, so
    they must be one rule; and the promotion door never installs an entry carrying the disarm trace."""
    entry = _entry("S1", live_tier="LIVE", live_tier_approval_id="appr_1", **_traded())
    # Stamped before the change, so the change is one the stamp did not see: a pool READ would
    # refuse the whole pool for it (decision 34), and this pins the arm's own verdict beside that.
    pool = _pool({**_stamped(entry), **change})
    [armed] = pool_store.live_arm_entries(pool).values()
    assert pool_store.live_arm_unsound(armed) == unsound
    assert pool_store.live_arm_approvals(pool) == {"S1": None if unsound else "appr_1"}


def test_a_spec_swapped_under_its_label_is_seen_even_without_its_own_hash():
    """The spec's own hash is checked only when present; its absence must not hide a swap."""
    from runtime.mvp_runtime.crypto.strategy import StrategySpec
    from tests.test_mvp_runtime_crypto_evidence_depth import _spec_dict

    traded = _traded()
    other = StrategySpec.from_dict(_spec_dict(direction="short")).to_dict()
    other.pop("strategy_rule_hash", None)
    pool = _pool({**_stamped(_entry("S1", live_tier="LIVE", live_tier_approval_id="appr_1", **traded)),
                  "strategy_spec": other})
    [armed] = pool_store.live_arm_entries(pool).values()
    assert armed["spec_rule_hash"] != traded["strategy_rule_hash"]
    assert pool_store.live_arm_approvals(pool) == {"S1": None}


def test_the_disarm_door_leaves_the_trace_an_unsound_arm_is_read_by(tmp_path):
    from runtime.mvp_runtime.crypto.strategy import StrategySpec
    from tests.test_mvp_runtime_crypto_evidence_depth import _spec_dict

    spec = StrategySpec.from_dict(_spec_dict()).to_dict()
    pool_store.install_active_pool({"active_strategies": [_stamped(
        {"strategy_id": "S1", "status": "PAPER_ACTIVE", "strategy_spec": spec, "candidate_id": "c1",
         "strategy_rule_hash": spec["strategy_rule_hash"], "live_tier": "LIVE",
         "live_tier_approval_id": "appr_1"}),
    ]}, root=tmp_path)
    assert pool_store.live_arm_approvals(pool_store.load_active_pool(tmp_path)) == {"S1": "appr_1"}
    pool_store.disarm_live_tier(["S1"], root=tmp_path, now="2026-09-17T00:00:00Z")
    pool = pool_store.load_active_pool(tmp_path)
    # A hand edit that puts the tier and the id back is still an arm with no approval.
    pool["active_strategies"][0].update({"live_tier": "LIVE", "live_tier_approval_id": "appr_1"})
    assert pool_store.live_arm_approvals(pool) == {"S1": None}


def test_disarming_takes_the_approval_with_the_tier(tmp_path):
    from runtime.mvp_runtime.crypto.strategy import StrategySpec
    from tests.test_mvp_runtime_crypto_evidence_depth import _spec_dict

    spec = StrategySpec.from_dict(_spec_dict()).to_dict()
    pool_store.install_active_pool({"active_strategies": [
        {"strategy_id": "S1", "status": "PAPER_ACTIVE", "strategy_spec": spec, "candidate_id": "c1",
         "live_tier": "LIVE", "live_tier_approval_id": "appr_1"},
    ]}, root=tmp_path)
    assert pool_store.disarm_live_tier(["S1"], root=tmp_path, now="2026-09-17T00:00:00Z") == 1
    [entry] = pool_store.load_active_pool(tmp_path)["active_strategies"]
    assert entry["live_tier"] == "OBSERVATION"
    assert pool_store.LIVE_TIER_APPROVAL_FIELD not in entry


def _promote(tmp_path, monkeypatch, **kw):
    from scripts import promote_strategy_candidates as prom
    from tests.test_mvp_runtime_crypto_evidence_depth import NOW, _TODAY_1D, _seed

    _seed(tmp_path, bars=_TODAY_1D)
    # The approval's own verification and the quality gates have their tests; this pins what the
    # door writes once they have passed.
    # What a verified approval binds (PR3a): the door re-hashes the rows it copies against it.
    monkeypatch.setattr(prom.promotion_mod, "verify_promotion_approval", lambda *a, **k: {
        "approval_id": kw.get("approval_id"),
        "approved_action_snapshot": {"content_sha256": prom.promotion_mod.content_sha256_of(
            pool_store.resolve_candidates(k["selectors"], k["root"]), keep_active=k["keep_active"],
            live_tier=k["live_tier"], root=k["root"])},
    })
    monkeypatch.setattr(prom.promotion_mod, "run_promotion_gates", lambda *a, **k: None)
    prom.run_promotion(selectors=["S1"], promoted_by="Thomas", reason="r", keep_active=False,
                       root=tmp_path, now=NOW, **kw)
    [entry] = pool_store.load_active_pool(tmp_path)["active_strategies"]
    return entry


def test_a_live_install_records_the_approval_it_was_armed_under(tmp_path, monkeypatch):
    entry = _promote(tmp_path, monkeypatch, live_tier="LIVE", approval_id="appr_live")
    assert entry["live_tier"] == "LIVE"
    assert entry[pool_store.LIVE_TIER_APPROVAL_FIELD] == "appr_live"
    assert pool_store.live_arm_approvals({"active_strategies": [entry]}) == {entry["strategy_id"]: "appr_live"}


@pytest.mark.parametrize("kw", [
    {"live_tier": "OBSERVATION", "approval_id": "appr_obs"},
    {"live_tier": "OBSERVATION", "without_approval": True},
])
def test_an_observation_install_names_no_arming_approval(tmp_path, monkeypatch, kw):
    entry = _promote(tmp_path, monkeypatch, **kw)
    assert entry["live_tier"] == "OBSERVATION"
    assert pool_store.LIVE_TIER_APPROVAL_FIELD not in entry
