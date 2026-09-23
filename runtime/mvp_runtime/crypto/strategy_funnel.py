"""The strategy funnel: where the candidate store's lineages stop, and how far the forward cohort's
members have got (2026-09-23; item PR-LIVE-2 of an external follow-up plan). Reads only.

`promotion_backlog` answers "what would the door take today" and says why the rest are not there,
as one partition. The board shows that count and the cohort's leaders. Neither says whether a thin
queue is a discovery problem (few lineages get past their own evidence), a validation problem (they
stop at the holdout), or a forward problem (the cohort has not reached its trade floors). This
report splits the same numbers by timeframe, strategy family and direction, so that can be read.

**Two funnels, not one chain.** A cohort member can be refused at the door, and a door-refused row
can have forward outcomes, so the two are reported side by side rather than as one list:

- the **pool funnel** is the door's own chain. Each lineage (the store's rows collapsed by
  `rank_candidates`, the population `promotable_backlog` judges) is charged to the first axis of
  :data:`promotion_backlog.BACKLOG_REFUSAL_AXES` that drops it, through
  :func:`promotion_backlog.refusal_axis` itself, or counted ``promotable``. Its totals therefore
  equal the backlog's ``refused`` and ``count`` by construction;
- the **forward funnel** follows every frozen cohort member through `forward_cohort.cohort_report`:
  a member with priced rows, then one at its timeframe's trade floor (`min_forward_trades`), and
  the judge's status. Its status counts equal the board's.

The vocabulary is the repo's: the backlog's axes and the judge's statuses. Nothing here refuses,
ranks or decides anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .candidate_identity import candidate_id
from .candidate_ranking import attempts_by_context, pooled_context_keys, rank_candidates
from .forward_confirmation import min_forward_trades
from .forward_cohort import cohort_report
from .pool_state import load_active_pool, read_candidates
from .promotion_backlog import BACKLOG_REFUSAL_AXES, _lineage_key, refusal_axis

PROMOTABLE = "promotable"
FACETS = ("timeframe", "strategy_family", "direction")
FORWARD_STAGES = ("members", "with_outcomes", "at_trade_floor")
UNKNOWN = "?"
# The text shows this many values per facet, largest first; fused families alone run past a hundred
# on today's store. ``--json`` carries every value.
TEXT_VALUES_PER_FACET = 12


def facets_of(spec: Mapping[str, Any] | None) -> dict[str, str]:
    """The three breakdowns of one lineage, read off its spec; ``?`` where the spec does not say."""
    spec = spec or {}
    return {facet: str(spec.get(facet) or UNKNOWN) for facet in FACETS}


def _count(by: dict[str, dict[str, dict[str, int]]], facets: Mapping[str, str], key: str) -> None:
    for facet, value in facets.items():
        bucket = by.setdefault(facet, {}).setdefault(value, {})
        bucket[key] = bucket.get(key, 0) + 1


def pool_funnel(records: Sequence[Mapping[str, Any]], active_pool: Mapping[str, Any]) -> dict[str, Any]:
    """The door's chain over the store's lineages, in `promotable_backlog`'s order and with its state,
    so each lineage lands on the axis the backlog charges it to."""
    rows = list(records)
    attempts = attempts_by_context(rows)
    pooled_keys = pooled_context_keys(rows)
    active_entries = active_pool.get("active_strategies") or []
    active_hashes = {entry.get("strategy_rule_hash") for entry in active_entries}
    seen_lineages = {_lineage_key(entry.get("strategy_spec") or {}) for entry in active_entries}
    outcomes = {axis: 0 for axis in (*BACKLOG_REFUSAL_AXES, PROMOTABLE)}
    by: dict[str, dict[str, dict[str, int]]] = {}
    ranked = rank_candidates(rows)
    for record in ranked:
        axis = refusal_axis(record, attempts=attempts, pooled_keys=pooled_keys,
                            active_hashes=active_hashes, seen_lineages=seen_lineages) or PROMOTABLE
        outcomes[axis] += 1
        _count(by, facets_of(record.get("strategy_spec")), axis)
    remaining = len(ranked)
    stages = []
    for axis in BACKLOG_REFUSAL_AXES:
        remaining -= outcomes[axis]
        stages.append({"after": axis, "dropped": outcomes[axis], "remaining": remaining})
    return {"rows": len(rows), "lineages": len(ranked), "outcomes": outcomes, "stages": stages, "by": by}


def forward_funnel(root: Path | None, records: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Every frozen cohort member: priced rows, the trade floor, the judge's status. None before
    any cohort is frozen."""
    report = cohort_report(root)
    if not report:
        return None
    specs = {candidate_id(record): record.get("strategy_spec") for record in records}
    counts: dict[str, int] = {}
    by: dict[str, dict[str, dict[str, int]]] = {}
    for member in (m for cohort in report for m in cohort["members"]):
        priced = int(member.get("priceable_count") or 0)
        facets = facets_of(specs.get(str(member.get("candidate_id"))))
        reached = ["members"]
        if priced > 0:
            reached.append("with_outcomes")
        if priced >= min_forward_trades(member.get("timeframe") or facets["timeframe"]):
            reached.append("at_trade_floor")
        reached.append(f"status:{member.get('status')}")
        for key in reached:
            counts[key] = counts.get(key, 0) + 1
            _count(by, facets, key)
    return {"cohorts": len(report), "counts": counts, "by": by}


