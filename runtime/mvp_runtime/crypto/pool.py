"""C7 strategy pool state — the active pool the cycle routes against, and the
imported-candidate store the C8 promotion flow will consume.

Two files under the crypto state directory:

- ``active_strategy_pool.json`` — the single pointer the runtime *reads*. The cycle
  only ever loads it; installing or changing it is an **operator door** (the import
  script's explicit ``--activate-pool``, and later C8's approval flow) — never a
  runtime side effect. A missing pool is honestly empty (no strategies, no entries);
  a malformed or spec-invalid pool raises so the cycle can refuse to route on
  tampered data rather than trade on whatever half-parses.
- ``strategy_candidates.jsonl`` — append-only candidates (C7 import provenance now,
  C8 factory output later). Candidates never route; only the active pool does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from ..errors import ToolError
from ..filelock import locked
from .cost import DEFAULT_MAKER_FEE_BPS, DEFAULT_SLIPPAGE_BPS, DEFAULT_TAKER_FEE_BPS
from .paper import OCCUPYING_STATUSES, state_dir
from .robustness import HOLDOUT_CONFIRMED, ROBUST, classify_verdict, verdict_rank
from .strategy import SpecParseError, StrategySpec, load_strategy_pool

POOL_FILENAME = "active_strategy_pool.json"
CANDIDATES_FILENAME = "strategy_candidates.jsonl"

# The basis of every R in a candidate's quality view.
#
# These figures come from `backtest_evidence`, and the factory backtest charges costs:
# `factory.backtest_spec` runs every closed trade through `cost.apply_cost_model` and states
# that `result_R` — and therefore `expectancy` and `champion_score` — is the NET R after fees
# and slippage, with `gross_R` alongside. The holdout aggregates are built the same way.
#
# The previous value here said the opposite. It came from reading `robustness.py`'s "the cost
# model was not ported" as a statement about R; it is a statement about the scorer's
# cost-ROBUSTNESS term — whether the edge is stable ACROSS cost assumptions — which is a
# different property from whether costs were charged at all.
EDGE_COST_BASIS_NET = "net_of_fees_and_slippage"

# ...and at WHICH rates, because that is no longer one answer for the whole store. The taker
# default moved from the ported 2.5 bps to the venue's measured 5.0, and `backtest_evidence`
# is durable — candidates scored before the change keep the numbers they were scored with.
# Ranking them against newer ones is comparing a cheaper venue to the real one, so the basis
# has to travel WITH each candidate rather than be assumed for the view.
EDGE_COST_BASIS_UNRECORDED = "cost_model_unrecorded"


def _is_number(value: Any) -> bool:
    """A real number, not a bool — ``isinstance(True, int)`` is True and would rescale on it."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def expectancy_at(
    record: Mapping[str, Any], *, taker_fee_bps: float, maker_fee_bps: float | None = None,
) -> float | None:
    """This candidate's expectancy re-derived at different fee rates. Exact, or None.

    Raising the taker default split the store: 224 candidates on this machine keep numbers
    scored at 2.5 bps while the venue charges 5.0, and `backtest_evidence` is durable so
    nothing re-scores them. Re-running the backtest is not available either — the snapshot
    that produced the evidence is not stored, only its hash.

    But the conversion needs neither. In `cost.apply_cost_model` the taker term

        taker_fee_cost_r = (taker-charged fills) * taker_fee_bps / 10000 / risk

    is **linear in the rate**, and the fills depend only on slippage. So changing the taker
    rate alone scales the recorded taker fee cost and leaves everything else untouched:

        total_net_r(new) = total_net_r(old) - total_taker_fee_cost_r(old) * (new/old - 1)

    Both terms are already in `cost_summary`. The result is exact, not an estimate — a test
    pins it against a real backtest re-run at the new rate rather than asserting the algebra.

    **The two legs scale independently.** Since 2026-07-28 a take-profit exit rests as a maker
    LIMIT and is charged the maker rate, so `total_fee_cost_r` is a mixture. Scaling the whole
    mixture by a taker ratio would charge the maker leg a rate it never faced — so the maker
    share is separated first, read from `total_maker_fee_cost_r`, and each share is scaled by
    its own ratio:

        total_net_r(new) = total_net_r(old)
                         - taker_share * (new_taker/old_taker - 1)
                         - maker_share * (new_maker/old_maker - 1)

    The maker term exists because the maker rate is the one figure here that is **not
    measured**: `DEFAULT_MAKER_FEE_BPS` is Binance's published standard rate, and no maker fill
    has been placed yet to check it against. Its error direction is the unsafe one — a real rate
    above 2.0 bps means this model reports an edge better than reality. Making the maker leg
    rescalable *before* the first candidate is scored under it is what keeps that eventual
    measurement from splitting the store a third time: every candidate scored at the published
    rate converts exactly to the measured one, the same way the taker change already converts.

    ``maker_fee_bps=None`` leaves the maker share alone — the caller is asking only about the
    taker axis. A record whose maker share is zero is unaffected either way: a model with no
    maker leg has nothing on that axis to rescale, and that is arithmetic, not an assumption.
    A record with a maker share but no recorded maker rate refuses, because the ratio's
    denominator would be a guess.

    Returns None when the record predates `cost_summary`, or carries no closed trades, or
    was scored at a rate of zero (nothing to scale). Never guesses.
    """
    evidence = record.get("backtest_evidence") or {}
    summary = evidence.get("cost_summary") or {}
    model = summary.get("cost_model") or {}
    old_rate = model.get("taker_fee_bps")
    net, fee = summary.get("total_net_r"), summary.get("total_fee_cost_r")
    closed = evidence.get("closed_count")
    if not all(_is_number(v) for v in (old_rate, net, fee, closed)):
        return None
    if not old_rate or not closed:
        return None
    maker_fee = summary.get("total_maker_fee_cost_r", 0.0)
    if not _is_number(maker_fee):
        # Present but unreadable is not the same as absent: absent means all-taker, unreadable
        # means the split is unknown and any rescale would be a guess about real money.
        return None
    adjusted = net - (fee - maker_fee) * (taker_fee_bps / old_rate - 1.0)
    if maker_fee_bps is not None and maker_fee:
        old_maker = model.get("maker_fee_bps")
        if not _is_number(old_maker) or not old_maker:
            return None
        adjusted -= maker_fee * (maker_fee_bps / old_maker - 1.0)
    return round(adjusted / closed, 8)


