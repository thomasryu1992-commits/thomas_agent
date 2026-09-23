"""PR3c-2 — the same rule is the same strategy, at the promotion door (Thomas decisions 40 and 42).

The candidate store re-scores a rule under a new candidate id (a new generation, a new evidence
window), and every identity check the door had compared candidate ids. A rule the pool routes
could be installed a second time, and a retired rule could come back under a new id as a fresh
hypothesis. On 2026-09-10 only a display-id collision stopped one; replayed on 2026-09-19, of 70
rules the pool holds with another id in the store, none could be installed that way that day, but
only because another gate or a collision happened to refuse each.

- A rule the pool routes (any status but SUSPENDED/ARCHIVED) is never installed under another
  candidate, and one batch never carries a rule twice: ``POOL_RULE_ALREADY_ROUTED``, no escape, at
  the ask and at the install.
- A rule only retired entries hold returns as a REACTIVATION: named in the approval (its content
  hash and its signed parameters), refused without ``--allow-reactivation``, and the new entry
  replaces those entries — their display ids with them — and inherits their record (decision 41,
  PR3c-1). The ledger keeps who they were and why they had been retired.
- The pool read does not enforce one entry per rule: S008 and S008-GEN-696 hold one rule today,
  both retired, and stay (decision 42); a promotion of their rule replaces both.
"""

from __future__ import annotations

import json

import pytest

import runtime.mvp_runtime.crypto.promotion as promotion_mod
from runtime.mvp_runtime.crypto import pool
from runtime.mvp_runtime.crypto import strategy_artifact as artifact_mod
from runtime.mvp_runtime.crypto.candidate_identity import PREDECESSOR_KEYS_FIELD, entry_attribution_keys, lineage_of
from runtime.mvp_runtime.crypto.promotion import promotion_content_sha256, request_promotion
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.errors import ApprovalBlocked
from scripts.promote_strategy_candidates import run_promotion
from tests._helpers import requires_local_core
from tests.test_mvp_runtime_crypto_promotion import (  # noqa: F401 — the autouse stage fixture
    NOW, _machine_is_staged_for_arming, _seed_candidates, _spec_dict,
)

EVERY_ESCAPE = dict(allow_stale_cost_basis=True, allow_unrecorded_evidence_depth=True, allow_duplicates=True,
                    allow_cluster_siblings=True, allow_below_entry_bar=True, allow_family_overflow=True,
                    allow_unconfirmed_holdout=True, allow_oversized_pool=True,
                    allow_quarantined_derivation=True, allow_unstamped_record=True, allow_reactivation=True)


def _seed(tmp_path, *, generation, spec=None):
    """One candidate row of ``spec`` (default: the S1 rule) minted in ``generation``: the same rule
    in another generation is another candidate id."""
    [row] = _seed_candidates(tmp_path, spec or _spec_dict(), generation_id=generation)
    return {**row, "candidate_id": pool.candidate_id(row)}


def _promote(tmp_path, *rows, keep_active=True, **escapes):
    return run_promotion(selectors=[r["candidate_id"] for r in rows], promoted_by="Thomas", reason="r",
                         keep_active=keep_active, live_tier="OBSERVATION", root=tmp_path, now=NOW,
                         without_approval=True, **escapes)


def _entries(tmp_path):
    return pool.load_active_pool(tmp_path)["active_strategies"]


def _set_status(tmp_path, strategy_id, status, **fields):
    """Move one entry's status as the lifecycle or an operator would (status is not in the stamp)."""
    installed = pool.load_active_pool(tmp_path)
    for entry in installed["active_strategies"]:
        if entry["strategy_id"] == strategy_id:
            entry.update(status=status, **fields)
    pool.install_active_pool(installed, root=tmp_path)


def _retire(tmp_path, strategy_id="S1"):
    _set_status(tmp_path, strategy_id, "SUSPENDED", lifecycle_reasons=["operator_retired"],
                lifecycle_retired_by="Thomas")


# --- a rule the pool routes -----------------------------------------------------------------------