def strategy_funnel(root: Path | None = None) -> dict[str, Any]:
    """Both funnels over one read of the candidate store."""
    records = read_candidates(root)
    return {"pool": pool_funnel(records, load_active_pool(root)), "forward": forward_funnel(root, records)}


def render_text(funnel: Mapping[str, Any]) -> list[str]:
    """ASCII lines for the operator's terminal. Reports only; nothing here is a verdict."""
    pool = funnel["pool"]
    lines = ["=== strategy funnel (read-only; decides nothing) ===",
             f"store rows {pool['rows']} | lineages judged {pool['lineages']} "
             f"(re-appends collapsed, as promotable_backlog counts)", "",
             "POOL DOOR - first axis that drops each lineage (promotable_backlog's chain)"]
    for stage in pool["stages"]:
        if stage["dropped"]:
            lines.append(f"  {stage['after']:<24} -{stage['dropped']:<6} left {stage['remaining']}")
    lines.append(f"  {PROMOTABLE:<24} {pool['outcomes'][PROMOTABLE]}")
    lines += _render_breakdown(pool["by"], order=(*BACKLOG_REFUSAL_AXES, PROMOTABLE))
    forward = funnel.get("forward")
    lines += ["", "FORWARD COHORT - every frozen member (option A: this opens no door)"]
    if forward is None:
        lines.append("  no cohort frozen yet")
        return lines
    counts = forward["counts"]
    statuses = sorted(k for k in counts if k.startswith("status:"))
    lines.append("  " + "  ".join(f"{stage} {counts.get(stage, 0)}" for stage in FORWARD_STAGES))
    lines.append("  " + "  ".join(f"{k.split(':', 1)[1]} {counts[k]}" for k in statuses))
    lines += _render_breakdown(forward["by"], order=(*FORWARD_STAGES, *statuses))
    return lines


def _render_breakdown(by: Mapping[str, Mapping[str, Mapping[str, int]]], *, order: Sequence[str]) -> list[str]:
    lines = []
    for facet in FACETS:
        values = by.get(facet) or {}
        if not values:
            continue
        lines.append(f"  by {facet}:")
        ordered = sorted(values, key=lambda v: (-sum(values[v].values()), v))
        for value in ordered[:TEXT_VALUES_PER_FACET]:
            cells = [f"{key.split(':', 1)[-1]}={values[value][key]}" for key in order if values[value].get(key)]
            lines.append(f"    {value:<22} " + " ".join(cells))
        if len(ordered) > TEXT_VALUES_PER_FACET:
            lines.append(f"    ... {len(ordered) - TEXT_VALUES_PER_FACET} more (--json lists every one)")
    return lines