def cost_basis_of(record: Mapping[str, Any]) -> str:
    """The cost model one candidate was actually scored under, from its own evidence.

    `factory.backtest_spec` records it in `cost_summary.cost_model`, so this reads what the
    scoring used rather than what the module currently defaults to. A record predating that
    field reports UNRECORDED — not the current default, which would claim a candidate had
    paid a rate it never faced.

    The maker rate joins the string only when the record carries one. That is deliberate: a
    candidate scored before the maker take-profit exit (2026-07-28) keeps the exact basis
    string it has always reported, so the split in the store stays legible as two bases rather
    than every old candidate silently acquiring a third term it was never scored under.
    """
    summary = (record.get("backtest_evidence") or {}).get("cost_summary") or {}
    model = summary.get("cost_model") or {}
    taker, slip = model.get("taker_fee_bps"), model.get("slippage_bps")
    if not isinstance(taker, (int, float)) or not isinstance(slip, (int, float)):
        return EDGE_COST_BASIS_UNRECORDED
    maker = model.get("maker_fee_bps")
    maker_term = f"+maker_{maker}bps" if isinstance(maker, (int, float)) else ""
    return f"{EDGE_COST_BASIS_NET}:taker_{taker}bps{maker_term}+slip_{slip}bps"


def current_cost_basis() -> str:
    """The basis a candidate minted right now would carry.

    Formatted by `cost_basis_of` over a synthetic record rather than by a second format
    string, so the "what the store holds" and "what the model charges" sides cannot drift
    into two spellings of the same rates."""
    return cost_basis_of({"backtest_evidence": {"cost_summary": {"cost_model": {
        "taker_fee_bps": DEFAULT_TAKER_FEE_BPS,
        "maker_fee_bps": DEFAULT_MAKER_FEE_BPS,
        "slippage_bps": DEFAULT_SLIPPAGE_BPS,
    }}}})


# How one candidate's basis stands against the model the venue charges today. Ordered, because
# the ONLY thing that matters about a stale basis is which way its error points.
#
# Equality is the wrong test and was the first thing tried. On this machine 90 of 359 candidates
# are scored at taker 5.0 with no maker leg: their take-profit exit paid taker 5.0 plus adverse
# slippage where the current model charges maker 2.0 and no slippage at all. Those numbers are
# too PESSIMISTIC, not too generous — refusing them would have made the escape hatch the normal
# door, and a gate everyone escapes is not a gate.
COST_BASIS_RANK_CURRENT = 0       # scored under exactly this model
COST_BASIS_RANK_CONSERVATIVE = 1  # every rate at or above the current one: understates the edge
COST_BASIS_RANK_OPTIMISTIC = 2    # some rate BELOW the current one: overstates the edge
COST_BASIS_RANK_UNRECORDED = 3    # no cost model recorded: the direction is unknown