@pytest.mark.parametrize("status", ["PAPER_ACTIVE", "WARNING", "PROBATION", "A_STATUS_NOBODY_WROTE"])
@pytest.mark.parametrize("keep_active", [True, False], ids=["add", "replace"])
def test_a_rule_the_pool_routes_is_never_installed_under_another_candidate(tmp_path, status, keep_active):
    """Every escape set, and still refused: which strategy a candidate is, is not an operator's call.
    A status this code does not know counts as routed."""
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _set_status(tmp_path, "S1", status)
    twin = _seed(tmp_path, generation="GEN-002")
    before = _entries(tmp_path)
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, twin, keep_active=keep_active, **EVERY_ESCAPE)
    assert "POOL_RULE_ALREADY_ROUTED" in str(refused.value) and first["candidate_id"] in str(refused.value)
    assert _entries(tmp_path) == before


def test_one_rule_twice_in_one_batch_is_refused(tmp_path):
    rows = [_seed(tmp_path, generation="GEN-001"), _seed(tmp_path, generation="GEN-002")]
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, *rows, keep_active=False, **EVERY_ESCAPE)
    assert "POOL_RULE_ALREADY_ROUTED" in str(refused.value)
    assert _entries(tmp_path) == []


def test_a_rule_is_its_spec_not_only_its_label(tmp_path):
    """An entry whose label parted from its spec still holds the spec's rule."""
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    installed = pool.load_active_pool(tmp_path)
    installed["active_strategies"][0]["strategy_rule_hash"] = "a-label-that-parted"
    installed["active_strategies"][0].pop(artifact_mod.ARTIFACT_SHA256_FIELD)   # an unstamped entry
    installed["active_strategies"][0].pop(artifact_mod.ARTIFACT_FIELD)
    pool.install_active_pool(installed, root=tmp_path)
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, _seed(tmp_path, generation="GEN-002"))
    assert "POOL_RULE_ALREADY_ROUTED" in str(refused.value)


def test_a_routed_rule_is_refused_as_what_it_is_not_as_a_display_id_collision(tmp_path):
    """"S1" routes the rule and "S1-GEN-002" is taken by another rule, so the twin's display id cannot
    be assigned. The door says the rule is routed — the refusal an operator can act on — before it
    tries a display id (2026-09-10 read as a collision)."""
    routed = _seed(tmp_path, generation="GEN-001")
    other_rule = _seed(tmp_path, generation="GEN-002", spec=_spec_dict(entry_rules={
        "operator": "AND", "conditions": [{"feature": "close", "comparison": ">", "value": 1.0}]}))
    pool.install_active_pool({"active_strategies": [
        _hand_entry(routed, strategy_id="S1", status="PAPER_ACTIVE"),
        _hand_entry(other_rule, strategy_id="S1-GEN-002", status="SUSPENDED")]}, root=tmp_path)
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, _seed(tmp_path, generation="GEN-002"), **EVERY_ESCAPE)
    assert "POOL_RULE_ALREADY_ROUTED" in str(refused.value)
    assert "cannot assign a unique strategy_id" not in str(refused.value)


def test_the_ask_refuses_a_routed_rule_too(tmp_path):
    """The roster runs it at both doors, so an ask cannot win an answer the install would refuse.
    No `requires_local_core`: the refusal lands in the roster before the ask binds a Core."""
    _promote(tmp_path, _seed(tmp_path, generation="GEN-001"))
    twin = _seed(tmp_path, generation="GEN-002")
    with pytest.raises(ApprovalBlocked) as refused:
        request_promotion([twin["candidate_id"]], keep_active=True, live_tier="OBSERVATION", now=NOW,
                          candidates_root=tmp_path, **EVERY_ESCAPE)
    assert refused.value.reason_code == pool.POOL_RULE_ALREADY_ROUTED


def test_a_new_rule_promotes_as_before(tmp_path):
    """The inverse pin: nothing is replaced, nothing inherited, nothing reactivated."""
    summary = _promote(tmp_path, _seed(tmp_path, generation="GEN-001"))
    [entry] = _entries(tmp_path)
    assert PREDECESSOR_KEYS_FIELD not in entry and "lifecycle_consecutive_failures" not in entry
    assert summary["replaced_entries"] == [] and summary["reactivated"] == []


# --- a rule only retired entries hold -------------------------------------------------------------

def test_a_retired_rule_returns_only_as_a_reactivation(tmp_path):
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _retire(tmp_path)
    before = _entries(tmp_path)
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, _seed(tmp_path, generation="GEN-002"))
    assert "POOL_SILENT_REACTIVATION" in str(refused.value)
    assert f"replaces S1 [{first['candidate_id']}]" in str(refused.value)
    assert _entries(tmp_path) == before


