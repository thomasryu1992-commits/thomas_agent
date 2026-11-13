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

  Worth knowing while reading proposals: the row computes several columns the validator
  does **not** admit, so a proposal using one is rejected even though the data exists.
  They are the venue-scale quantities — raw ``open_interest``, ``quote_volume``,
  ``trade_count``, ``taker_buy_base``, ``taker_buy_quote``, ``avg_trade_size`` — which are
  withheld on purpose: a threshold on any of them means a different thing on every symbol.
  Their normalized derivatives (``*_change_pct``, ``*_zscore``, ``taker_buy_ratio``,
  ``taker_flow_imbalance``, ``taker_flow_ma``) are the usable forms.

  *(This note previously said the three liquidation columns were excluded. They were
  admitted by Thomas on 2026-07-24 and the note was not updated — a proposal naming them
  has been accepted ever since, contrary to what the sentence claimed.)*

Fail direction — degraded, never blocking (the ``TRIAGE_DEGRADED`` precedent): a
provider failure or an unparseable answer yields zero proposals with the reason
recorded. Nothing downstream depends on a proposal existing.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from typing import Any, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from ..budgets import TRIAGE_TIMEOUT_SECONDS
from ..errors import MvpRuntimeError, ProviderError, ToolBlocked
from ..worker import Provider
from . import cost as costs
from . import factory, market_data, template_space
from .features import build_feature_rows
from .forward_confirmation import min_forward_trades
from .robustness import score_robustness
from .strategy import ALLOWED_TIMEFRAMES, SpecParseError, StrategySpec

PROPOSER_WORKER_ID = "mvp.crypto.strategy_proposer.llm"
PROPOSER_WORKER_VERSION = "0.1.0"
# v2 (2026-11-12): the prompt states what the trial screen refuses and names what was already
# refused or retired, and a proposal must carry the hypothesis form (below).
PROPOSER_PROMPT_VERSION = "mvp_crypto_strategy_proposer.v3"
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

# The hypothesis form a proposal must carry (HYPOTHESIS_FORM_AND_RESEARCH_MAP_V0.1 §2, adopted
# 2026-11-12; required of model proposals by FACTORY_GENERATOR_REDESIGN_V0.1 G5). Until v2 a
# proposal said why in one sentence, so every proposal was a recombination of feature names with a
# caption. The form asks what the caption cannot answer: who is on the other side, why it lasts,
# how large it is, and what would show it wrong.
#
# **Presence, not truth.** The check is that each field is there — five non-empty sentences and a
# probability in [0, 1]. Nothing here can tell a good answer from a fluent one; the judge of the
# rule is still the backtest, the holdout and the forward walk, exactly as before.
HYPOTHESIS_TEXT_FIELDS = ("payer", "persistence", "size", "falsifier", "nearest_record")
HYPOTHESIS_PRIOR_FIELD = "prior"
# Stored length per field. An answer is cut to this, never refused for length: the allowance
# (`PROPOSAL_TOKEN_ALLOWANCE`) is what bounds the model, and four proposals at this length are
# well inside it (`test_four_full_proposals_fit_the_token_allowance`).
HYPOTHESIS_FIELD_CHARS = 200

# What the prompt is told about earlier screens: the families a trial screen refused, with the
# screen's own reason token. Structural and trade-count reasons only — `_screen_trials` refuses
# on parsing, the validator, a known family, a column the frame cannot supply, the train-only
# lattice, no trade, and pace. None is a function of a holdout or forward return, so telling the
# model does not let the generator fit what the judge has yet to read. Closed trials' reasons
# (which do name holdout and forward results) are deliberately NOT passed.
#
# A family name is model-written text coming back into a prompt, so it is held to the shape a
# family name has and the list to a size; anything else is dropped here and refused at the
# worker's door (`pipeline_worker`).
REFUSALS_IN_PROMPT = 20
REFUSAL_FAMILY_SHAPE = re.compile(r"^[a-z][a-z0-9_+]{0,63}$")
REFUSAL_REASON_SHAPE = re.compile(r"^[a-z][a-z_]{0,39}$")

# M4b — the ledger record kind the CLI and the scheduler both append a proposal under.
PROPOSAL_LEDGER_KIND = "crypto_strategy_proposal"