# Which of those may back a promotion. Optimistic and unrecorded evidence is refused at the
# door — the first inflates the number an operator reads, the second cannot even say whether
# it does. Conservative evidence promotes: its error runs against the candidate, so a lineage
# that clears the bar under it clears the bar under the real model too.
PROMOTABLE_COST_BASIS_RANKS = frozenset({COST_BASIS_RANK_CURRENT, COST_BASIS_RANK_CONSERVATIVE})


def cost_basis_rank(record: Mapping[str, Any]) -> int:
    """Which `COST_BASIS_RANK_*` tier this candidate's evidence falls in.

    One authority for two consumers: `rank_candidates` orders by it so cheap-venue rows stop
    outranking real ones, and `assert_promotable_cost_basis` refuses on it at the promotion
    door. A single rule means the list an operator reads and the gate that stops them can
    never disagree about which rows are believable."""
    model = ((record.get("backtest_evidence") or {}).get("cost_summary") or {}).get("cost_model") or {}
    taker, slip = model.get("taker_fee_bps"), model.get("slippage_bps")
    if not _is_number(taker) or not _is_number(slip):
        return COST_BASIS_RANK_UNRECORDED
    maker = model.get("maker_fee_bps")
    if taker == DEFAULT_TAKER_FEE_BPS and maker == DEFAULT_MAKER_FEE_BPS and slip == DEFAULT_SLIPPAGE_BPS:
        return COST_BASIS_RANK_CURRENT
    # A record with no maker rate charged its exit at the TAKER rate — that model had no maker
    # leg at all, so the honest comparison against today's maker rate is what the exit actually
    # paid, not a missing field treated as zero (which would read every legacy row as optimistic).
    maker_charged = maker if _is_number(maker) else taker
    if (taker >= DEFAULT_TAKER_FEE_BPS and maker_charged >= DEFAULT_MAKER_FEE_BPS
            and slip >= DEFAULT_SLIPPAGE_BPS):
        return COST_BASIS_RANK_CONSERVATIVE
    return COST_BASIS_RANK_OPTIMISTIC


def assert_promotable_cost_basis(records: list[Mapping[str, Any]]) -> None:
    """Refuse a promotion backed by evidence scored more cheaply than the venue charges.

    The store is append-only and `backtest_evidence` is durable, so a stale basis can never be
    repaired in place — the only place it can be caught is the door where evidence turns into
    real money. `expectancy` alone is re-derivable at the current rates (`expectancy_at`), but
    win-rate, realized reward:risk and the robustness verdict all need per-trade signs the
    store does not keep, so a candidate cannot simply be re-read at today's model.

    Raises `CANDIDATE_COST_BASIS_STALE`, naming every offending candidate and its basis."""
    stale = [
        (candidate_id(record), cost_basis_of(record))
        for record in records
        if cost_basis_rank(record) not in PROMOTABLE_COST_BASIS_RANKS
    ]
    if stale:
        listed = ", ".join(f"{cid} ({basis})" for cid, basis in stale)
        raise ToolError(
            "CANDIDATE_COST_BASIS_STALE",
            f"scored under a cost model cheaper than the venue charges "
            f"({current_cost_basis()}), so their expectancy is overstated: {listed}. "
            f"Re-mint the lineage at the current model, or pass the explicit "
            f"--allow-stale-cost-basis escape.",
        )


# --- candidate identity (single source) ----------------------------------------

def derive_candidate_id(record: Mapping[str, Any]) -> str:
    """The globally unique id of one candidate: its lineage, not its display name.

    ``strategy_id`` restarts at S001 every factory generation, so it can never key a
    lookup. The id derives from (generation_id, strategy_rule_hash,
    evidence_input_sha256) — the exact strategy content in its exact generation with
    its exact evidence window — so legacy rows without a stored ``candidate_id``
    derive the same id on every read and the append-only store is never rewritten."""
    return integrity.short_id("cand", {
        "generation_id": record.get("generation_id"),
        "strategy_rule_hash": record.get("strategy_rule_hash"),
        "evidence_input_sha256": record.get("evidence_input_sha256"),
    })


def candidate_id(record: Mapping[str, Any]) -> str:
    stored = record.get("candidate_id")
    if isinstance(stored, str) and stored:
        return stored
    return derive_candidate_id(record)


# --- candidate lineage (fusion groundwork) --------------------------------------

# Closed set. ``seeded_template`` is fresh generation from the template library
# (no parents); the parented types name how a fused/derived child was produced.
# The factory ops that MINT parented candidates are a separate increment — the
# store admits them so the schema is one authority, not per-writer convention.
DERIVATION_TYPES = frozenset({"seeded_template", "crossover", "mutation"})
_PARENT_COUNT_RULES = {"seeded_template": (0, 0), "mutation": (1, 1), "crossover": (2, None)}


