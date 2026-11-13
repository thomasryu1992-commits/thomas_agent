"""LLM strategy-family proposer tests — the model suggests, the deterministic code judges.

The safety property under test is that a proposal is *evidence*, never an installation:
it cannot reach ``factory.TEMPLATES``, it cannot smuggle an unevaluable feature past the
validator, and a provider failure degrades to zero proposals instead of raising.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import factory, features, market_data, proposer, template_space
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


_FORM = {
    "payer": "Late longs who chase a pullback's rebound.",
    "persistence": "It needs patience through the dip, which momentum traders do not have.",
    "size": "A few percent a year after costs at this size, if it exists.",
    "falsifier": "No rebound within a day of the entry across the cohort.",
    "nearest_record": "The installed RSI pullback family; this one adds a trend filter.",
    "prior": 0.15,
}


def _valid_proposal(**overrides):
    base = {
        "family": "rsi_pullback_test",
        "rationale": "test",
        "hypothesis": dict(_FORM),
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
    assert known == frozenset(template_space.NUMERIC_FEATURES) | frozenset(template_space.CATEGORICAL_FEATURES)
    # Strictly narrower than the computed row: the validator is the authority on what a
    # spec may name, and it does not admit every column features.py computes.
    assert known < frozenset(str(k) for k in row)


def test_every_computed_liquidation_feature_is_proposable(row):
    """The C9 liquidation columns are all nameable (Thomas 2026-07-24).

    They were held out while the Coinalyze feed was unconfigured. The gap is closed,
    so this now guards the other direction: a column features.py computes but the
    validator will not admit is a column no proposal can ever use.
    """
    # `prev_*` is the SAME series read one bar back (`features.attach_previous_bar`), not a
    # column of its own — a spec reaches it through `lag`, never by naming it, and
    # `referenced_features` strips the prefix for exactly this reason. Counting it here would
    # demand the validator admit a name no proposal may write.
    computed = {k for k in row
                if "liquidation" in k and not k.startswith(features.PREVIOUS_BAR_PREFIX)}
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


# --- the vocabulary offered is the venue's, not the library's ------------------

def test_the_prompt_offers_only_features_the_venue_can_serve():
    """The model is told to use ONLY the names it is given, so a name it may not use is
    our error surfacing as its mistake — a proposal discarded after the provider was paid."""
    hyperliquid = proposer.build_proposal_prompt(
        existing_families=["breakout"], venue=market_data.HYPERLIQUID
    )
    binance = proposer.build_proposal_prompt(existing_families=["breakout"])

    assert "liquidation_spike_ratio" in binance
    assert "liquidation_spike_ratio" not in hyperliquid
    assert "taker_flow_zscore" in binance and "taker_flow_zscore" not in hyperliquid
    # Kept: funding is a real series on this venue, and the trade count is on its candle.
    assert "funding_zscore" in hyperliquid
    assert "trade_count_zscore" in hyperliquid


def test_known_features_agrees_with_the_validator_per_venue():
    # It used to union the raw tables, which made it right only for binance_futures.
    for venue in (market_data.BINANCE_FUTURES, market_data.HYPERLIQUID):
        numeric, categorical = template_space.known_features(venue)
        assert proposer.known_features(venue) == numeric | frozenset(categorical)


def test_unknown_features_are_judged_against_the_specs_own_venue():
    """`liquidation_spike_ratio` is a real feature on binance and not on hyperliquid, so the
    same spec must be reported differently — a refusal naming nothing sends the reader
    looking for a typo that is not there."""
    from runtime.mvp_runtime.crypto.strategy import StrategySpec

    raw = proposer._spec_dict(
        _valid_proposal(entry_rules={"operator": "AND", "conditions": [
            {"feature": "liquidation_spike_ratio", "comparison": "<", "value": 0.5}]}),
        index=1, symbol="BTCUSDT",
    )
    assert proposer.unknown_features(StrategySpec.from_dict(raw)) == []
    on_hyperliquid = StrategySpec.from_dict({**raw, "venue": market_data.HYPERLIQUID})
    assert proposer.unknown_features(on_hyperliquid) == ["liquidation_spike_ratio"]


def test_an_undeclared_venue_reports_no_unknown_features():
    # The venue is what is wrong; `validate_strategy` says so with BLOCK_UNKNOWN_VENUE, and
    # listing every condition here would bury that under names that are all perfectly real.
    from runtime.mvp_runtime.crypto.strategy import StrategySpec

    raw = proposer._spec_dict(_valid_proposal(), index=1, symbol="BTCUSDT")
    spec = StrategySpec.from_dict({**raw, "venue": "not_a_venue"})
    assert proposer.unknown_features(spec) == []


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


def test_a_proposal_for_another_timeframe_is_rejected_not_scored_on_these_bars(snapshot):
    # D-0 (HYPOTHESIS_TRIAL_V0.1 §1): a 4h spec on the 1h frame ran its exits in 1h bars and
    # was recorded as accepted. The same proposal on its own timeframe is still accepted.
    verdict = proposer.evaluate_proposal(_valid_proposal(timeframe="4h"), snapshot, index=1)
    assert verdict["accepted"] is False
    assert verdict["reject_reason"] == "timeframe"
    assert (verdict["spec_timeframe"], verdict["scored_timeframe"]) == ("4h", "1h")
    assert "closed_count" not in verdict  # never backtested
    assert proposer.evaluate_proposal(_valid_proposal(), snapshot, index=1)["accepted"] is True


def test_a_frame_naming_no_timeframe_accepts_nothing(snapshot):
    frame = {k: v for k, v in snapshot.items() if k != "timeframe"}
    verdict = proposer.evaluate_proposal(_valid_proposal(), frame, index=1)
    assert verdict["accepted"] is False
    assert verdict["reject_reason"] == "timeframe"


def test_the_review_sheet_names_both_timeframes(snapshot):
    record = proposer.assemble_proposal_record(
        snapshot, generation={"raw": [_valid_proposal(timeframe="1d")]}, focus=None, now=NOW,
    )
    assert record["accepted_count"] == 0
    assert "REJECTED rsi_pullback_test: timeframe — spec is 1d, the frame is 1h" in (
        proposer.format_proposal_report(record))


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


# --- M4b: unreviewed-backlog counting ----------------------------------------

def _proposal_row(families_accepted, *, created_at="2026-07-24T00:00:00Z", rejected=()):
    """A ledger record row as LedgerStore.read_records returns it."""
    proposals = [{"family": f, "accepted": True} for f in families_accepted]
    proposals += [{"family": f, "accepted": False} for f in rejected]
    return {"kind": proposer.PROPOSAL_LEDGER_KIND, "trace_id": "t",
            "record": {"record_type": proposer.PROPOSAL_RECORD_TYPE,
                       "created_at": created_at, "proposals": proposals}}


def test_backlog_counts_distinct_accepted_uninstalled_families():
    rows = [
        _proposal_row(["alpha", "beta"]),
        _proposal_row(["beta", "gamma"]),     # beta repeats — dedup to one
    ]
    # gamma is not installed; alpha/beta not installed => {alpha, beta, gamma} = 3
    assert proposer.count_unreviewed_backlog(rows, [], now="2026-07-24T01:00:00Z") == 3


def test_backlog_excludes_installed_families():
    rows = [_proposal_row(["alpha", "beta", "gamma"])]
    # Installing beta (a factory.TEMPLATES code change) drops it from the backlog.
    assert proposer.count_unreviewed_backlog(rows, ["beta"], now="2026-07-24T01:00:00Z") == 2


def test_backlog_ignores_rejected_proposals():
    rows = [_proposal_row(["alpha"], rejected=["bad_one", "invented"])]
    assert proposer.count_unreviewed_backlog(rows, [], now="2026-07-24T01:00:00Z") == 1


def test_backlog_windows_out_old_proposals():
    rows = [
        _proposal_row(["stale"], created_at="2026-06-01T00:00:00Z"),   # >30d before now
        _proposal_row(["fresh"], created_at="2026-07-24T00:00:00Z"),
    ]
    assert proposer.count_unreviewed_backlog(rows, [], now="2026-07-25T00:00:00Z") == 1


def test_backlog_drops_an_acceptance_recorded_on_another_timeframe():
    # Ledger rows written before D-0 keep their verdict; the count reads them by today's rule.
    row = _proposal_row(["own", "other"])
    row["record"]["timeframe"] = "1h"
    row["record"]["proposals"][0]["spec"] = {"timeframe": "1h"}
    row["record"]["proposals"][1]["spec"] = {"timeframe": "4h"}
    assert proposer.count_unreviewed_backlog([row], [], now="2026-07-24T01:00:00Z") == 1


def test_backlog_keeps_counting_a_row_that_cannot_show_the_mismatch():
    # No record timeframe, or no spec: absence is not evidence of the defect.
    bare = _proposal_row(["no_record_tf"])
    bare["record"]["proposals"][0]["spec"] = {"timeframe": "4h"}
    no_spec = _proposal_row(["no_spec"])
    no_spec["record"]["timeframe"] = "1h"
    assert proposer.count_unreviewed_backlog([bare, no_spec], [], now="2026-07-24T01:00:00Z") == 2


def test_backlog_skips_malformed_rows_without_crashing():
    rows = [
        {"kind": "some_other_kind", "record": {}},          # wrong kind
        {"kind": proposer.PROPOSAL_LEDGER_KIND, "record": "not a mapping"},
        {"kind": proposer.PROPOSAL_LEDGER_KIND, "record": {"proposals": "nope"}},
        _proposal_row(["real"]),
    ]
    assert proposer.count_unreviewed_backlog(rows, [], now="2026-07-24T01:00:00Z") == 1


# --- the prompt names the frame's timeframe (2026-09-25) -------------------------------------

def test_the_prompt_allows_only_the_frames_timeframe_when_it_has_one():
    from runtime.mvp_runtime.crypto import proposer
    bound = proposer.build_proposal_prompt(existing_families=[], timeframe="1h")
    assert '- timeframe must be "1h"' in bound and '"timeframe": "1h"' in bound
    assert "timeframe must be one of" not in bound
    open_ = proposer.build_proposal_prompt(existing_families=[])
    assert "- timeframe must be one of" in open_


def test_the_cli_path_binds_the_prompt_to_the_snapshots_timeframe():
    from runtime.mvp_runtime.crypto import proposer
    prompts: list[str] = []

    class _Spy(proposer.MockProposerProvider):
        def generate(self, prompt, **kw):
            prompts.append(prompt)
            return super().generate(prompt, **kw)

    proposer.propose_strategy_families({"symbol": "BTCUSDT", "timeframe": "4h", "candles": []},
                                       provider=_Spy(), now="2026-09-25T00:00:00Z",
                                       existing_families=[])
    assert '- timeframe must be "4h"' in prompts[0]


# --- prompt v2: the hypothesis form, and what the prompt is told (2026-11-12) -------------------

def test_an_accepted_proposal_carries_its_hypothesis_form(snapshot):
    verdict = proposer.evaluate_proposal(_valid_proposal(), snapshot, index=1)
    assert verdict["accepted"] is True and verdict["hypothesis"] == _FORM


@pytest.mark.parametrize("hypothesis, missing", [
    (None, ["payer", "persistence", "size", "falsifier", "nearest_record", "prior"]),
    ("because it works", ["payer", "persistence", "size", "falsifier", "nearest_record", "prior"]),
    ({**_FORM, "payer": "  "}, ["payer"]),
    ({k: v for k, v in _FORM.items() if k != "falsifier"}, ["falsifier"]),
    ({**_FORM, "prior": "likely"}, ["prior"]),
    ({**_FORM, "prior": 1.5}, ["prior"]),
    ({**_FORM, "prior": True}, ["prior"]),
])
def test_a_proposal_without_the_whole_form_is_rejected_before_it_is_scored(snapshot, hypothesis, missing):
    """A rule with a caption is what this channel produced until v2. The form is required, and
    the check is presence — it cannot tell a good answer from a fluent one, and does not try."""
    proposal = _valid_proposal()
    if hypothesis is None:
        proposal.pop("hypothesis")
    else:
        proposal["hypothesis"] = hypothesis
    verdict = proposer.evaluate_proposal(proposal, snapshot, index=1)
    assert verdict["accepted"] is False and verdict["reject_reason"] == "form"
    assert verdict["missing_fields"] == missing
    assert "closed_count" not in verdict, "never backtested"


def test_a_long_answer_is_cut_not_refused(snapshot):
    proposal = _valid_proposal(hypothesis={**_FORM, "payer": "x" * 1000})
    verdict = proposer.evaluate_proposal(proposal, snapshot, index=1)
    assert verdict["accepted"] is True
    assert len(verdict["hypothesis"]["payer"]) == proposer.HYPOTHESIS_FIELD_CHARS


def test_the_mock_proposals_carry_the_form_so_a_mock_run_still_accepts_one(snapshot):
    record = proposer.propose_strategy_families(
        snapshot, provider=proposer.MockProposerProvider(), now="2026-11-12T00:00:00Z",
        existing_families=[])
    reasons = [p.get("reject_reason") for p in record["proposals"]]
    assert "form" not in reasons and reasons.count(None) == 1, reasons
    assert record["prompt_version"].endswith(".v3")


def test_four_full_proposals_fit_the_token_allowance():
    """Six sentences a proposal is more output than v1 asked for. Counted at the stored length and
    at three characters a token — pessimistic for English JSON — four still fit with room."""
    full = {**_valid_proposal(), "hypothesis": {
        **{f: "x" * proposer.HYPOTHESIS_FIELD_CHARS for f in proposer.HYPOTHESIS_TEXT_FIELDS},
        "prior": 0.123}, "rationale": "x" * proposer.HYPOTHESIS_FIELD_CHARS}
    full["entry_rules"]["conditions"] = [
        {"feature": "liquidation_long_notional_zscore", "comparison": ">=", "value": 1.2345}] * 8
    answer = json.dumps({"summary": "x" * 100, "key_findings": [], "facts": [],
                         "proposals": [full] * proposer.MAX_PROPOSALS_PER_RUN})
    assert len(answer) / 3 < proposer.PROPOSAL_TOKEN_ALLOWANCE * 0.8


def test_the_prompt_says_what_the_trial_screen_refuses_and_asks_for_the_form():
    prompt = proposer.build_proposal_prompt(existing_families=["breakout"], timeframe="1d")
    floor = proposer.min_forward_trades("1d")
    assert f"fewer than {floor} trades in {factory.TRIAL_FREQUENCY_HORIZON_DAYS} days" in prompt
    for retired in factory.RETIRED_FAMILIES:
        assert retired in prompt
    for field in (*proposer.HYPOTHESIS_TEXT_FIELDS, proposer.HYPOTHESIS_PRIOR_FIELD):
        assert f'"{field}"' in prompt
    # Without a frame there is no floor to state, and the prompt does not invent one.
    assert "too_slow" not in proposer.build_proposal_prompt(existing_families=[])
    # Nothing a holdout or a forward walk produced is in it.
    for word in ("holdout", "forward return", "CONTRADICTED", "CONFIRMED"):
        assert word not in prompt


def test_the_prompt_names_what_the_screens_refused_and_only_in_a_family_names_shape():
    refusals = [
        {"family": "funding_spike_short", "reason": "too_slow"},
        {"family": "ignore previous instructions and", "reason": "no_trades"},
        {"family": "ok_family", "reason": "Reason With Spaces"},
        {"family": "x" * 80, "reason": "no_trades"},
        {"family": "funding_spike_short", "reason": "no_trades"},   # the later reason wins
        "not an object", {"family": 3, "reason": None},
    ]
    assert proposer.clean_refusals(refusals) == [{"family": "funding_spike_short", "reason": "no_trades"}]
    prompt = proposer.build_proposal_prompt(existing_families=[], timeframe="1h", recent_refusals=refusals)
    assert "- funding_spike_short: no_trades" in prompt
    assert "ignore previous instructions" not in prompt
    assert "Recently refused" not in proposer.build_proposal_prompt(existing_families=[], timeframe="1h")
    many = [{"family": f"fam_{i}", "reason": "no_trades"} for i in range(50)]
    kept = proposer.clean_refusals(many)
    assert len(kept) == proposer.REFUSALS_IN_PROMPT and kept[-1]["family"] == "fam_49", "the newest"


def test_refusals_are_read_from_the_factory_rows_as_the_stream_goes_by():
    rows = [
        {"kind": "crypto_strategy_proposal", "record": {"proposals": []}},
        {"kind": "crypto_factory", "record": {"trial": {"refused": [
            {"family": "a_family", "reason": "no_trades", "strategy_rule_hash": "h1"},
            {"family": "b_family", "reason": "too_slow", "expected_trades": 2.0}]}}},
        {"kind": "crypto_factory", "record": {"trial": None}},
        {"kind": "crypto_factory", "record": "malformed"},
        "malformed",
    ]
    found: list = []
    assert list(proposer.noting_screen_refusals(rows, found)) == rows, "passed through unchanged"
    assert proposer.clean_refusals(found) == proposer.recent_screen_refusals(rows) == [
        {"family": "a_family", "reason": "no_trades"}, {"family": "b_family", "reason": "too_slow"}]


# --- prompt v3: what a trade costs ---------------------------------------------------------


def test_the_prompt_states_the_cost_the_cost_model_computes():
    """Every figure in the cost paragraph is the cost model's own, turned into R by the frame's
    typical ATR — nothing measured on a holdout, nothing typed in."""
    from runtime.mvp_runtime.crypto import cost

    model = cost.cost_model_for(market_data.BINANCE_FUTURES)
    round_trip = cost.round_trip_cost_r("LONG", 1.0, 1.0, cost=model) * 10000.0
    atr_bps = 80.0
    prompt = proposer.build_proposal_prompt(existing_families=[], timeframe="1h", atr_bps=atr_bps)
    assert f"about {round_trip:.1f} bps of notional" in prompt
    assert f"about {round_trip / atr_bps:.2f}R at stop_atr 1.0" in prompt
    assert f"more than {cost.MAX_ENTRY_COST_R}R is refused" in prompt
    assert f"any stop_atr below {round_trip / (cost.MAX_ENTRY_COST_R * atr_bps):.2f}" in prompt
    for word in ("holdout", "forward return", "CONTRADICTED", "CONFIRMED"):
        assert word not in prompt


@pytest.mark.parametrize("atr_bps", [20.0, 80.0, 300.0])
def test_the_stop_the_prompt_names_is_where_the_entry_door_turns(atr_bps):
    """The prompt tells the model a stop tighter than the edge is refused on a typical bar. The
    door is asked directly: a plan just inside the edge is refused, one just outside is admitted."""
    from runtime.mvp_runtime.crypto import trade_plan

    edge = proposer.cost_terms(market_data.BINANCE_FUTURES, atr_bps)["stop_atr_edge"]
    close = 100.0
    atr = close * atr_bps / 10000.0

    def refused(stop_atr):
        return trade_plan.entry_cost_refusal(
            {"direction": "LONG", "entry_price": close, "risk": stop_atr * atr}) is not None

    assert refused(edge * 0.99) and not refused(edge * 1.01)


def test_the_typical_atr_is_read_from_the_frames_own_bars(snapshot):
    import statistics

    shares = [r["atr"] / r["close"] * 10000.0 for r in features.build_feature_rows(dict(snapshot))
              if isinstance(r.get("atr"), (int, float)) and isinstance(r.get("close"), (int, float))
              and r["atr"] > 0 and r["close"] > 0]
    assert shares, "the fixture frame has bars with an ATR"
    assert proposer.typical_atr_bps(snapshot) == round(statistics.median(shares), 1)
    # A frame with no bars, or not a frame at all, yields no number rather than a guess.
    assert proposer.typical_atr_bps({**snapshot, "candles": []}) is None
    assert proposer.typical_atr_bps({"candles": "not candles"}) is None


@pytest.mark.parametrize("value", [None, True, "80", 0, 0.1, -5.0, 6000.0, float("nan"), float("inf")])
def test_a_figure_outside_the_range_never_reaches_the_prompt(value):
    assert proposer.clean_atr_bps(value) is None
    prompt = proposer.build_proposal_prompt(existing_families=[], timeframe="1h", atr_bps=value)
    assert "ATR is typically" not in prompt
    # The rule itself needs no market number, so it is still stated.
    assert "bps of notional" in prompt and "divided by the stop distance" in prompt


def test_a_venue_whose_costs_are_not_measured_gets_no_cost_paragraph():
    """Quoting Binance's rates in a prompt about another venue would be a number that is wrong in
    a way the model cannot detect; the paragraph is left out and the prompt is still built."""
    from runtime.mvp_runtime.crypto import cost
    from runtime.mvp_runtime.errors import ToolError

    unmeasured = []
    for venue in market_data.VENUE_FEEDS:
        try:
            cost.cost_model_for(venue)
        except ToolError:
            unmeasured.append(venue)
    assert unmeasured, "this test needs a venue with feeds and no measured cost model"
    for venue in unmeasured:
        assert proposer.cost_terms(venue, 80.0) is None
        prompt = proposer.build_proposal_prompt(
            existing_families=[], timeframe="1h", atr_bps=80.0, venue=venue)
        assert "bps of notional" not in prompt and "Hard constraints" in prompt


def test_the_cli_path_tells_the_model_the_cost_on_its_own_frame(snapshot):
    prompts: list[str] = []

    class _Spy(proposer.MockProposerProvider):
        def generate(self, prompt, **kw):
            prompts.append(prompt)
            return super().generate(prompt, **kw)

    proposer.propose_strategy_families(
        snapshot, provider=_Spy(), now="2026-11-13T00:00:00Z", existing_families=[])
    assert f"typically about {proposer.typical_atr_bps(snapshot):.0f} bps of price" in prompts[0]
    assert "order of magnitude" in prompts[0], "the figure is one symbol's recent bars, and the prompt says so"


def test_the_record_keeps_the_one_market_number_its_prompt_carried(snapshot):
    record = proposer.propose_strategy_families(
        snapshot, provider=proposer.MockProposerProvider(), now="2026-11-13T00:00:00Z",
        existing_families=[])
    assert record["prompt_atr_bps"] == proposer.typical_atr_bps(snapshot)
    from runtime.read_only_kernel import integrity
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    assert integrity.sha256_record(body) == record["record_sha256"], "added before the hash, not after"