def test_the_returning_rule_replaces_the_retired_entry_and_inherits_its_record(tmp_path):
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _retire(tmp_path)
    [retired] = _entries(tmp_path)
    twin = _seed(tmp_path, generation="GEN-002")
    summary = _promote(tmp_path, twin, allow_reactivation=True)

    [entry] = _entries(tmp_path)                                  # one rule, one entry
    assert entry["candidate_id"] == twin["candidate_id"] and entry["status"] == "PAPER_ACTIVE"
    assert entry["strategy_id"] == "S1"                           # the display id left with its entry
    rule = first["strategy_rule_hash"]
    assert entry[PREDECESSOR_KEYS_FIELD] == [f"cand:{first['candidate_id']}", f"gen:GEN-001:{rule}"]
    assert f"cand:{first['candidate_id']}" in entry_attribution_keys(entry)
    assert summary["replaced_entries"] == [{
        "replaced_by": twin["candidate_id"], "strategy_id": "S1", "candidate_id": first["candidate_id"],
        "status": "SUSPENDED", "lineage_keys": entry[PREDECESSOR_KEYS_FIELD],
        "lifecycle_reasons": ["operator_retired"], "lifecycle_retired_by": "Thomas",
    }]
    [returned] = summary["reactivated"]
    assert returned["from_status"] == "SUSPENDED"
    assert returned["replaces"] == [{"strategy_id": "S1", "candidate_id": first["candidate_id"],
                                     "status": "SUSPENDED", "lineage": f"cand:{first['candidate_id']}",
                                     "lifecycle_reasons": ["operator_retired"]}]
    assert "silent_reactivation" in summary["reviews_skipped"]
    assert retired["candidate_id"] not in {e["candidate_id"] for e in _entries(tmp_path)}


def test_replace_mode_records_what_a_returning_rule_replaced(tmp_path):
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _retire(tmp_path)
    twin = _seed(tmp_path, generation="GEN-002")
    _promote(tmp_path, twin, keep_active=False, allow_reactivation=True)
    [entry] = _entries(tmp_path)
    assert f"cand:{first['candidate_id']}" in entry[PREDECESSOR_KEYS_FIELD]


def test_a_restate_keeps_what_an_entry_inherited(tmp_path):
    """Review of PR3c-1: replace mode rebuilds every entry from its candidate row, which carries no
    inheritance, and arming a successor LIVE goes through such a restate. Its record survives it."""
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _retire(tmp_path)
    twin = _seed(tmp_path, generation="GEN-002")
    _promote(tmp_path, twin, allow_reactivation=True)
    [entry] = _entries(tmp_path)
    _promote(tmp_path, twin, keep_active=False)                  # re-list it alone
    [restated] = _entries(tmp_path)
    assert restated[PREDECESSOR_KEYS_FIELD] == entry[PREDECESSOR_KEYS_FIELD]
    assert f"cand:{first['candidate_id']}" in restated[PREDECESSOR_KEYS_FIELD]


def test_a_returning_rule_keeps_the_longest_failure_streak(tmp_path):
    """Review of PR3c-1: a rule the lifecycle suspended after three failing evaluations comes back
    at three, not at nothing — the next failing evaluation suspends it again, as it would have."""
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _set_status(tmp_path, "S1", "SUSPENDED", lifecycle_consecutive_failures=3,
                lifecycle_reasons=["suspend_conditions_met"])
    _promote(tmp_path, _seed(tmp_path, generation="GEN-002"), allow_reactivation=True)
    [entry] = _entries(tmp_path)
    assert entry["lifecycle_consecutive_failures"] == 3


def test_a_chain_of_returns_inherits_every_predecessor(tmp_path):
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _retire(tmp_path)
    second = _seed(tmp_path, generation="GEN-002")
    _promote(tmp_path, second, allow_reactivation=True)
    _retire(tmp_path)
    third = _seed(tmp_path, generation="GEN-003")
    _promote(tmp_path, third, allow_reactivation=True)
    [entry] = _entries(tmp_path)
    assert {f"cand:{first['candidate_id']}", f"cand:{second['candidate_id']}"} <= set(entry[PREDECESSOR_KEYS_FIELD])