# What a trade costs, told to the model (prompt v3). One number crosses to the worker beside the
# family names: the frame's typical ATR as a share of price, in basis points. With it the prompt
# can turn the cost model's fixed bps into R at a given ``stop_atr`` — the unit the model's own
# knob is in. These are the only values the worker's door lets into a prompt: a bar whose ATR
# is under half a basis point or over half the price is not a market this prompt describes, so
# such a value is dropped by the caller and refused by the door, never clipped into range.
ATR_SHARE_WIRE_RANGE = (0.5, 5000.0)


def clean_atr_bps(value: Any) -> float | None:
    """``value`` as the typical-ATR figure a prompt may carry, or None. Pure."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    low, high = ATR_SHARE_WIRE_RANGE
    if not math.isfinite(number) or not (low <= number <= high):
        return None
    return number


def typical_atr_bps(snapshot: Mapping[str, Any]) -> float | None:
    """The median of ATR over close across the frame's bars, in basis points of price.

    Read from the same feature rows the proposal's backtest replays, so "one ATR" in the prompt
    is the ATR the entry door will divide by. Market data only: no outcome, no holdout figure.
    None when the frame has no bar with both numbers, or cannot be built at all — the prompt
    then states the cost rule without the timeframe's arithmetic, the same degraded direction
    as an unreadable refusal list."""
    try:
        rows = build_feature_rows(dict(snapshot))
    except (MvpRuntimeError, ValueError, TypeError, KeyError, ZeroDivisionError):
        return None
    shares = [
        float(row["atr"]) / float(row["close"]) * 10000.0
        for row in rows
        if isinstance(row.get("atr"), (int, float)) and isinstance(row.get("close"), (int, float))
        and not isinstance(row.get("atr"), bool) and not isinstance(row.get("close"), bool)
        and row["atr"] > 0 and row["close"] > 0
    ]
    if not shares:
        return None
    return clean_atr_bps(round(statistics.median(shares), 1))


def cost_terms(venue: str, atr_bps: float | None = None) -> dict[str, float] | None:
    """The numbers the prompt's cost paragraph is built from, every one computed by the cost
    model itself. Pure.

    ``round_trip_bps`` is `cost.round_trip_cost_r` at a stop distance of the whole price — the
    friction as a share of notional, which is the form that does not depend on the stop. Given
    ``atr_bps`` it is divided out into R: ``stop_atr_edge`` is the stop distance, in ATRs, at
    which a typical bar's round trip costs exactly `cost.MAX_ENTRY_COST_R`, so a tighter stop is
    refused by the backtest and by both entry doors. None for a venue whose costs are not
    declared: the prompt says nothing about cost rather than quoting another venue's rates."""
    try:
        model = costs.cost_model_for(venue)
    except MvpRuntimeError:
        return None
    round_trip_bps = max(
        costs.round_trip_cost_r(direction, 1.0, 1.0, cost=model) for direction in ("LONG", "SHORT")
    ) * 10000.0
    if not math.isfinite(round_trip_bps) or round_trip_bps <= 0:
        return None
    terms = {
        "round_trip_bps": round_trip_bps,
        "entry_cost_limit_r": float(costs.MAX_ENTRY_COST_R),
        "funding_bps": float(model.funding_bps_per_interval),
        "funding_per_day": float(model.funding_intervals_per_day),
    }
    atr = clean_atr_bps(atr_bps)
    if atr is not None:
        terms.update({
            "atr_bps": atr,
            "cost_r_at_one_atr": round_trip_bps / atr,
            "stop_atr_edge": round_trip_bps / (costs.MAX_ENTRY_COST_R * atr),
            "carry_r_per_day_at_one_atr":
                model.funding_bps_per_interval * model.funding_intervals_per_day / atr,
        })
    return terms