def validate_candidate_lineage(record: Mapping[str, Any], known_ids: frozenset[str]) -> None:
    """Fail-closed lineage check for one candidate row, at the append door.

    Rows written before lineage existed carry neither field and pass untouched
    (the ``candidate_id`` legacy rule — the append-only store is never rewritten).
    A row that does claim a derivation must be coherent: a known type, parents as
    a duplicate-free list of non-empty strings whose count fits the type (seeded
    has none, a mutation has exactly one, a crossover at least two), and every
    parent already durable in this store — so a child can never cite evidence
    that does not exist."""
    has_type = "derivation_type" in record
    has_parents = "parent_candidate_ids" in record
    if not has_type and not has_parents:
        return  # legacy row
    derivation = record.get("derivation_type")
    if not has_type:
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "parent_candidate_ids without a derivation_type")
    if derivation not in DERIVATION_TYPES:
        raise ToolError("CANDIDATE_LINEAGE_INVALID", f"unknown derivation_type: {derivation!r}")
    parents = record.get("parent_candidate_ids", [])
    if not isinstance(parents, list) or not all(isinstance(p, str) and p for p in parents):
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "parent_candidate_ids must be a list of non-empty ids")
    if len(set(parents)) != len(parents):
        raise ToolError("CANDIDATE_LINEAGE_INVALID", "duplicate parent_candidate_ids")
    lo, hi = _PARENT_COUNT_RULES[derivation]
    if len(parents) < lo or (hi is not None and len(parents) > hi):
        raise ToolError(
            "CANDIDATE_LINEAGE_INVALID",
            f"derivation_type {derivation!r} admits {lo}{'+' if hi is None else f'..{hi}'} parents, got {len(parents)}",
        )
    unknown = [p for p in parents if p not in known_ids]
    if unknown:
        raise ToolError("UNKNOWN_PARENT_CANDIDATE", f"parents not in the candidate store: {unknown}")


def resolve_candidates(selectors: list[str], root: Path | None = None) -> list[dict[str, Any]]:
    """Resolve operator selectors to candidate records, fail-closed.

    A selector is a ``candidate_id`` (exact) or a ``strategy_id`` (convenience). A
    strategy_id matching candidates from more than one lineage refuses with
    ``CANDIDATE_AMBIGUOUS`` — never silently the newest — and an unmatched selector
    refuses with ``UNKNOWN_CANDIDATE``. Returned records are stamped with their
    ``candidate_id``; re-appends of the same lineage collapse latest-wins."""
    by_cid: dict[str, dict[str, Any]] = {}
    for record in read_candidates(root):
        cid = candidate_id(record)
        by_cid[cid] = {**record, "candidate_id": cid}

    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    for selector in selectors:
        if selector in by_cid:
            resolved.append(by_cid[selector])
            continue
        matches = [r for r in by_cid.values() if r.get("strategy_id") == selector]
        if not matches:
            missing.append(selector)
        elif len(matches) > 1:
            ambiguous[selector] = sorted(r["candidate_id"] for r in matches)
        else:
            resolved.append(matches[0])
    if missing:
        raise ToolError("UNKNOWN_CANDIDATE", f"unknown candidate selectors: {missing}")
    if ambiguous:
        raise ToolError(
            "CANDIDATE_AMBIGUOUS",
            f"strategy ids matching multiple lineages, use candidate ids: {ambiguous}",
        )
    seen: set[str] = set()
    for record in resolved:
        if record["candidate_id"] in seen:
            raise ToolError("DUPLICATE_SELECTOR", f"candidate selected twice: {record['candidate_id']}")
        seen.add(record["candidate_id"])
    return resolved


def pool_path(root: Path | None = None) -> Path:
    return state_dir(root) / POOL_FILENAME


def candidates_path(root: Path | None = None) -> Path:
    return state_dir(root) / CANDIDATES_FILENAME


def assert_pool_identity_unique(pool: Mapping[str, Any]) -> None:
    """No two active entries may share a ``strategy_id`` or a ``candidate_id``.

    Both are keys the runtime resolves by: ``strategy_id`` selects the champion and
    keys every lifecycle status update, ``candidate_id`` names the lineage an outcome
    is attributed to. A duplicate makes routing, demotion and attribution ambiguous —
    the pool would silently pick one entry and update the other. Fail-closed at both
    doors (install and read) so a duplicate can neither be written nor traded on."""
    seen_strategy: set[str] = set()
    seen_candidate: set[str] = set()
    for entry in pool.get("active_strategies") or []:
        strategy_id = entry.get("strategy_id")
        if isinstance(strategy_id, str) and strategy_id:
            if strategy_id in seen_strategy:
                raise ToolError("STRATEGY_POOL_DUPLICATE", f"duplicate strategy_id in the pool: {strategy_id}")
            seen_strategy.add(strategy_id)
        candidate_id = entry.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id:
            if candidate_id in seen_candidate:
                raise ToolError("STRATEGY_POOL_DUPLICATE", f"duplicate candidate_id in the pool: {candidate_id}")
            seen_candidate.add(candidate_id)