def _hand_entry(row, *, strategy_id, status):
    """A pool entry of ``row`` as an older door would have installed it: unstamped."""
    return {"strategy_id": strategy_id, "candidate_id": row["candidate_id"], "status": status,
            "champion_score": 0.5, "strategy_rule_hash": row["strategy_rule_hash"],
            "generation_id": row["generation_id"], "strategy_spec": row["strategy_spec"]}


def test_a_rule_installed_twice_returns_once_replacing_both(tmp_path):
    """The host's S008 / S008-GEN-696 shape (decision 42): the pool still loads, and the rule's
    return replaces both entries and inherits both records."""
    a = _seed(tmp_path, generation="GEN-001")
    b = _seed(tmp_path, generation="GEN-002")
    pool.install_active_pool({"active_strategies": [
        _hand_entry(a, strategy_id="S1", status="SUSPENDED"),
        _hand_entry(b, strategy_id="S1-GEN-002", status="SUSPENDED")]}, root=tmp_path)
    assert len(_entries(tmp_path)) == 2
    back = _seed(tmp_path, generation="GEN-003")
    summary = _promote(tmp_path, back, allow_reactivation=True)
    [entry] = _entries(tmp_path)
    assert {f"cand:{a['candidate_id']}", f"cand:{b['candidate_id']}"} <= set(entry[PREDECESSOR_KEYS_FIELD])
    assert sorted(r["strategy_id"] for r in summary["replaced_entries"]) == ["S1", "S1-GEN-002"]


def test_a_retired_rule_its_display_id_once_blocked_now_returns_as_a_reactivation(tmp_path):
    """The 2026-09-10 shape, replayed on the host on 2026-09-19 as cand_d3556f0f1a137ede74c9: "S1" is
    another rule, the retired twin holds "S1-GEN-002", and the candidate is the twin's rule in the
    same generation (another evidence window). The door used to refuse it on the display id alone;
    now it is what it is — a reactivation — and the twin's display id leaves with the twin."""
    other_rule = _seed(tmp_path, generation="GEN-001",
                       spec=_spec_dict(entry_rules={"operator": "AND", "conditions": [
                           {"feature": "close", "comparison": ">", "value": 1.0}]}))
    twin = _seed(tmp_path, generation="GEN-002")
    pool.install_active_pool({"active_strategies": [
        _hand_entry(other_rule, strategy_id="S1", status="PAPER_ACTIVE"),
        _hand_entry(twin, strategy_id="S1-GEN-002", status="SUSPENDED")]}, root=tmp_path)
    spec = StrategySpec.from_dict(_spec_dict())
    pool.append_candidates([{**{k: v for k, v in twin.items() if k != "candidate_id"},
                             "evidence_input_sha256": "sha256:another-window"}], root=tmp_path)
    rescored = next(r for r in pool.read_candidates(tmp_path)
                    if r.get("evidence_input_sha256") == "sha256:another-window")
    rescored = {**rescored, "candidate_id": pool.candidate_id(rescored)}
    assert rescored["strategy_rule_hash"] == spec.strategy_rule_hash == twin["strategy_rule_hash"]
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, rescored)
    assert "POOL_SILENT_REACTIVATION" in str(refused.value)
    _promote(tmp_path, rescored, allow_reactivation=True)
    by_id = {e["strategy_id"]: e for e in _entries(tmp_path)}
    assert by_id["S1-GEN-002"]["candidate_id"] == rescored["candidate_id"]
    # Its own generation key is its predecessor's too: one owner, and the pool loads.
    assert f"gen:GEN-002:{spec.strategy_rule_hash}" in by_id["S1-GEN-002"][PREDECESSOR_KEYS_FIELD]


# --- the approval names it ------------------------------------------------------------------------

def _retired_twin(tmp_path):
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _retire(tmp_path)
    return first, _seed(tmp_path, generation="GEN-002")


