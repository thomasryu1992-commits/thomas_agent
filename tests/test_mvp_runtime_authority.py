"""D remediation tests — runtime/mvp_runtime/authority.py.

The shared module is the single authority for the P0-P6 order, the authority
invariant, and the no-grant effect blocks; these tests pin its semantics so the
importing modules (planner / permission / assignment / validation / audit /
safety_gate) cannot silently drift again.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.authority import (
    EVIDENCE_ONLY,
    LEVEL_RANK,
    REVIEW_ONLY,
    audit_event_runtime_effect,
    authority_invariant_holds,
    permission_decision_runtime_effect,
    rank_of,
    validation_result_permission_boundary,
    validation_result_runtime_effect,
)


def test_level_rank_is_the_total_p0_p6_order():
    assert LEVEL_RANK == {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4, "P5": 5, "P6": 6}
    ranks = [rank_of(level) for level in ("P0", "P1", "P2", "P3", "P4", "P5", "P6")]
    assert ranks == sorted(ranks)


@pytest.mark.parametrize("bad", ["P7", "p2", "", None, 2, ["P2"]])
def test_rank_of_unknown_level_is_none(bad):
    assert rank_of(bad) is None


def test_invariant_holds_for_least_privilege_grant():
    assert authority_invariant_holds("P2", "P2", "P2", "P3") is True
    assert authority_invariant_holds("P0", "P1", "P2", "P3") is True


def test_invariant_fails_when_required_exceeds_ceiling():
    assert authority_invariant_holds("P4", "P4", "P4", "P3") is False
    assert authority_invariant_holds("P2", "P3", "P2", "P3") is False  # effective > granted


@pytest.mark.parametrize("levels", [
    ("P9", "P2", "P2", "P3"),
    ("P2", None, "P2", "P3"),
    ("P2", "P2", "p2", "P3"),
    ("P2", "P2", "P2", ""),
])
def test_invariant_raises_on_unknown_level(levels):
    with pytest.raises(ValueError):
        authority_invariant_holds(*levels)


@pytest.mark.parametrize("factory, mode", [
    (permission_decision_runtime_effect, REVIEW_ONLY),
    (validation_result_runtime_effect, REVIEW_ONLY),
    (audit_event_runtime_effect, EVIDENCE_ONLY),
])
def test_effect_blocks_grant_nothing(factory, mode):
    block = factory()
    assert block["mode"] == mode
    flags = {k: v for k, v in block.items() if k != "mode"}
    assert flags and all(v is False for v in flags.values())


def test_permission_boundary_grants_nothing():
    boundary = validation_result_permission_boundary()
    assert boundary and all(v is False for v in boundary.values())


@pytest.mark.parametrize("factory", [
    permission_decision_runtime_effect,
    validation_result_permission_boundary,
    validation_result_runtime_effect,
    audit_event_runtime_effect,
])
def test_factories_return_fresh_dicts(factory):
    first, second = factory(), factory()
    assert first == second and first is not second
    first["mutated"] = True
    assert "mutated" not in factory()