def _cost_block(venue: str, timeframe: str | None, atr_bps: float | None) -> str:
    terms = cost_terms(venue, atr_bps)
    if terms is None:
        return ""
    limit = terms["entry_cost_limit_r"]
    opening = (
        "\nWhat a trade costs (the cost model the backtest and the entry doors use): a round trip "
        f"is about {terms['round_trip_bps']:.1f} bps of notional — taker in, taker plus slippage out. "
    )
    funding = (
        f"Funding is charged on notional at each settlement a position is held through "
        f"({terms['funding_per_day']:.0f} a day): a long pays a positive rate and a short earns it"
    )
    if "atr_bps" not in terms:
        return (
            opening
            + "In R that is the bps divided by the stop distance in bps of price (stop_atr x ATR), "
            f"and an entry whose round trip costs more than {limit}R is refused. "
            + funding + ". The edge you claim in \"size\" must clear these.\n"
        )
    one = terms["cost_r_at_one_atr"]
    bars = f"these {timeframe} bars" if timeframe else "these bars"
    return (
        opening
        + f"On {bars} one ATR is typically about {terms['atr_bps']:.0f} bps of price, so the cost in "
        f"R is {terms['round_trip_bps']:.1f} / (stop_atr x {terms['atr_bps']:.0f}): about {one:.2f}R at "
        f"stop_atr 1.0 and {one / 2:.2f}R at stop_atr 2.0. An entry whose round trip costs more than "
        f"{limit}R is refused, which on a typical bar is any stop_atr below "
        f"{terms['stop_atr_edge']:.2f} — such a rule trades only on its most volatile bars, if at all. "
        + funding
        + f" (at the venue's base rate of {terms['funding_bps']:.1f} bp, about "
        f"{terms['carry_r_per_day_at_one_atr']:.3f}R a day at stop_atr 1.0). "
        "The edge you claim in \"size\" must clear these.\n"
    )

# M4b — the unreviewed-backlog cap. Scheduling the proposer reverses the 2026-07-24
# "manual CLI only — proposals accumulate faster than anyone reviews" decision, so a
# scheduled fire is gated on a backlog cap: once this many DISTINCT accepted-but-uninstalled
# families are already waiting, a fire skips (and is audited) instead of piling on more.
# "Reviewed" is the real action — Thomas installing a family into ``factory.TEMPLATES`` (a
# code change) drops that proposal out of the backlog. A recency window ages old proposals
# out so the tap reopens on its own even if none are ever installed (no permanent block).
MAX_UNREVIEWED_BACKLOG = 12       # ~3 full fires' worth of accepted families
BACKLOG_WINDOW_DAYS = 30


def _scored_on_another_timeframe(record: Mapping[str, Any], proposal: Mapping[str, Any]) -> bool:
    """An acceptance recorded before :func:`evaluate_proposal` refused it: the spec names one
    timeframe and the record was scored on another. Both must be present to say so — a row
    that does not carry them keeps counting (absence is not evidence of the defect)."""
    spec = proposal.get("spec")
    spec_timeframe = spec.get("timeframe") if isinstance(spec, Mapping) else None
    scored = record.get("timeframe")
    return isinstance(spec_timeframe, str) and isinstance(scored, str) and spec_timeframe != scored


# The factory's record kind, read here for one field: the `trial` block a cohort fire writes when
# it screens proposals (`factory._screen_trials`).
FACTORY_LEDGER_KIND = "crypto_factory"


def clean_refusals(items: Any) -> list[dict[str, str]]:
    """``[{family, reason}]`` held to :data:`REFUSAL_FAMILY_SHAPE` / :data:`REFUSAL_REASON_SHAPE`,
    deduplicated by family (the latest reason wins) and cut to the newest
    :data:`REFUSALS_IN_PROMPT`. Never raises: an entry that does not fit is dropped."""
    kept: dict[str, str] = {}
    for item in items if isinstance(items, (list, tuple)) else ():
        if not isinstance(item, Mapping):
            continue
        family, reason = item.get("family"), item.get("reason")
        if (isinstance(family, str) and REFUSAL_FAMILY_SHAPE.match(family)
                and isinstance(reason, str) and REFUSAL_REASON_SHAPE.match(reason)):
            kept.pop(family, None)
            kept[family] = reason
    return [{"family": f, "reason": r} for f, r in list(kept.items())[-REFUSALS_IN_PROMPT:]]


def noting_screen_refusals(
    record_rows: Iterable[Mapping[str, Any]], found: list[dict[str, Any]],
) -> Iterable[Mapping[str, Any]]:
    """``record_rows`` passed through unchanged, with every proposal a factory trial screen
    refused noted into ``found`` (``{family, reason}``, oldest first) as its row goes by.

    A pass-through rather than a second read: the ledger stream is read under the records lock
    and one row at a time, and the backlog count already walks it once. A malformed row is
    yielded and notes nothing."""
    for row in record_rows:
        if isinstance(row, Mapping) and row.get("kind") == FACTORY_LEDGER_KIND:
            record = row.get("record")
            trial = record.get("trial") if isinstance(record, Mapping) else None
            for entry in (trial.get("refused") or []) if isinstance(trial, Mapping) else []:
                if isinstance(entry, Mapping):
                    found.append({"family": entry.get("family"), "reason": entry.get("reason")})
        yield row