def load_active_pool(root: Path | None = None) -> dict[str, Any]:
    """The active pool, validated spec-by-spec and identity-unique. Missing = empty."""
    path = pool_path(root)
    if not path.is_file():
        return {"active_strategies": []}
    try:
        pool = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ToolError("STRATEGY_POOL_UNREADABLE", f"active strategy pool unreadable: {type(exc).__name__}") from exc
    try:
        load_strategy_pool(pool)  # fail-closed structural validation, one bad spec poisons
    except SpecParseError as exc:
        raise ToolError("STRATEGY_POOL_INVALID", f"active strategy pool failed validation: {exc}") from exc
    assert_pool_identity_unique(pool)
    return pool


def routable_contexts(pool: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Distinct ``(symbol, timeframe)`` pairs the active pool can route on.

    One pair per ``(symbol_scope entry, timeframe)`` — every symbol a strategy is
    scoped to, exactly what :func:`paper.route_entries` now matches on — so a
    fan-out proposes a cycle for every context a strategy could fire in (a
    multi-symbol strategy contributes each of its symbols) and none where it never
    could. Non-occupying or spec-less entries contribute nothing. Deduplicated and
    sorted for a stable, deterministic cycle order."""
    contexts: set[tuple[str, str]] = set()
    for entry in pool.get("active_strategies") or []:
        if entry.get("status") not in OCCUPYING_STATUSES or not entry.get("strategy_spec"):
            continue
        spec = StrategySpec.from_dict(entry["strategy_spec"])
        for scoped_symbol in spec.symbol_scope:
            contexts.add((str(scoped_symbol), str(spec.timeframe)))
    return sorted(contexts)


def install_active_pool(pool: dict[str, Any], *, root: Path | None = None) -> int:
    """Install (replace) the active pool — the OPERATOR door, not a runtime call.

    Validates every spec and the identity invariant first (fail-closed), then writes
    atomically. Returns the number of strategies installed. Callers are operator
    scripts acting on an explicit confirmation (the pre-R10 promotion posture); the
    runtime cycle never calls this."""
    specs = load_strategy_pool(pool)
    assert_pool_identity_unique(pool)
    path = pool_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return len(specs)


def update_statuses(
    decisions: list[dict[str, Any]], *, root: Path | None = None, updated_by: str = "lifecycle_agent"
) -> int:
    """Apply lifecycle status transitions to the active pool (C10). Locked, guarded.

    The narrowest possible pool mutation: only ``status`` and the running
    ``lifecycle_consecutive_failures`` of named strategies change — specs, hashes,
    scores and membership are untouched, so this can never smuggle a promotion.
    Guards, each fail-closed: unknown strategy id refused; a CURRENTLY terminal
    entry is immutable (reactivation is the approval door, never this); and a
    transition record that isn't an evaluate_lifecycle decision shape is refused.
    Returns the number of entries whose status actually changed."""
    from .lifecycle import TERMINAL_STATUSES  # local: avoids a module cycle

    if not decisions:
        return 0
    path = pool_path(root)
    with locked(path.with_suffix(".lock"), code="STRATEGY_POOL_LOCKED", label="active strategy pool"):
        pool = load_active_pool(root)
        entries = {e.get("strategy_id"): e for e in pool.get("active_strategies") or []}
        changed = 0
        for decision in decisions:
            strategy_id = decision.get("strategy_id")
            new_status = decision.get("new_status")
            if not (isinstance(strategy_id, str) and strategy_id and isinstance(new_status, str)):
                raise ToolError("LIFECYCLE_DECISION_INVALID", "transition lacks strategy_id/new_status")
            entry = entries.get(strategy_id)
            if entry is None:
                raise ToolError("LIFECYCLE_UNKNOWN_STRATEGY", f"no pool entry for {strategy_id}")
            if str(entry.get("status")) in TERMINAL_STATUSES:
                raise ToolError(
                    "LIFECYCLE_TERMINAL_IMMUTABLE",
                    f"{strategy_id} is terminal; reactivation is the approval door, not a transition",
                )
            entry["lifecycle_consecutive_failures"] = int(decision.get("consecutive_failures") or 0)
            if new_status != entry.get("status"):
                entry["status"] = new_status
                entry["lifecycle_updated_at"] = decision.get("created_at_utc")
                entry["lifecycle_decision_id"] = decision.get("strategy_lifecycle_decision_id")
                changed += 1
        pool["updated_by"] = updated_by
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return changed


def read_candidates(root: Path | None = None) -> list[dict[str, Any]]:
    """All candidate rows, oldest first — a VERIFIED read.

    Any row carrying a ``record_sha256`` (everything :func:`append_candidates` has
    written since the store began stamping) must recompute it exactly; a mismatch
    raises ``CANDIDATES_TAMPERED`` so promotion asks/executions fail closed rather
    than binding Thomas's approval to silently edited evidence. Rows persisted
    before stamping existed have no hash to check — documented gap, closed for
    every new row."""
    path = candidates_path(root)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ToolError("CANDIDATES_UNREADABLE", f"strategy candidates unreadable: {exc.strerror}") from exc
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise ToolError("CANDIDATES_UNREADABLE", f"strategy candidates line {i + 1} is not valid JSON") from exc
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        if stored is not None:
            body = {k: v for k, v in record.items() if k != "record_sha256"}
            if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
                raise ToolError(
                    "CANDIDATES_TAMPERED", f"strategy candidates line {i + 1} fails its self-hash"
                )
        rows.append(record)
    return rows


def append_candidates(records: list[dict[str, Any]], *, root: Path | None = None) -> int:
    """Append candidate records (operator/import door). Returns the count written.

    The store stamps each row's ``record_sha256`` at append time (over the full row,
    import marks included), so tamper evidence starts the moment a row becomes
    durable — provenance-independent, unlike the outcomes store's build-time hash.

    Lineage is validated under the same lock, against the rows durable BEFORE this
    batch — a parent must already exist in the store, never in the batch that cites
    it (fusion reads its parents from the store first). All-or-nothing: one invalid
    row refuses the whole batch before anything is written."""
    path = candidates_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path.with_suffix(".lock"), code="CANDIDATES_LOCKED", label="strategy candidates"):
        known_ids = frozenset(candidate_id(r) for r in read_candidates(root))
        for record in records:
            validate_candidate_lineage(record, known_ids)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                row = dict(record)
                if "record_sha256" not in row:
                    row["record_sha256"] = integrity.sha256_record(row)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(records)


# --- candidate ranking (M4a): robustness first-pass, win-rate + reward:risk second -

# A payoff ratio a losing-free backtest can't divide out. It floats an all-wins
# lineage to the top of its robustness tier for the sort only; the displayed
# reward:risk stays honest (None → "∞"), so this cap is never shown as a real ratio.
_ALL_WINS_RR_SORT = float("inf")


def _as_float(value: Any) -> float:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _designed_reward_risk(record: Mapping[str, Any]) -> float | None:
    """target_atr / stop_atr from the spec — the legacy fallback when a candidate
    predates the realized avg_win_R/avg_loss_R evidence. None if it can't be read."""
    exit_rules = ((record.get("strategy_spec") or {}).get("exit_rules")) or {}
    stop = _as_float(exit_rules.get("stop_atr"))
    target = _as_float(exit_rules.get("target_atr"))
    return round(target / stop, 8) if stop > 0 and target > 0 else None


