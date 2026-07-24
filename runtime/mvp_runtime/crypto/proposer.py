"""LLM strategy-family proposer — the model suggests, the deterministic code judges.

The factory's creative ceiling is its twelve hand-written template families: it mutates
parameters inside them and never invents a thirteenth. This module is where a model can
propose one — and where that proposal is held to exactly the same evidence standard as
everything else.

**The model never reaches code.** A ``StrategyTemplate`` carries an ``entry_builder``
that is a Python callable, so a model proposing one would be proposing code to execute.
Instead a proposal is *declarative data* — the same ``entry_rules``/``exit_rules`` shape
``strategy_spec.v1`` already defines — which the existing validator parses and the
existing backtester scores. Adding a family to ``factory.TEMPLATES`` stays a human code
change in Thomas's PR (the programization precedent: the runtime produces the candidate,
Thomas authors the substance).

So the pipeline keeps its determinism. The model widens the *search space*; generation,
backtest, and scoring remain byte-identical functions of the candles.

Why a proposal cannot smuggle a bad strategy in:

- ``factory.validate_strategy`` rejects it on the same S3 bounds as any candidate
  (reward/risk >= 1.0, parameter ranges, no duplicate rule hash).
- ``factory.backtest_spec`` + ``robustness.score_robustness`` judge it on real candles.
  A proposal arrives with evidence attached or it arrives rejected.
- **Hallucinated indicators are caught by that same validator**, which is stricter than
  this module could be: its ``NUMERIC_FEATURES``/``CATEGORICAL_FEATURES`` vocabulary is
  a strict subset of the live feature row, so anything it accepts the row can supply.
  This module adds no gate of its own — it only *names* the offending features, because
  ``BLOCK_UNKNOWN_FEATURE`` alone does not say which one, and a review sheet that cannot
  say "you invented `quantum_flux`" wastes the reader's time.

  Worth knowing while reading proposals: three C9 liquidation features
  (``liquidation_total``, ``long_liquidation``, ``short_liquidation``) are computed into
  the feature row but are **not** in the validator's vocabulary, so a proposal using them
  is rejected even though the data exists. Only ``liquidation_spike_ratio`` is usable.

Fail direction — degraded, never blocking (the ``TRIAGE_DEGRADED`` precedent): a
provider failure or an unparseable answer yields zero proposals with the reason
recorded. Nothing downstream depends on a proposal existing.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from runtime.read_only_kernel import integrity

from ..budgets import TRIAGE_TIMEOUT_SECONDS
from ..errors import ProviderError
from ..worker import Provider
from . import factory
from .robustness import score_robustness
from .strategy import ALLOWED_TIMEFRAMES, SpecParseError, StrategySpec

PROPOSER_WORKER_ID = "mvp.crypto.strategy_proposer.llm"
PROPOSER_WORKER_VERSION = "0.1.0"
PROPOSER_PROMPT_VERSION = "mvp_crypto_strategy_proposer.v1"
PROPOSAL_RECORD_TYPE = "crypto_strategy_family_proposal.v0"

# A family proposal is a few short JSON objects, not an essay — but it is bigger than a
# triage verdict, so it gets its own allowance rather than borrowing that one.
PROPOSAL_TOKEN_ALLOWANCE = 4000
PROPOSAL_TIMEOUT_SECONDS = TRIAGE_TIMEOUT_SECONDS

PROPOSER_DEGRADED = "PROPOSER_DEGRADED"
# The validator's own block code, re-used to decide when to add the diagnostic.
UNKNOWN_FEATURE_BLOCK = "BLOCK_UNKNOWN_FEATURE"

# Proposals are capped per run: an unbounded list is a way to spend the whole allowance
# on quantity and none of it on thought.
MAX_PROPOSALS_PER_RUN = 4


class MockProposerProvider:
    """Deterministic proposer: no network, no real model (the ``MockTriageProvider``
    precedent). Returns two fixed proposals — one valid, one referencing an invented
    indicator — so the mock path exercises acceptance AND the unknown-feature rejection
    rather than only the degraded branch."""

    model_id = "mock.strategy_proposer"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call

    # Shaped like a real provider answer: the shared analysis envelope with the
    # proposals riding as an extra key (see build_proposal_prompt).
    _PROPOSALS = {
        "summary": "two proposed families",
        "key_findings": [],
        "facts": [],
        "proposals": [
            {
                "family": "liquidation_flush_reversal",
                "rationale": "A forced-liquidation cascade overshoots; price mean-reverts "
                             "once the flush exhausts.",
                "direction": "long",
                "timeframe": "1h",
                "entry_rules": {"operator": "AND", "conditions": [
                    {"feature": "liquidation_spike_ratio", "comparison": ">=", "value": 2.0},
                    {"feature": "rsi", "comparison": "<=", "value": 35.0},
                ]},
                "exit_rules": {"stop_model": "atr", "stop_atr": 1.2, "target_atr": 3.0,
                               "max_holding_bars": 24},
            },
            {
                "family": "invented_oscillator_cross",
                "rationale": "Deliberately references an indicator this runtime does not "
                             "compute, so the mock path demonstrates that rejection.",
                "direction": "long",
                "timeframe": "1h",
                "entry_rules": {"operator": "AND", "conditions": [
                    {"feature": "quantum_flux_oscillator", "comparison": ">", "value": 0.5},
                ]},
                "exit_rules": {"stop_model": "atr", "stop_atr": 1.0, "target_atr": 2.0,
                               "max_holding_bars": 12},
            },
        ]
    }

    def generate(self, prompt: str, *, max_output_tokens: int, timeout_seconds: int):
        from ..worker import ProviderResult
        # Returned as-is, exactly as a hosted provider hands back its parsed analysis.
        return ProviderResult(
            analysis=dict(self._PROPOSALS),
            model_id=self.model_id,
            model_version=self.model_version,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )


def known_features() -> frozenset[str]:
    """The feature vocabulary a strategy may reference — the validator's own, not the
    feature row's. The row is wider (it computes columns no spec is allowed to name),
    and the validator is the authority on what a spec may say."""
    return frozenset(factory.NUMERIC_FEATURES) | frozenset(factory.CATEGORICAL_FEATURES)


def unknown_features(spec: StrategySpec) -> list[str]:
    """Which of ``spec``'s feature names the validator does not know (sorted, deduped).

    Diagnostic, not a gate: ``factory.validate_strategy`` has already refused the spec
    by the time this is called. It exists because ``BLOCK_UNKNOWN_FEATURE`` names no
    feature, and the reader needs to know which one to stop proposing.
    """
    available = known_features()
    return sorted(name for name in spec.referenced_features() if name not in available)


def build_proposal_prompt(
    *,
    existing_families: Sequence[str],
    focus: str | None = None,
    count: int = MAX_PROPOSALS_PER_RUN,
) -> str:
    """The proposal prompt: the real vocabulary, the real families, the real bounds.

    The feature list comes from the validator's own vocabulary rather than a
    written-down copy, so the model is asked for exactly what a spec is allowed to name
    — the cheapest way to make hallucinated indicators rare instead of merely caught.
    """
    features = ", ".join(sorted(known_features()))
    families = ", ".join(sorted(existing_families))
    focus_line = (
        f"\nFocus this proposal on: {focus}. Prefer features related to that focus.\n"
        if focus else "\n"
    )
    return (
        "You are proposing new crypto trading strategy FAMILIES for a backtesting "
        "factory. Each proposal is a declarative rule set — you are not writing code.\n"
        f"{focus_line}"
        f"\nAvailable features (use ONLY these names, exactly):\n{features}\n"
        f"\nFamilies that already exist (propose something genuinely different):\n{families}\n"
        "\nHard constraints — a proposal violating any of these is discarded:\n"
        f"- timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}\n"
        "- direction is \"long\" or \"short\"\n"
        "- target_atr / stop_atr must be >= 1.0 (reward:risk)\n"
        "- stop_atr in [0.3, 5.0]; target_atr in [0.5, 10.0]; max_holding_bars in [1, 500]\n"
        "- 1 to 8 entry conditions\n"
        "- each condition is {\"feature\": <name>, \"comparison\": <op>, \"value\": <number>} "
        "or {\"feature\": <name>, \"comparison\": <op>, \"value_from\": <another feature name>}\n"
        "- comparison is one of >, >=, <, <=, ==, !=\n"
        # The hosted providers parse every answer as the shared analysis JSON and reject
        # anything missing summary/key_findings/facts, so the proposals ride INSIDE that
        # envelope as an extra key (the R7 precedent triage uses for its verdict). Asking
        # for a bare {"proposals": [...]} object fails at the provider, before this
        # module ever sees it.
        "\nReply with ONLY a JSON object of this shape:\n"
        '{"summary": "<one line>", "key_findings": [], "facts": [], '
        '"proposals": [{"family": "<snake_case_name>", "rationale": "<one sentence: what '
        'market behaviour this exploits>", "direction": "long", "timeframe": "1h", '
        '"entry_rules": {"operator": "AND", "conditions": [...]}, '
        '"exit_rules": {"stop_model": "atr", "stop_atr": 1.2, "target_atr": 3.0, '
        '"max_holding_bars": 24}}]}\n'
        f"\nPropose at most {int(count)} families."
    )


def _extract_proposals(analysis: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Pull the proposal list out of a provider answer. Never raises — an unusable
    answer is zero proposals, which the caller reports as degraded."""
    payload: Any = analysis
    # The shared analysis JSON wraps free-form model output; a proposal list may arrive
    # at the top level or inside the summary as a JSON string.
    if isinstance(payload, Mapping) and "proposals" not in payload:
        for key in ("summary", "raw_text"):
            text = payload.get(key)
            if isinstance(text, str):
                start, end = text.find("{"), text.rfind("}")
                if start >= 0 and end > start:
                    try:
                        candidate = json.loads(text[start:end + 1])
                    except ValueError:
                        continue
                    if isinstance(candidate, Mapping) and "proposals" in candidate:
                        payload = candidate
                        break
    if not isinstance(payload, Mapping):
        return []
    proposals = payload.get("proposals")
    if not isinstance(proposals, list):
        return []
    return [p for p in proposals if isinstance(p, Mapping)][:MAX_PROPOSALS_PER_RUN]