def test_the_hash_names_the_retired_lineage_a_rule_returns_from(tmp_path):
    """An approval asked before PR3c-2 named nothing returning in add mode; the same promotion now
    names the retired lineage, so such an approval does not verify (fail-closed, both ways)."""
    first, twin = _retired_twin(tmp_path)
    returned = pool.reactivated_candidate_ids([twin["candidate_id"]], keep_active=True, root=tmp_path,
                                              candidates=[twin])
    assert returned == [f"cand:{first['candidate_id']}"]
    now_hash = promotion_mod.content_sha256_of([twin], keep_active=True, live_tier="OBSERVATION", root=tmp_path)
    before = promotion_content_sha256([twin["candidate_id"]], [twin["strategy_rule_hash"]], True, "OBSERVATION",
                                      [], artifact_sha256s=promotion_mod.candidate_artifacts([twin]))
    assert now_hash != before


def test_the_hash_input_and_the_door_guard_name_the_same_return(tmp_path):
    _first, twin = _retired_twin(tmp_path)
    by_hash = pool.reactivated_candidate_ids([twin["candidate_id"]], keep_active=True, root=tmp_path,
                                             candidates=[twin])
    predicted = promotion_mod.predicted_pool_entries([twin], keep_active=True, live_tier="OBSERVATION",
                                                     root=tmp_path)
    by_guard = [r["lineage"] for f in pool.silent_reactivations(predicted, root=tmp_path)
                for r in f.get("replaces", [])]
    assert by_hash == by_guard


def test_the_ask_predicts_the_replacement(tmp_path):
    """The shape gates judge the pool the install will write: the retired twin leaves it."""
    first, twin = _retired_twin(tmp_path)
    predicted = promotion_mod.predicted_pool_entries([twin], keep_active=True, live_tier="OBSERVATION",
                                                     root=tmp_path)
    assert [e["candidate_id"] for e in predicted] == [twin["candidate_id"]]
    assert predicted[0]["strategy_rule_hash"] == twin["strategy_rule_hash"]


def test_the_ask_refuses_a_return_it_was_not_told_to_allow(tmp_path):
    _first, twin = _retired_twin(tmp_path)
    with pytest.raises(ApprovalBlocked) as refused:
        request_promotion([twin["candidate_id"]], keep_active=True, live_tier="OBSERVATION", now=NOW,
                          candidates_root=tmp_path)
    assert refused.value.reason_code == "POOL_SILENT_REACTIVATION"


def _pool_at(monkeypatch, tmp_path):
    """The ask reads its reactivation set from the repo root, where the install door reads the pool
    in production (one root). These tests keep their pool beside their candidates, so point that one
    read there — the candidate rows still come from the ask, so a door that stops passing them fails."""
    real = pool.reactivated_candidate_ids
    monkeypatch.setattr(pool, "reactivated_candidate_ids",
                        lambda ids, *, keep_active, root=None, candidates=(): real(
                            ids, keep_active=keep_active, root=tmp_path, candidates=candidates))


@requires_local_core
def test_the_ask_says_what_returns_and_signs_it(tmp_path, monkeypatch):
    first, twin = _retired_twin(tmp_path)
    _pool_at(monkeypatch, tmp_path)
    prepared = request_promotion([twin["candidate_id"]], keep_active=True, live_tier="OBSERVATION", now=NOW,
                                 candidates_root=tmp_path, allow_reactivation=True)
    decision = prepared["permission_decision"]
    params = decision["fingerprint_payload"]["normalized_parameters"]
    assert params["reactivated_lineages"] == [f"cand:{first['candidate_id']}"]
    assert any("RETURNS RETIRED STRATEGIES TO TRADING" in r for r in decision["risk"]["risk_reasons"])
    assert prepared["approval_request"]["approved_action_snapshot"]["content_sha256"] == \
        promotion_mod.content_sha256_of([twin], keep_active=True, live_tier="OBSERVATION", root=tmp_path)


@requires_local_core
def test_an_ordinary_ask_signs_what_it_always_signed(tmp_path, monkeypatch):
    row = _seed(tmp_path, generation="GEN-001")
    _pool_at(monkeypatch, tmp_path)
    prepared = request_promotion([row["candidate_id"]], keep_active=True, live_tier="OBSERVATION", now=NOW,
                                 candidates_root=tmp_path)
    decision = prepared["permission_decision"]
    assert "reactivated_lineages" not in decision["fingerprint_payload"]["normalized_parameters"]
    assert not any("RETURNS RETIRED" in r for r in decision["risk"]["risk_reasons"])