def candidate_quality(record: Mapping[str, Any]) -> dict[str, Any]:
    """The ranking view of one candidate: robustness tier + realized performance.

    First-pass ``verdict_rank`` (ROBUST < PROVISIONAL < FRAGILE < unknown) never
    changes with performance — the anti-overfit filter stays authoritative. The
    second-pass axes are ``win_rate`` and the realized ``reward_risk`` (avg_win_R /
    avg_loss_R); ``edge_quality = win_rate * reward_risk`` combines them so a lineage
    strong on *both* outranks one strong on either alone. A candidate with no losing
    trades has an undefined ratio (``reward_risk`` None, ``all_wins`` True); one
    predating the realized evidence falls back to the designed target/stop ratio
    (``reward_risk_basis`` ``"designed"``)."""
    evidence = record.get("backtest_evidence") or {}
    robustness = evidence.get("robustness") or {}
    # Out-of-sample status rides into the ranking view so the promotion door can show
    # it: with ROBUST now gated on it, "PROVISIONAL because unconfirmed" and
    # "PROVISIONAL because it failed forward" are very different things to promote.
    holdout_state = str(robustness.get("holdout_status") or "UNCONFIRMED")
    # The verdict is RECOMPUTED from the stored components, never read back as a label.
    #
    # It used to be read: `robustness.get("verdict")`. Verdicts are written once, at mint
    # time, under whatever rule was current then — and the rule changed when ROBUST became
    # gated on out-of-sample survival. Candidates minted before that kept a stored ROBUST
    # while their holdout read UNCONFIRMED, a pair the rule can no longer produce. Measured
    # on this machine: 12 of 269 candidates, and because `rank_candidates` orders by verdict
    # tier FIRST, all 12 sorted above the 13 PROVISIONAL+CONFIRMED lineages that had actually
    # survived unseen bars. The shortlist was inverted on exactly the property the holdout
    # rule was added to enforce.
    #
    # `classify_verdict` is the one authority for the rule, so a later change to it cannot
    # leave stale labels behind again. A record missing the components keeps its stored
    # verdict — recomputing from absent inputs would invent a rating, not correct one.
    stored_verdict = robustness.get("verdict")
    score = robustness.get("robustness_score")
    tpp = robustness.get("trades_per_parameter")
    if isinstance(score, (int, float)) and isinstance(tpp, (int, float)):
        verdict = classify_verdict(float(score), float(tpp), holdout_state)
    else:
        verdict = stored_verdict
    closed = int(_as_float(evidence.get("closed_count")))
    win_count = int(_as_float(evidence.get("win_count")))
    win_rate = round(win_count / closed, 8) if closed else 0.0

    all_wins = False
    if "avg_win_R" in evidence or "avg_loss_R" in evidence:
        basis = "realized"
        avg_win = _as_float(evidence.get("avg_win_R"))
        avg_loss = _as_float(evidence.get("avg_loss_R"))
        if avg_loss > 0:
            reward_risk: float | None = round(avg_win / avg_loss, 8)
        elif avg_win > 0:
            reward_risk, all_wins = None, True  # no losses to divide by
        else:
            reward_risk = 0.0
    else:
        reward_risk = _designed_reward_risk(record)
        basis = "designed" if reward_risk is not None else "none"

    rr_sort = _ALL_WINS_RR_SORT if all_wins else (reward_risk or 0.0)
    return {
        "candidate_id": candidate_id(record),
        "verdict": verdict,
        "verdict_rank": verdict_rank(verdict),
        "holdout_status": holdout_state,
        "robustness_score": round(_as_float(record.get("champion_score")), 8),
        "win_rate": win_rate,
        "reward_risk": reward_risk,
        "reward_risk_basis": basis,
        "all_wins": all_wins,
        "expectancy": round(_as_float(evidence.get("expectancy")), 8),
        "closed_count": closed,
        "edge_quality": win_rate * rr_sort,
        # What every R above does NOT include. Paper settlement models no fee, slippage or
        # funding by design ("Accounting is R-based only... paper sizing added nothing but
        # noise"), and the robustness scorer withholds its cost term for the same reason
        # ("the cost model was not ported, so cost_robustness inputs are withheld"). Both
        # are honest about it in their own docstrings; the promotion surface — the one an
        # operator actually reads before putting real money behind a lineage — said nothing,
        # so a cost-free expectancy arrived looking like a net one.
        #
        # A field rather than a printed sentence because it is a property OF the number: a
        # later cost-adjusted basis becomes a different value here, and any consumer that
        # compares two candidates can refuse to compare across bases.
        "cost_basis": cost_basis_of(record),
        # ...and which way that basis errs against the model in force today. The string says
        # WHAT this row paid; this says whether reading it next to a current row flatters it.
        "cost_basis_rank": cost_basis_rank(record),
        # The same expectancy at the rates the venue actually charges, so a candidate scored
        # under the old default can be read against a new one instead of merely flagged as
        # incomparable. None when it cannot be derived — never the stored number relabelled.
        #
        # Both axes, not just the taker one: the maker rate is published rather than measured,
        # so the day it IS measured this view converts every maker-scored candidate rather than
        # stranding it. Records with no maker leg are untouched by the maker argument.
        #
        # Alongside `expectancy` rather than replacing it: the stored figure is what the
        # durable evidence says, and overwriting it would make the record and the view
        # disagree about what was measured.
        "expectancy_at_current_costs": expectancy_at(
            record, taker_fee_bps=DEFAULT_TAKER_FEE_BPS, maker_fee_bps=DEFAULT_MAKER_FEE_BPS,
        ),
    }