def recent_screen_refusals(record_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The proposals the factory's trial screens refused, from the ledger rows given (the caller
    windows them), oldest first, cleaned by :func:`clean_refusals`."""
    found: list[dict[str, Any]] = []
    for _ in noting_screen_refusals(record_rows, found):
        pass
    return clean_refusals(found)


def hypothesis_form(proposal: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """The proposal's hypothesis form and the fields it lacks. ``(form, [])`` when complete —
    text fields stripped and cut to :data:`HYPOTHESIS_FIELD_CHARS`, the prior a float —
    otherwise ``(None, missing)``. Presence only (see :data:`HYPOTHESIS_TEXT_FIELDS`)."""
    raw = proposal.get("hypothesis")
    raw = raw if isinstance(raw, Mapping) else {}
    form: dict[str, Any] = {}
    missing: list[str] = []
    for field in HYPOTHESIS_TEXT_FIELDS:
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            form[field] = value.strip()[:HYPOTHESIS_FIELD_CHARS]
        else:
            missing.append(field)
    prior = raw.get(HYPOTHESIS_PRIOR_FIELD)
    if isinstance(prior, (int, float)) and not isinstance(prior, bool) and 0.0 <= float(prior) <= 1.0:
        form[HYPOTHESIS_PRIOR_FIELD] = float(prior)
    else:
        missing.append(HYPOTHESIS_PRIOR_FIELD)
    return (None, missing) if missing else (form, [])


def _screened_proposal_hashes(record: Mapping[str, Any]) -> set[str]:
    """The proposal rule hashes one factory record screened — minted or refused.

    Both, because either is a review: a minted proposal is now a trial with its own forward
    record, and a refused one was scored at depth and failed a named check. The fire's skips
    (``cap``, ``not_cohort``) list neither and so screen nothing."""
    trial = record.get("trial")
    if not isinstance(trial, Mapping):
        return set()
    hashes: set[str] = set()
    for entry in [*(trial.get("minted") or []), *(trial.get("refused") or [])]:
        value = entry.get("strategy_rule_hash") if isinstance(entry, Mapping) else None
        if isinstance(value, str) and value:
            hashes.add(value)
    return hashes


def _in_window_acceptances(record_rows: Iterable[Mapping[str, Any]], *, cutoff: str):
    """One pass over ledger rows: the in-window acceptances by today's rule, and every proposal
    hash a factory fire has screened. Split out because the backlog and the trial queue ask the
    same two questions of the same stream."""
    accepted: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    screened: set[str] = set()
    for row in record_rows:
        if not isinstance(row, Mapping):
            continue
        record = row.get("record")
        if not isinstance(record, Mapping):
            continue
        if row.get("kind") == FACTORY_LEDGER_KIND:
            screened |= _screened_proposal_hashes(record)
            continue
        if row.get("kind") != PROPOSAL_LEDGER_KIND:
            continue
        created = record.get("created_at")
        if not (isinstance(created, str) and created >= cutoff):
            continue
        for proposal in record.get("proposals") or []:
            if not isinstance(proposal, Mapping) or not proposal.get("accepted"):
                continue
            if _scored_on_another_timeframe(record, proposal):
                continue
            accepted.append((record, proposal))
    return accepted, screened


def pending_trial_proposals(
    record_rows: Iterable[Mapping[str, Any]],
    *,
    timeframe: str,
    now: str,
    exclude_rule_hashes: Iterable[str] = (),
    window_days: int = BACKLOG_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """The accepted proposals a ``timeframe`` cohort fire may screen as trials, oldest first.

    The backlog's population (same window, same acceptance rule — an acceptance scored on
    another timeframe's bars is not one) narrowed to the fire's timeframe, minus every proposal a
    factory fire already screened and ``exclude_rule_hashes`` (the store's trial sources — a
    trial row outlives the window its screening record was read in). Keyed on the proposal's
    own rule hash; one without a hash cannot be tracked and is not offered. Deduplicated, first
    sighting wins, so a family re-proposed with the same rules is screened once."""
    from .. import timeutil

    cutoff = timeutil.plus_minutes(now, -abs(int(window_days)) * 24 * 60)
    accepted, screened = _in_window_acceptances(record_rows, cutoff=cutoff)
    skip = screened | {h for h in exclude_rule_hashes if isinstance(h, str)}
    queue: list[dict[str, Any]] = []
    for record, proposal in sorted(accepted, key=lambda pair: str(pair[0].get("created_at"))):
        spec = proposal.get("spec")
        rule_hash = proposal.get("strategy_rule_hash")
        if not isinstance(spec, Mapping) or spec.get("timeframe") != timeframe:
            continue
        if not isinstance(rule_hash, str) or not rule_hash or rule_hash in skip:
            continue
        skip.add(rule_hash)
        queue.append({
            "proposal_id": record.get("proposal_id"),
            "family": proposal.get("family"),
            "strategy_rule_hash": rule_hash,
            "proposed_at": record.get("created_at"),
            # The proposer's one-sentence why, carried so a trial row can say it without a join
            # through `proposal_id` (CRYPTO_SYSTEM_IMPROVEMENT_GAP §7, first when D3 lifted).
            "rationale": str(proposal.get("rationale") or ""),
            # The hypothesis form, when the acceptance carries one. An acceptance recorded before
            # prompt v2 has none and is still queued: it was accepted by the rule of its day, and
            # the trial screen judges the rule, not the caption.
            **({"hypothesis": dict(proposal["hypothesis"])}
               if isinstance(proposal.get("hypothesis"), Mapping) else {}),
            "spec": dict(spec),
        })
    return queue


def count_unreviewed_backlog(
    record_rows: Iterable[Mapping[str, Any]],
    installed_families: Sequence[str],
    *,
    now: str,
    window_days: int = BACKLOG_WINDOW_DAYS,
) -> int:
    """DISTINCT accepted proposal families still awaiting review (M4b backlog measure).

    A family counts when it was **accepted** by an in-window proposal record and is **not
    yet installed** in ``factory.TEMPLATES`` — the exact pile a scheduled proposer would be
    adding to faster than Thomas can work it. Deduped by family (re-proposing the same one
    is not a bigger backlog), windowed by ``created_at`` so an unreviewed proposal ages out
    (the tap reopens without an install), and blind to rejected proposals (they were never a
    review burden) — including an acceptance recorded on another timeframe's bars, which the
    evaluator now rejects: the ledger keeps the original verdict, the count reads it by
    today's rule rather than holding the tap shut on it. ``record_rows`` are ledger rows as ``LedgerStore.iter_records`` yields
    them (a single pass — the ledger is never materialized for a count) (``{"kind", "record"}``); a malformed row is skipped, never fatal — a backlog count
    must not itself fail closed and stop the scheduler.

    **A proposal a factory fire screened as a trial is reviewed** (Thomas 2026-09-25,
    HYPOTHESIS_TRIAL_V0.1 Q6), minted or refused: the screen scored it at depth, which is the
    review an install was the only form of before. So a family counts only while one of its
    in-window acceptances is still unscreened — and the tap reopens as the trial fires work the
    queue, rather than only as the window ages it out."""
    from .. import timeutil

    cutoff = timeutil.plus_minutes(now, -abs(int(window_days)) * 24 * 60)
    installed = {f for f in installed_families if isinstance(f, str)}
    accepted, screened = _in_window_acceptances(record_rows, cutoff=cutoff)
    pending: set[str] = set()
    for _record, proposal in accepted:
        family = proposal.get("family")
        if not (isinstance(family, str) and family and family not in installed):
            continue
        if proposal.get("strategy_rule_hash") in screened:
            continue
        pending.add(family)
    return len(pending)


class MockProposerProvider:
    """Deterministic proposer: no network, no real model (the ``MockTriageProvider``
    precedent). Returns two fixed proposals — one valid, one referencing an invented
    indicator — so the mock path exercises acceptance AND the unknown-feature rejection
    rather than only the degraded branch."""

    model_id = "mock.strategy_proposer"
    model_version = "0.1.0"
    network_egress = False  # deterministic, in-process; no outbound call
    # Holds a model_id for record-keeping but reaches no model. Declared explicitly
    # because gate_banners now announces anything carrying a model_id: silence is the
    # thing that must be opted into, so a real capability can never go unannounced.
    model_invocation = False

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
                "hypothesis": {
                    "payer": "Leveraged longs closed by the venue at any price.",
                    "persistence": "Forced sellers cannot wait, and absorbing them needs capital at the worst moment.",
                    "size": "Unknown at this capital; a few percent a year after costs if it exists at all.",
                    "falsifier": "No rebound within a day of the flush, across the cohort.",
                    "nearest_record": "The installed liquidation families; this one waits for exhaustion, not the spike.",
                    "prior": 0.1,
                },
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
                "hypothesis": {
                    "payer": "Nobody: the indicator does not exist.",
                    "persistence": "It cannot persist.",
                    "size": "Zero.",
                    "falsifier": "The validator refusing the feature name.",
                    "nearest_record": "None.",
                    "prior": 0.0,
                },
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


def known_features(venue: str = market_data.BINANCE_FUTURES) -> frozenset[str]:
    """The feature vocabulary a strategy mined on ``venue`` may reference.

    The validator's own, not the feature row's: the row is wider (it computes columns no
    spec is allowed to name), and the validator is the authority on what a spec may say.
    Delegates to ``factory.known_features`` rather than unioning the raw tables, so the
    venue scoping cannot be true at validation and false here — which is the failure this
    function had when it read `NUMERIC_FEATURES` directly.
    """
    numeric, categorical = template_space.known_features(venue)
    return numeric | frozenset(categorical)


def unknown_features(spec: StrategySpec) -> list[str]:
    """Which of ``spec``'s feature names the validator does not know (sorted, deduped).

    Diagnostic, not a gate: ``factory.validate_strategy`` has already refused the spec
    by the time this is called. It exists because ``BLOCK_UNKNOWN_FEATURE`` names no
    feature, and the reader needs to know which one to stop proposing.

    Scoped to the spec's OWN venue, so a name that exists but is unavailable where the spec
    was mined is reported — a name absent from this list while the spec is refused for
    naming it would send the reader looking for a typo that is not there.
    """
    try:
        available = known_features(spec.venue)
    except ToolBlocked:
        # An undeclared venue is not a feature problem; `validate_strategy` reports it as
        # BLOCK_UNKNOWN_VENUE, and naming every feature here would bury that.
        return []
    return sorted(name for name in spec.referenced_features() if name not in available)


def build_proposal_prompt(
    *,
    existing_families: Sequence[str],
    focus: str | None = None,
    count: int = MAX_PROPOSALS_PER_RUN,
    venue: str = market_data.BINANCE_FUTURES,
    timeframe: str | None = None,
    recent_refusals: Sequence[Mapping[str, Any]] = (),
    atr_bps: float | None = None,
) -> str:
    """The proposal prompt: the real vocabulary, the real families, the real bounds — and, since
    v2, what the trial screen will do with the answer.

    Until v2 the model was handed feature names, family names and a focus, and nothing about
    what happens next. It could not know that a rule too slow to reach the forward judge's trade
    floor is never walked (`factory.TRIAL_FREQUENCY_HORIZON_DAYS`), that a retired family is
    refused by name, or that the family it is about to propose was refused last week. Three of
    the four trials open from 2026-09-28 to 2026-11-12 never traded once. So the prompt now says
    those things. All of it is generator-side knowledge — code constants and the screens' own
    structural refusals (``recent_refusals``, cleaned by :func:`clean_refusals`); nothing from a
    holdout or a forward record is passed, so the search cannot fit what the judge reads.

    Since v3 it also says what a trade costs (:func:`cost_terms`): the round trip in bps from the
    cost model, and — given ``atr_bps``, the frame's typical ATR as a share of price — the same
    cost in R at a stop distance, and the stop below which the entry door refuses every typical
    bar. Before v3 the model chose ``stop_atr`` without knowing that the cost in R is fixed bps
    over that stop. Computed from code and the frame's own bars; the measured per-timeframe
    costs and gross figures in the records are holdout results and are not passed.

    ``timeframe`` is the frame the proposals will be scored on. Given, it is the ONLY timeframe
    the prompt allows: `evaluate_proposal` refuses a spec whose timeframe is not the frame's
    (D-0, #963), so offering the model every allowed timeframe invited proposals that were paid
    for and then discarded — measured 2026-09-25, successful 1h fires still carried 15m and 4h
    specs. Absent keeps the open list, for a caller that has no frame yet.

    The feature list comes from the validator's own vocabulary rather than a
    written-down copy, so the model is asked for exactly what a spec is allowed to name
    — the cheapest way to make hallucinated indicators rare instead of merely caught.

    ``venue`` narrows that list to what the venue can actually serve. Offering a feature the
    validator will refuse invites a proposal that is discarded after the model has already
    been paid for it, and the model has no way to know which of the names it was handed are
    real here — it was told to use ONLY these, so listing one it may not use is our error
    reported as its mistake.
    """
    features = ", ".join(sorted(known_features(venue)))
    families = ", ".join(sorted(existing_families))
    retired = ", ".join(sorted(template_space.RETIRED_FAMILIES))
    refusals = clean_refusals(list(recent_refusals))
    refused_block = (
        "\nRecently refused by the trial screen (family: reason) — do not re-propose these, and "
        "read the reasons:\n" + "\n".join(f"- {r['family']}: {r['reason']}" for r in refusals) + "\n"
        if refusals else ""
    )
    pace_block = (
        f"\nWhat happens to an accepted proposal: it is re-scored across the whole symbol cohort on "
        f"{timeframe} bars at full history. It is REFUSED there if it makes no trade, or if its "
        f"backtest pace would give fewer than {min_forward_trades(timeframe)} trades in "
        f"{factory.TRIAL_FREQUENCY_HORIZON_DAYS} days across the cohort (too_slow) — a rule that "
        "rarely fires can never be judged. Prefer conditions that fire regularly over a rare "
        "coincidence of many thresholds.\n"
        if timeframe else ""
    )
    focus_line = (
        f"\nFocus this proposal on: {focus}. Prefer features related to that focus.\n"
        if focus else "\n"
    )
    timeframe_rule = (
        f"- timeframe must be \"{timeframe}\" — the bars your proposals are scored on; any other "
        "timeframe is discarded\n"
        if timeframe else f"- timeframe must be one of {sorted(ALLOWED_TIMEFRAMES)}\n"
    )
    example_timeframe = timeframe or "1h"
    return (
        "You are proposing new crypto trading strategy FAMILIES for a backtesting "
        "factory. Each proposal is a declarative rule set — you are not writing code.\n"
        f"{focus_line}"
        f"\nAvailable features (use ONLY these names, exactly):\n{features}\n"
        f"\nFamilies that already exist (propose something genuinely different):\n{families}\n"
        f"\nFamilies already searched and retired (a proposal naming one is refused):\n{retired}\n"
        f"{refused_block}"
        f"{pace_block}"
        f"{_cost_block(venue, timeframe, atr_bps)}"
        "\nHard constraints — a proposal violating any of these is discarded:\n"
        f"{timeframe_rule}"
        "- direction is \"long\" or \"short\"\n"
        "- target_atr / stop_atr must be >= 1.0 (reward:risk)\n"
        "- stop_atr in [0.3, 5.0]; target_atr in [0.5, 10.0]; max_holding_bars in [1, 500]\n"
        "- 1 to 8 entry conditions\n"
        "- each condition is {\"feature\": <name>, \"comparison\": <op>, \"value\": <number>} "
        "or {\"feature\": <name>, \"comparison\": <op>, \"value_from\": <another feature name>}\n"
        "- comparison is one of >, >=, <, <=, ==, !=\n"
        "- every proposal carries a \"hypothesis\" object with ALL six fields, written BEFORE you "
        "choose the rules, one short sentence each (under 160 characters): "
        "\"payer\" (who is on the other side and why they pay), "
        "\"persistence\" (why others have not already taken it), "
        "\"size\" (what you expect to remain after costs at 1,000-10,000 USD a position), "
        "\"falsifier\" (what observation would make you drop it), "
        "\"nearest_record\" (the closest existing or refused family and how this differs — a "
        "changed sign or window is not a new hypothesis), "
        "\"prior\" (a number from 0 to 1: your probability that it survives). "
        "A proposal missing any field is discarded\n"
        # The hosted providers parse every answer as the shared analysis JSON and reject
        # anything missing summary/key_findings/facts, so the proposals ride INSIDE that
        # envelope as an extra key (the R7 precedent triage uses for its verdict). Asking
        # for a bare {"proposals": [...]} object fails at the provider, before this
        # module ever sees it.
        "\nReply with ONLY a JSON object of this shape:\n"
        '{"summary": "<one line>", "key_findings": [], "facts": [], '
        '"proposals": [{"family": "<snake_case_name>", "rationale": "<one sentence: what '
        'market behaviour this exploits>", "hypothesis": {"payer": "...", "persistence": "...", '
        '"size": "...", "falsifier": "...", "nearest_record": "...", "prior": 0.1}, '
        '"direction": "long", '
        f'"timeframe": "{example_timeframe}", '
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

    # The replay reads the SNAPSHOT's bars and nothing compares them to the spec's timeframe, so a
    # 4h proposal scored on the 1h frame ran its exits in 1h bars (`max_holding_bars=48` = 2 days,
    # not 8) and was recorded as accepted. Measured 2026-09-24: 19 of the 39 accepted families were
    # scored that way (`docs/proposals/HYPOTHESIS_TRIAL_V0.1.md` §1, D-0). Refused rather than
    # re-collected at the spec's timeframe — that multiplies the fire's requests and is option C's
    # to decide. A frame that names no timeframe is refused too: nothing could be compared.
    scored_timeframe = snapshot.get("timeframe")
    if spec.timeframe != scored_timeframe:
        verdict["reject_reason"] = "timeframe"
        verdict["spec_timeframe"] = spec.timeframe
        verdict["scored_timeframe"] = scored_timeframe
        return verdict

    # The hypothesis form (see `HYPOTHESIS_TEXT_FIELDS`): presence only. Checked before the
    # validator and the backtest because it is cheaper than either, and a rule without a stated
    # hypothesis is the recombination this channel exists not to be.
    form, missing = hypothesis_form(proposal)
    if form is None:
        verdict["reject_reason"] = "form"
        verdict["missing_fields"] = missing
        return verdict
    verdict["hypothesis"] = form

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
    venue: str = market_data.BINANCE_FUTURES,
) -> dict[str, Any]:
    """Ask the model for families, judge each one, return the proposal record.

    The record is evidence for a human decision — it grants nothing and installs
    nothing. A provider failure degrades to zero proposals with the reason recorded;
    nothing downstream depends on a proposal existing.
    """
    generation = generate_proposals(
        provider=provider, existing_families=existing_families, focus=focus,
        count=count, venue=venue, timeframe=snapshot.get("timeframe"),
        atr_bps=typical_atr_bps(snapshot),
    )
    return assemble_proposal_record(snapshot, generation=generation, focus=focus, now=now)


def generate_proposals(
    *,
    provider: Provider,
    existing_families: Sequence[str],
    focus: str | None = None,
    count: int = MAX_PROPOSALS_PER_RUN,
    venue: str = market_data.BINANCE_FUTURES,
    timeframe: str | None = None,
    recent_refusals: Sequence[Mapping[str, Any]] = (),
    atr_bps: float | None = None,
) -> dict[str, Any]:
    """The model half: ask for families, parse what came back. **Touches no snapshot.**

    Split out so the model call can run somewhere other than the process that judges its
    answer (`docs/proposals/CREDENTIAL_PLANE_SEPARATION_PHASE2_V0.1.md`, D6). That the split
    is this clean is a property of the prompt, not a refactor: ``build_proposal_prompt`` takes
    family names, a focus, a count, a venue and the frame's timeframe NAME — it has never read
    the market frame. The frame is used only by ``evaluate_proposal`` below, which invokes no
    model.

    Returns the three things the record needs from the call and nothing else, so what crosses
    a socket is a handful of strings each way rather than a 5-leg market snapshot.
    """
    prompt = build_proposal_prompt(
        existing_families=existing_families, focus=focus, count=count, venue=venue,
        timeframe=timeframe, recent_refusals=recent_refusals, atr_bps=atr_bps,
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
    return {"raw": raw, "invocation": invocation, "degraded": degraded}


def assemble_proposal_record(
    snapshot: Mapping[str, Any],
    *,
    generation: Mapping[str, Any],
    focus: str | None,
    now: str,
) -> dict[str, Any]:
    """The judging half: score each proposal against the frame and build the record.

    Deterministic and model-free — every verdict comes from ``evaluate_proposal``, which is
    the existing backtest machinery. This is what stays beside the market data when the model
    call is delegated.
    """
    raw = list(generation.get("raw") or [])
    invocation = generation.get("invocation")
    degraded = generation.get("degraded")

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
            if proposal.get("reject_reason") == "timeframe":
                detail = (f"{detail} — spec is {proposal.get('spec_timeframe')}, "
                          f"the frame is {proposal.get('scored_timeframe')}")
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
    "pending_trial_proposals",
    "build_proposal_prompt",
    "evaluate_proposal",
    "format_proposal_report",
    "known_features",
    "propose_strategy_families",
    "generate_proposals",
    "assemble_proposal_record",
    "unknown_features",
]