def test_the_ledger_keeps_the_replaced_entry(tmp_path):
    """The replaced entry leaves the pool; the promotion event on the control ledger is where its
    identity and its retirement reason stay."""
    first, twin = _retired_twin(tmp_path)
    _promote(tmp_path, twin, allow_reactivation=True)
    from runtime.mvp_runtime.store import LEDGER_REL

    control = [json.loads(line) for line in (tmp_path / LEDGER_REL / "control_events.jsonl").read_text(
        encoding="utf-8").splitlines() if line.strip()]
    [event] = [e for e in control if e.get("replaced_entries")]
    assert event["replaced_entries"][0]["candidate_id"] == first["candidate_id"]
    assert event["replaced_entries"][0]["lifecycle_reasons"] == ["operator_retired"]


def test_the_lifecycle_judges_the_returned_rule_on_the_retired_record(tmp_path):
    """End to end with PR3c-1: the retired lineage's losses judge the entry that replaced it."""
    from runtime.mvp_runtime.crypto.lifecycle import run_lifecycle

    first, twin = _retired_twin(tmp_path)
    _promote(tmp_path, twin, allow_reactivation=True)
    losses = [{"outcome_closed": True, "result_R": -0.1, "candidate_id": first["candidate_id"],
               "strategy_id": "S1", "created_at_utc": f"2026-09-{1 + i % 17:02d}T00:00:00Z"} for i in range(30)]
    [decision] = run_lifecycle(pool.load_active_pool(tmp_path), losses, now=NOW)
    assert decision["status_changed"] is True
    assert decision["candidate_id"] == twin["candidate_id"]
    assert lineage_of(_entries(tmp_path)[0])["candidate_id"] == twin["candidate_id"]


# --- review of PR3c-2 -----------------------------------------------------------------------------

def test_a_restate_of_a_routed_entry_returns_nothing(tmp_path):
    """A routed entry and a retired twin of its rule (a shape older doors could leave). Restating the
    routed one in replace mode brings nothing back: the hash names no return, no escape is needed,
    it inherits nothing, and the twin goes as replace mode has always taken what it does not
    re-list. The ask once named the twin as "returning" in front of the real-money warning."""
    routed = _seed(tmp_path, generation="GEN-001")
    twin = _seed(tmp_path, generation="GEN-002")
    pool.install_active_pool({"active_strategies": [
        _hand_entry(routed, strategy_id="S1", status="PAPER_ACTIVE"),
        _hand_entry(twin, strategy_id="S1-GEN-002", status="SUSPENDED")]}, root=tmp_path)
    assert pool.reactivated_candidate_ids([routed["candidate_id"]], keep_active=False, root=tmp_path,
                                          candidates=[routed]) == []
    assert promotion_mod.content_sha256_of([routed], keep_active=False, live_tier="OBSERVATION", root=tmp_path) == \
        promotion_content_sha256([routed["candidate_id"]], [routed["strategy_rule_hash"]], False, "OBSERVATION", [],
                                 artifact_sha256s=promotion_mod.candidate_artifacts([routed]))
    summary = _promote(tmp_path, routed, keep_active=False)
    assert summary["reactivated"] == [] and summary["replaced_entries"] == []
    [entry] = _entries(tmp_path)
    assert entry["candidate_id"] == routed["candidate_id"] and PREDECESSOR_KEYS_FIELD not in entry


def test_a_replaced_entry_without_a_candidate_id_is_named_by_its_generation(tmp_path):
    """41 of the host's retired entries were installed without a candidate id; their record keys on
    `gen:`, and that is the key the approval names and the successor inherits."""
    retired = _seed(tmp_path, generation="GEN-001")
    imported = {k: v for k, v in _hand_entry(retired, strategy_id="S1", status="SUSPENDED").items()
                if k != "candidate_id"}
    pool.install_active_pool({"active_strategies": [imported]}, root=tmp_path)
    back = _seed(tmp_path, generation="GEN-002")
    key = f"gen:GEN-001:{retired['strategy_rule_hash']}"
    assert pool.reactivated_candidate_ids([back["candidate_id"]], keep_active=True, root=tmp_path,
                                          candidates=[back]) == [key]
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, back)
    assert "POOL_SILENT_REACTIVATION" in str(refused.value)
    _promote(tmp_path, back, allow_reactivation=True)
    [entry] = _entries(tmp_path)
    assert entry[PREDECESSOR_KEYS_FIELD] == [key]