def rank_candidates(records: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Candidates ordered for the promotion decision, latest-wins per lineage.

    Deterministic total order: **cost basis tier first**, then robustness verdict tier (the
    anti-overfit first-pass), then ``edge_quality`` (win-rate × realized reward:risk)
    descending, then ``expectancy`` descending, then ``candidate_id`` ascending so a tie never
    depends on store order. Re-appends of a lineage collapse to the latest row.

    The basis tier leads because every key after it is a number scored under that basis, and
    269 of 359 rows on this machine were scored under a cheaper one. `verdict` is recomputed
    but from a `robustness_score` fitted at the old rate; `edge_quality` and `expectancy` are
    read straight off the old evidence. Sorting those together put candidates that never paid
    the real fee above candidates that did, and the surface said so in a printed warning while
    the ranking underneath went on mixing them — the same shape as the stored-verdict bug
    below, which was also fixed by ordering on the property rather than describing it."""
    by_cid: dict[str, dict[str, Any]] = {}
    for record in records:
        cid = candidate_id(record)
        by_cid[cid] = {**record, "candidate_id": cid}

    def _key(record: Mapping[str, Any]) -> tuple[int, int, float, float, str]:
        q = candidate_quality(record)
        return (q["cost_basis_rank"], q["verdict_rank"], -q["edge_quality"], -q["expectancy"],
                str(record["candidate_id"]))

    return sorted(by_cid.values(), key=_key)


# How many promotable lineages may wait before the daily board says so. Mirrors the
# proposer's unreviewed-family cap (M4b) in intent and differs in effect: that cap makes
# a fire SKIP, this one only speaks. Nothing here refuses anything — the promotion door
# stays exactly as manual as it was.
PROMOTION_BACKLOG_ALERT_THRESHOLD = 5


def _lineage_key(spec: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two strategies the SAME promotion decision: one family on one context.

    Not the rule hash — the factory mints a fresh hash for every parameter tweak of the
    same family on the same symbol and timeframe, and an operator choosing between them is
    filling one slot, not making four decisions."""
    return (
        spec.get("strategy_family"),
        tuple(spec.get("symbol_scope") or ()),
        spec.get("timeframe"),
    )


def promotable_backlog(
    root: Path | None = None,
    *,
    candidates: list[Mapping[str, Any]] | None = None,
    active_pool: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """How many lineages an operator could promote right now and has not.

    Deliberately NOT the candidate count. The factory mints dozens a day, so that number
    only ever rises and a threshold on it fires every morning until it is ignored. What
    an operator can actually act on is the far smaller set that would clear the door
    TODAY — so this applies the same chain ``--list`` and the promotion gate apply, in
    the same order:

    - evidence at a basis the door accepts (:data:`PROMOTABLE_COST_BASIS_RANKS`); an
      OPTIMISTIC row is refused at the ask, so counting it would advertise work that
      cannot be done
    - ROBUST on the *recomputed* verdict, and CONFIRMED out-of-sample
    - positive expectancy at the CURRENT rates, not at whatever rate it was scored under
    - one row per (family, symbol scope, timeframe), counting the active pool's own
      members first: the factory re-mints the same lineage every generation, so this
      collapses the re-mints AND drops the ones whose slot an operator already filled

    That last rule is one rule on purpose. Excluding pool members by ``strategy_rule_hash``
    while collapsing candidates by lineage mixes two granularities, and the backlog then
    never drains: promoting one re-mint leaves its siblings — same family, same context,
    different rule hash — to resurface as fresh backlog the next morning, forever. Measured
    here: 7 reported where 4 were waiting, the other 3 being siblings of rows promoted
    minutes earlier. The hash check stays as well, for pool entries whose spec cannot
    supply a lineage.

    Read-only, and it decides nothing: the count exists so the daily board can say a
    queue formed. Ids come back in :func:`rank_candidates` order, so the first one named
    is the first one an operator would read.
    """
    records = candidates if candidates is not None else read_candidates(root)
    pool_doc = active_pool if active_pool is not None else load_active_pool(root)
    active_entries = pool_doc.get("active_strategies") or []
    active_hashes = {entry.get("strategy_rule_hash") for entry in active_entries}
    seen_lineages: set[tuple[Any, ...]] = {
        _lineage_key(entry.get("strategy_spec") or {}) for entry in active_entries
    }

    candidate_ids: list[str] = []
    for record in rank_candidates(list(records)):
        if record.get("strategy_rule_hash") in active_hashes:
            continue
        quality = candidate_quality(record)
        if quality["cost_basis_rank"] not in PROMOTABLE_COST_BASIS_RANKS:
            continue
        if quality["verdict"] != ROBUST or quality["holdout_status"] != HOLDOUT_CONFIRMED:
            continue
        expectancy = quality["expectancy_at_current_costs"]
        if not isinstance(expectancy, (int, float)) or expectancy <= 0:
            continue
        lineage = _lineage_key(record.get("strategy_spec") or {})
        if lineage in seen_lineages:
            continue
        seen_lineages.add(lineage)
        candidate_ids.append(candidate_id(record))

    return {
        "count": len(candidate_ids),
        "threshold": PROMOTION_BACKLOG_ALERT_THRESHOLD,
        "candidate_ids": candidate_ids,
    }