def _spec_dict(proposal: Mapping[str, Any], *, index: int, symbol: str) -> dict[str, Any]:
    """A proposal as a ``strategy_spec.v1`` dict — the shape the validator already reads."""
    family = str(proposal.get("family") or f"proposed_{index}")
    return {
        "schema_version": "strategy_spec.v1",
        "strategy_id": f"PROP-{index:03d}",
        "strategy_version": "1.0",
        "strategy_family": family,
        "symbol_scope": [symbol],
        "timeframe": proposal.get("timeframe"),
        "direction": proposal.get("direction"),
        "entry_rules": proposal.get("entry_rules"),
        "exit_rules": proposal.get("exit_rules"),
        "risk_constraints": {"max_risk_per_trade_R": 1.0},
    }


def evaluate_proposal(
    proposal: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    """Judge ONE proposal with the existing deterministic machinery.

    Returns a verdict dict either way — a rejection is as much a result as an
    acceptance, and both belong in the record. Order matters: parse, then features,
    then the S3 validator, then the backtest. Each stage is cheaper than the next and
    a failure at any stage makes the later ones meaningless.
    """
    symbol = str(snapshot.get("symbol") or "BTCUSDT")
    verdict: dict[str, Any] = {
        "family": str(proposal.get("family") or f"proposed_{index}"),
        "rationale": str(proposal.get("rationale") or ""),
        "accepted": False,
    }

    try:
        spec = StrategySpec.from_dict(_spec_dict(proposal, index=index, symbol=symbol))
    except SpecParseError as exc:
        verdict["reject_reason"] = f"parse: {exc}"
        return verdict

    validation = factory.validate_strategy(spec)
    if not validation.get("approved_for_backtest"):
        block_reasons = list(validation.get("block_reasons") or [])
        verdict["reject_reason"] = "validator"
        verdict["block_reasons"] = block_reasons
        if UNKNOWN_FEATURE_BLOCK in block_reasons:
            # The validator says a feature is unknown but not which; name them, so the
            # review sheet is readable. Diagnostic only — the validator already refused.
            verdict["unknown_features"] = unknown_features(spec)
        return verdict

    backtest = factory.backtest_spec(spec, snapshot)
    robustness = score_robustness(
        spec,
        backtest,
        backtest.get("walk_forward") or {},
        backtest.get("regime_breakdown") or {},
    )
    verdict.update({
        "accepted": True,
        "spec": spec.to_dict(),
        "strategy_rule_hash": backtest.get("strategy_rule_hash"),
        "closed_count": backtest.get("closed_count"),
        "expectancy": backtest.get("expectancy"),
        "champion_score": robustness.get("robustness_score"),
        "robustness_verdict": robustness.get("verdict"),
        "trades_per_parameter": robustness.get("trades_per_parameter"),
        "robustness_warnings": list(robustness.get("warnings") or []),
    })
    return verdict


def propose_strategy_families(
    snapshot: Mapping[str, Any],
    *,
    provider: Provider,
    now: str,
    existing_families: Sequence[str],
    focus: str | None = None,
    count: int = MAX_PROPOSALS_PER_RUN,
) -> dict[str, Any]:
    """Ask the model for families, judge each one, return the proposal record.

    The record is evidence for a human decision — it grants nothing and installs
    nothing. A provider failure degrades to zero proposals with the reason recorded;
    nothing downstream depends on a proposal existing.
    """
    prompt = build_proposal_prompt(
        existing_families=existing_families, focus=focus, count=count
    )

    degraded: str | None = None
    invocation: dict[str, Any] | None = None
    raw: list[dict[str, Any]] = []
    try:
        result = provider.generate(
            prompt,
            max_output_tokens=PROPOSAL_TOKEN_ALLOWANCE,
            timeout_seconds=PROPOSAL_TIMEOUT_SECONDS,
        )
    except (ProviderError, TimeoutError) as exc:
        degraded = f"proposer provider failed: {exc}"
    else:
        raw = _extract_proposals(result.analysis if isinstance(result.analysis, Mapping) else {})
        if not raw:
            degraded = "proposer returned no parseable proposals"
        invocation = {
            "worker_id": PROPOSER_WORKER_ID,
            "worker_version": PROPOSER_WORKER_VERSION,
            "model_id": result.model_id,
            "model_version": result.model_version,
            "prompt_version": PROPOSER_PROMPT_VERSION,
            "input_tokens": int(result.input_tokens),
            "output_tokens": int(result.output_tokens),
            "tokens_used": int(result.input_tokens) + int(result.output_tokens),
            "latency_ms": int(result.latency_ms),
            "finish_reason": result.finish_reason,
            "network_egress": bool(getattr(provider, "network_egress", False)),
        }

    verdicts = [
        evaluate_proposal(p, snapshot, index=i)
        for i, p in enumerate(raw, start=1)
    ]
    accepted = [v for v in verdicts if v.get("accepted")]

    record = {
        "record_type": PROPOSAL_RECORD_TYPE,
        "symbol": snapshot.get("symbol"),
        "timeframe": snapshot.get("timeframe"),
        "candle_count": snapshot.get("candle_count"),
        "focus": focus,
        "prompt_version": PROPOSER_PROMPT_VERSION,
        "proposed_count": len(verdicts),
        "accepted_count": len(accepted),
        "proposals": verdicts,
        "invocation": invocation,
        # An installed family would be a code change; this record never is one.
        "installation_effect": "NONE",
        "created_at": now,
    }
    if degraded:
        record["degraded"] = PROPOSER_DEGRADED
        record["degraded_reason"] = degraded
    record["proposal_id"] = integrity.short_id(
        "propose", {"symbol": record["symbol"], "focus": focus, "at": now}
    )
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def format_proposal_report(record: Mapping[str, Any]) -> str:
    """Human-readable review sheet — what Thomas reads before writing any code."""
    lines = [
        "=== strategy family proposals ===",
        f"symbol/timeframe : {record.get('symbol')} {record.get('timeframe')} "
        f"({record.get('candle_count')} candles)",
        f"focus            : {record.get('focus') or '(none)'}",
        f"proposed/accepted: {record.get('proposed_count')} / {record.get('accepted_count')}",
    ]
    if record.get("degraded"):
        lines.append(f"degraded         : {record.get('degraded_reason')}")
    for proposal in record.get("proposals") or []:
        lines.append("")
        if not proposal.get("accepted"):
            detail = str(proposal.get("reject_reason"))
            if proposal.get("block_reasons"):
                detail = f"{detail} {proposal['block_reasons']}"
            if proposal.get("unknown_features"):
                detail = f"{detail} — no such feature: {', '.join(proposal['unknown_features'])}"
            lines.append(f"REJECTED {proposal.get('family')}: {detail}")
            continue
        lines.append(f"ACCEPTED {proposal.get('family')}  score={proposal.get('champion_score')} "
                     f"({proposal.get('robustness_verdict')})")
        lines.append(f"  rationale : {proposal.get('rationale')}")
        lines.append(f"  backtest  : {proposal.get('closed_count')} closed, "
                     f"expectancy {proposal.get('expectancy')}, "
                     f"{proposal.get('trades_per_parameter')} trades/parameter")
        for warning in proposal.get("robustness_warnings") or []:
            lines.append(f"  warning   : {warning}")
    lines.append("")
    lines.append("Installing a family is a code change in factory.TEMPLATES — this record "
                 "installs nothing.")
    return "\n".join(lines)


__all__ = [
    "PROPOSAL_RECORD_TYPE",
    "build_proposal_prompt",
    "evaluate_proposal",
    "format_proposal_report",
    "known_features",
    "propose_strategy_families",
    "unknown_features",
]
