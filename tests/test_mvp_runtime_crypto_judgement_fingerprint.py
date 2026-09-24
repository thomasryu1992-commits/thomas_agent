"""The judgement-rule fingerprint (RESEARCH_EPOCH_V0.1, decided 2026-09-24): one hash over the
constants that turn evidence into a verdict, read from their owners, stamped where verdicts are read
and checked once per operator start."""

from __future__ import annotations

import json

from runtime.mvp_runtime import policy_fingerprint
from runtime.mvp_runtime.crypto import judgement_fingerprint as jf
from runtime.mvp_runtime.crypto import robustness, tunables


def test_every_scoped_name_is_indexed_or_declared_not_indexed():
    scope = set(jf.judgement_rules())
    assert scope - tunables.names() == set(jf.NOT_INDEXED)
    assert not (jf.NOT_INDEXED & tunables.names())


def test_the_values_are_the_owners_own():
    rules = jf.judgement_rules()
    indexed = {t.name: t.value for t in tunables.TUNABLES}
    for name, value in rules.items():
        if name not in jf.NOT_INDEXED:
            assert value == indexed[name], name


def test_the_fingerprint_is_stable_and_moves_with_any_scoped_threshold(monkeypatch):
    first = jf.judgement_fingerprint()
    assert jf.judgement_fingerprint() == first and len(first["short"]) == 12
    monkeypatch.setattr(robustness, "MIN_HOLDOUT_TRADES", robustness.MIN_HOLDOUT_TRADES - 1)
    assert jf.judgement_fingerprint()["sha256"] != first["sha256"]


def test_the_check_records_once_and_says_changed_once(tmp_path, monkeypatch):
    first = jf.check_and_record(now="2026-09-24T00:00:00Z", root=tmp_path)
    assert first["status"] == policy_fingerprint.FIRST_SEEN and first["recorded"]
    assert jf.check_and_record(now="2026-09-24T01:00:00Z", root=tmp_path)["status"] == policy_fingerprint.UNCHANGED
    monkeypatch.setattr(robustness, "CONFIDENCE_Z", 2.5)
    changed = jf.check_and_record(now="2026-09-24T02:00:00Z", root=tmp_path)
    assert changed["status"] == policy_fingerprint.CHANGED and changed["previous_sha256"] == first["sha256"]
    assert "JUDGEMENT RULES CHANGED" in jf.banner(changed) and first["short"] in jf.change_notice(changed)
    assert jf.check_and_record(now="2026-09-24T03:00:00Z", root=tmp_path)["status"] == policy_fingerprint.UNCHANGED
    assert json.loads(jf.fingerprint_path(tmp_path).read_text(encoding="utf-8"))["sha256"] == changed["sha256"]


def test_an_unwritable_directory_degrades_never_raises(tmp_path):
    blocker = tmp_path / ".runtime_governance_state"
    blocker.write_text("not a directory", encoding="utf-8")
    result = jf.check_and_record(now="2026-09-24T00:00:00Z", root=tmp_path)
    assert result["status"] == policy_fingerprint.FIRST_SEEN and result["recorded"] is False


def test_the_board_and_the_cohort_record_carry_it(tmp_path):
    from runtime.mvp_runtime.crypto import forward_cohort
    from runtime.mvp_runtime.crypto.dashboard import build_status, render_status_text
    now = jf.judgement_fingerprint()
    status = build_status(tmp_path, now="2026-09-24T00:00:00Z")
    assert status["judgement_rules"] == now
    assert render_status_text(status).splitlines()[0].endswith(f" · 판정 규칙 {now['short']}")
    record = forward_cohort.build_cohort_record([], now="2026-09-24T00:00:00Z")
    assert record["judgement_rules"]["sha256"] == now["sha256"]
    assert record["judgement_rules"]["rules"] == jf.judgement_rules()


def test_the_scope_is_the_decided_one():
    """Q2 of the decision: holdout, forward, scoring, cost, selection correction and promotion-door
    constants — pinned by name, so dropping one is a diff that says so."""
    assert set(jf.judgement_rules()) == {
        "MIN_HOLDOUT_TRADES", "MIN_HOLDOUT_PERIODS", "CONFIDENCE_Z", "ROBUST_SCORE_THRESHOLD",
        "FRAGILE_SCORE_THRESHOLD", "HEALTHY_TRADES_PER_PARAMETER", "CRITICAL_TRADES_PER_PARAMETER",
        "MAX_FREE_PARAMETERS", "WEIGHTS", "HOLDOUT_FRACTION", "MIN_BARS_FOR_HOLDOUT",
        "WALK_FORWARD_PERIODS", "WALK_FORWARD_MIN_PERIODS", "MIN_TRADES_PER_WINDOW",
        "FACTORY_DEPTH_DAYS", "MIN_FACTORY_BARS", "SELECTION_ALPHA", "FORWARD_SLICE_WIDTH_DAYS",
        "MIN_FORWARD_TRADES_1D", "DEFAULT_TAKER_FEE_BPS", "DEFAULT_MAKER_FEE_BPS",
        "DEFAULT_SLIPPAGE_BPS", "DEFAULT_STOP_SLIPPAGE_BPS", "DEFAULT_FUNDING_BPS_PER_INTERVAL",
        "MAX_ENTRY_COST_R", "OBSERVATION_MIN_BACKTEST_CLOSED", "OBSERVATION_FAMILY_CAP",
        "PROMOTABLE_COST_BASIS_RANKS", "PROMOTABLE_EVIDENCE_DEPTH_RANKS", "PROMOTABLE_DERIVATION_TYPES",
    }