def test_an_archived_rule_returns_as_a_reactivation_too(tmp_path):
    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _set_status(tmp_path, "S1", "ARCHIVED")
    with pytest.raises(SystemExit) as refused:
        _promote(tmp_path, _seed(tmp_path, generation="GEN-002"))
    assert "POOL_SILENT_REACTIVATION" in str(refused.value) and "ARCHIVED" in str(refused.value)


def test_the_returning_rule_keeps_the_longest_streak_among_what_it_replaces(tmp_path):
    a, b = _seed(tmp_path, generation="GEN-001"), _seed(tmp_path, generation="GEN-002")
    pool.install_active_pool({"active_strategies": [
        {**_hand_entry(a, strategy_id="S1", status="SUSPENDED"), "lifecycle_consecutive_failures": 1},
        {**_hand_entry(b, strategy_id="S1-GEN-002", status="SUSPENDED"), "lifecycle_consecutive_failures": 3},
    ]}, root=tmp_path)
    _promote(tmp_path, _seed(tmp_path, generation="GEN-003"), allow_reactivation=True)
    [entry] = _entries(tmp_path)
    assert entry["lifecycle_consecutive_failures"] == 3


def test_a_restate_keeps_the_entry_s_own_streak(tmp_path):
    """Review of PR3c-2: a replace-mode restate rebuilt every entry from its candidate row, and every
    re-listed entry's failure streak went back to nothing — including a returning rule's, which the
    LIVE-arming restate goes through."""
    row = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, row)
    _set_status(tmp_path, "S1", "WARNING", lifecycle_consecutive_failures=1)
    _promote(tmp_path, row, keep_active=False)
    [entry] = _entries(tmp_path)
    assert entry["lifecycle_consecutive_failures"] == 1


@requires_local_core
def test_the_ask_says_who_returns_in_words_thomas_can_check(tmp_path, monkeypatch):
    first, twin = _retired_twin(tmp_path)
    _pool_at(monkeypatch, tmp_path)
    prepared = request_promotion([twin["candidate_id"]], keep_active=True, live_tier="OBSERVATION", now=NOW,
                                 candidates_root=tmp_path, allow_reactivation=True)
    [said] = [r for r in prepared["permission_decision"]["risk"]["risk_reasons"] if "RETURNS RETIRED" in r]
    assert f"replacing S1 [{first['candidate_id']}] SUSPENDED (operator_retired)" in said
    assert "judged on their record" in said


def test_an_approved_return_installs_and_one_asked_before_it_named_returns_does_not(tmp_path):
    """Ask, approve, install — the approval's hash names the retired lineage the rule returns from.
    One minted without it (as before PR3c-2) binds another effect, and the door refuses it."""
    from tests.test_mvp_runtime_crypto_strategy_artifact import _approval

    first = _seed(tmp_path, generation="GEN-001")
    _promote(tmp_path, first)
    _retire(tmp_path)
    twin = _seed(tmp_path, generation="GEN-002")
    stale = _approval(tmp_path, [twin], live_tier="OBSERVATION", approval_id="approval_named_nothing",
                      content=promotion_content_sha256(
                          [twin["candidate_id"]], [twin["strategy_rule_hash"]], False, "OBSERVATION", [],
                          artifact_sha256s=promotion_mod.candidate_artifacts([twin])))
    with pytest.raises(SystemExit) as refused:
        run_promotion(selectors=[twin["candidate_id"]], promoted_by="Thomas", reason="r", keep_active=False,
                      live_tier="OBSERVATION", root=tmp_path, now=NOW, approval_id=stale, allow_reactivation=True)
    assert "APPROVAL_CONTENT_MISMATCH" in str(refused.value)
    named = _approval(tmp_path, [twin], live_tier="OBSERVATION", approval_id="approval_named_the_return")
    summary = run_promotion(selectors=[twin["candidate_id"]], promoted_by="Thomas", reason="r", keep_active=False,
                            live_tier="OBSERVATION", root=tmp_path, now=NOW, approval_id=named,
                            allow_reactivation=True)
    assert summary["approval_verified"] is True
    [entry] = _entries(tmp_path)
    assert f"cand:{first['candidate_id']}" in entry[PREDECESSOR_KEYS_FIELD]
