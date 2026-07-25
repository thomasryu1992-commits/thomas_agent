"""LLM strategy-family proposer tests — the model suggests, the deterministic code judges.

The safety property under test is that a proposal is *evidence*, never an installation:
it cannot reach ``factory.TEMPLATES``, it cannot smuggle an unevaluable feature past the
validator, and a provider failure degrades to zero proposals instead of raising.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import factory, proposer
from runtime.mvp_runtime.crypto.features import latest_feature_row
from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data
from runtime.mvp_runtime.errors import ProviderError
from runtime.mvp_runtime.worker import ProviderResult

NOW = "2026-07-24T00:00:00Z"


@pytest.fixture(scope="module")
def snapshot():
    snap, _ = collect_market_data(
        "BTCUSDT", "1h", collector=MockMarketDataCollector(), now=NOW, limit=500
    )
    return snap


@pytest.fixture(scope="module")
def row(snapshot):
    return latest_feature_row(snapshot)


def _valid_proposal(**overrides):
    base = {
        "family": "rsi_pullback_test",
        "rationale": "test",
        "direction": "long",
        "timeframe": "1h",
        "entry_rules": {"operator": "AND", "conditions": [
            {"feature": "rsi", "comparison": "<=", "value": 40.0},
            {"feature": "ma20", "comparison": ">", "value_from": "ma50"},
        ]},
        "exit_rules": {"stop_model": "atr", "stop_atr": 1.2, "target_atr": 3.0,
                       "max_holding_bars": 24},
    }
    base.update(overrides)
    return base


class _Provider:
    """A provider test double.

    ``nested=False`` returns the proposals as an extra key on the shared analysis
    envelope — what the prompt asks for and what a hosted provider hands back.
    ``nested=True`` buries the JSON inside ``summary`` instead, which is what a model
    that ignores the shape does; both must work.
    """

    model_id, model_version, network_egress = "test.provider", "0.1.0", False

    def __init__(self, payload, *, raises=None, nested=False):
        self._payload, self._raises, self._nested = payload, raises, nested

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        if self._raises:
            raise self._raises
        envelope = {"summary": "", "key_findings": [], "facts": []}
        if self._nested:
            envelope["summary"] = json.dumps(self._payload)
        elif isinstance(self._payload, dict):
            envelope.update(self._payload)
        return ProviderResult(
            analysis=envelope,
            model_id=self.model_id, model_version=self.model_version,
            input_tokens=10, output_tokens=20, latency_ms=5,
        )


# --- the feature vocabulary (the validator's, not this module's) ---------------


def test_known_features_is_the_validators_vocabulary_not_the_rows(row):
    known = proposer.known_features()
    assert known == frozenset(factory.NUMERIC_FEATURES) | frozenset(factory.CATEGORICAL_FEATURES)
    # Strictly narrower than the computed row: the validator is the authority on what a
    # spec may name, and it does not admit every column features.py computes.
    assert known < frozenset(str(k) for k in row)


def test_every_computed_liquidation_feature_is_proposable(row):
    """The C9 liquidation columns are all nameable (Thomas 2026-07-24).

    They were held out while the Coinalyze feed was unconfigured. The gap is closed,
    so this now guards the other direction: a column features.py computes but the
    validator will not admit is a column no proposal can ever use.
    """
    computed = {k for k in row if "liquidation" in k}
    usable = {f for f in proposer.known_features() if "liquidation" in f}
    assert computed == usable == {
        "liquidation_spike_ratio", "liquidation_total", "long_liquidation", "short_liquidation",
    }


def test_unknown_features_names_what_the_validator_refused(snapshot):
    verdict = proposer.evaluate_proposal(
        _valid_proposal(entry_rules={"operator": "AND", "conditions": [
            {"feature": "rsi", "comparison": "<=", "value": 40.0},
            {"feature": "quantum_flux", "comparison": ">", "value": 1.0},
            {"feature": "another_invention", "comparison": ">", "value": 1.0},
        ]}),
        snapshot, index=1,
    )
    # The VALIDATOR refuses it; this module only names the offenders for the reader.
    assert verdict["accepted"] is False
    assert verdict["reject_reason"] == "validator"
    assert proposer.UNKNOWN_FEATURE_BLOCK in verdict["block_reasons"]
    assert verdict["unknown_features"] == ["another_invention", "quantum_flux"]  # sorted


def test_why_naming_them_matters(row, snapshot):
    """A hallucinated feature PARSES and would score — it just never matches.

    ``strategy_spec.v1`` accepts any non-empty string as a feature name and the
    evaluator is fail-closed on unknown ones, so an invented indicator is safe but
    silent. The validator is what refuses it; naming it is what stops the model (and
    the reader) from proposing it again.
    """
    from runtime.mvp_runtime.crypto.strategy import StrategySpec, evaluate_spec

    spec = StrategySpec.from_dict(proposer._spec_dict(
        _valid_proposal(entry_rules={"operator": "AND", "conditions": [
            {"feature": "quantum_flux", "comparison": ">", "value": 0.0}]}),
        index=1, symbol="BTCUSDT",
    ))
    assert evaluate_spec(spec, row).matched is False  # parses fine, never matches
    assert proposer.unknown_features(spec) == ["quantum_flux"]


# --- deterministic judgement --------------------------------------------------


def test_a_valid_proposal_is_scored_by_the_existing_machinery(row, snapshot):
    verdict = proposer.evaluate_proposal(_valid_proposal(), snapshot, index=1)
    assert verdict["accepted"] is True
    # Scored by robustness.py, not by anything this module invented.
    assert verdict["champion_score"] is not None
    assert verdict["robustness_verdict"] in {"ROBUST", "PROVISIONAL", "FRAGILE"}
    assert "strategy_rule_hash" in verdict and "spec" in verdict


def test_a_proposal_failing_the_s3_validator_is_rejected(row, snapshot):
    # reward:risk below 1.0 — the validator's own bound, not a new rule here.
    verdict = proposer.evaluate_proposal(
        _valid_proposal(exit_rules={"stop_model": "atr", "stop_atr": 3.0, "target_atr": 1.0,
                                    "max_holding_bars": 24}),
        snapshot, index=1,
    )
    assert verdict["accepted"] is False
    assert verdict["reject_reason"] == "validator"
    assert verdict["block_reasons"]


def test_an_unparseable_proposal_is_rejected_not_raised(row, snapshot):
    verdict = proposer.evaluate_proposal(
        _valid_proposal(timeframe="7m"), snapshot, index=1  # outside ALLOWED_TIMEFRAMES
    )
    assert verdict["accepted"] is False
    assert verdict["reject_reason"].startswith("parse:")


def test_evaluation_is_deterministic(row, snapshot):
    a = proposer.evaluate_proposal(_valid_proposal(), snapshot, index=1)
    b = proposer.evaluate_proposal(_valid_proposal(), snapshot, index=1)
    assert a == b  # same candles + same proposal -> same verdict, model or not


# --- the run record -----------------------------------------------------------


def _run(snapshot, provider, **kwargs):
    return proposer.propose_strategy_families(
        snapshot, provider=provider, now=NOW,
        existing_families=[t.family for t in factory.TEMPLATES], **kwargs,
    )


def test_run_record_carries_verdicts_and_installs_nothing(row, snapshot):
    record = _run(snapshot, _Provider({"proposals": [_valid_proposal()]}))
    assert record["proposed_count"] == 1 and record["accepted_count"] == 1
    assert record["installation_effect"] == "NONE"
    assert record["record_sha256"].startswith("sha256:")
    assert record["invocation"]["worker_id"] == proposer.PROPOSER_WORKER_ID


def test_a_provider_failure_degrades_to_zero_proposals(row, snapshot):
    record = _run(snapshot, _Provider(None, raises=ProviderError("BOOM", "upstream down")))
    assert record["proposed_count"] == 0 and record["accepted_count"] == 0
    assert record["degraded"] == proposer.PROPOSER_DEGRADED
    assert "provider failed" in record["degraded_reason"]
    assert record["invocation"] is None  # nothing was spent to report


def test_an_unparseable_answer_degrades_rather_than_raising(row, snapshot):
    record = _run(snapshot, _Provider({"not_proposals": []}))
    assert record["proposed_count"] == 0
    assert record["degraded"] == proposer.PROPOSER_DEGRADED
    assert record["invocation"] is not None  # the call happened; its answer was unusable


def test_proposal_count_is_capped(row, snapshot):
    many = {"proposals": [_valid_proposal(family=f"f{i}") for i in range(20)]}
    record = _run(snapshot, _Provider(many))
    assert record["proposed_count"] == proposer.MAX_PROPOSALS_PER_RUN


def test_running_the_proposer_never_touches_the_template_registry(row, snapshot):
    before = tuple(factory.TEMPLATES)
    _run(snapshot, _Provider({"proposals": [_valid_proposal()]}))
    # The whole safety argument in one assertion: proposing cannot install.
    assert factory.TEMPLATES == before


# --- prompt + mock provider ---------------------------------------------------


def test_proposals_are_read_from_the_shared_analysis_envelope(row, snapshot):
    """The shape the prompt asks for: proposals as an extra key on the envelope.

    The hosted providers reject any answer missing summary/key_findings/facts, so a
    bare {"proposals": [...]} never reaches this module — verified against a live
    provider, which returned MALFORMED_RESPONSE until the prompt asked for the envelope.
    """
    record = _run(snapshot, _Provider({"proposals": [_valid_proposal()]}))
    assert record["proposed_count"] == 1 and not record.get("degraded")


def test_proposals_nested_in_the_summary_string_are_still_found(row, snapshot):
    # A model that answers with the envelope but stuffs the JSON into summary.
    record = _run(snapshot, _Provider({"proposals": [_valid_proposal()]}, nested=True))
    assert record["proposed_count"] == 1 and not record.get("degraded")


def test_prompt_asks_for_the_envelope_the_providers_require(row):
    prompt = proposer.build_proposal_prompt(existing_families=["breakout"])
    # Without these the provider's own parser rejects the answer before we see it.
    for required in ("summary", "key_findings", "facts"):
        assert required in prompt


def test_prompt_lists_the_real_vocabulary_and_the_existing_families(row):
    prompt = proposer.build_proposal_prompt(
        existing_families=[t.family for t in factory.TEMPLATES], focus="liquidation"
    )
    assert "liquidation_spike_ratio" in prompt  # the live row's own names
    assert "trend_pullback" in prompt           # so it is asked for something new
    assert "liquidation" in prompt              # the focus is carried
    assert "quantum" not in prompt


def test_mock_provider_exercises_both_outcomes(row, snapshot):
    record = _run(snapshot, proposer.MockProposerProvider())
    assert record["accepted_count"] == 1
    rejected = [p for p in record["proposals"] if not p.get("accepted")]
    # The mock's second proposal proves the validator refuses invented indicators and
    # that the review sheet names them.
    assert rejected and rejected[0]["unknown_features"] == ["quantum_flux_oscillator"]
    assert record["invocation"]["network_egress"] is False


def test_report_states_that_nothing_was_installed(row, snapshot):
    text = proposer.format_proposal_report(
        _run(snapshot, proposer.MockProposerProvider())
    )
    assert "installs nothing" in text
    assert "ACCEPTED" in text and "REJECTED" in text
