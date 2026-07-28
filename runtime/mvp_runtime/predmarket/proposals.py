"""Discovery runs, recorded — candidate generation on a cadence instead of on a whim.

``propose`` on the command line answers "what is pairable right now?". That is the wrong
shape for the question PM1 actually needs answered, which is "what became pairable while
nobody was looking?". Venues list new markets continuously; an operator who runs discovery
by hand sees whatever exists the moment they think to run it, and a pairing that appeared
and resolved between two of those moments leaves no trace that it was ever missed.

So discovery runs on a schedule and **writes down what it found**. Without that a scheduled
run is invisible: the same work, producing output nobody reads, at a cadence nobody can
audit.

Three properties, all borrowed from the stores next door rather than reinvented:

- **Append-only and self-hashed.** A proposal is the input to a decision about real money,
  so a row that cannot prove itself is refused rather than read.
- **It confirms nothing.** This module holds no writer for the group store, and every record
  says so on its face. Discovery is the operator's reading material.
- **The refusal counts travel with the candidates.** A run that proposed nothing because
  every market was screened out is a different finding from one that judged everything and
  matched nothing, and the record keeps both apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from ..errors import PersistenceError, ToolError
from ..filelock import locked
from ..jsonl import append_lines, read_objects
from . import pairs

PROPOSAL_RECORD_VERSION = "predmarket_proposal.v0.1"
PROPOSALS_FILENAME = "proposals.jsonl"

PROPOSALS_UNREADABLE = "PREDMARKET_PROPOSALS_UNREADABLE"
PROPOSALS_TAMPERED = "PREDMARKET_PROPOSALS_TAMPERED"
PROPOSALS_WRITE_FAILED = "PREDMARKET_PROPOSALS_WRITE_FAILED"
PROPOSALS_LOCKED = "PREDMARKET_PROPOSALS_LOCKED"

# How many candidates a run records. Discovery is reading material for a human, and a run
# that proposes four hundred pairings proposes nothing — but the total is recorded beside
# the kept rows, so a truncated list can never read as a complete one.
DEFAULT_PROPOSAL_LIMIT = 50


def proposals_path(root: Path | None = None) -> Path:
    return pairs.state_dir(root) / PROPOSALS_FILENAME


def _stamp(record: dict[str, Any]) -> dict[str, Any]:
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def _verify(record: Mapping[str, Any]) -> dict[str, Any]:
    stored = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(
            PROPOSALS_TAMPERED,
            "a stored discovery proposal does not match its own hash; it is the input to a "
            "decision about real money and will not be read as evidence",
        )
    return dict(record)


def build_proposal_record(
    result: Mapping[str, Any],
    *,
    now: str,
    limit: int = DEFAULT_PROPOSAL_LIMIT,
) -> dict[str, Any]:
    """One discovery run as a record. Pure — takes a ``matching.generate_candidates`` result.

    Near-misses are deliberately **not** carried. They are diagnostic material for tuning
    the rules and there are thousands of them; a store meant to be read by a person at a
    six-hour cadence would become unreadable within a day. Their count travels instead, so
    the record still says how much was refused.
    """
    candidates = list(result.get("candidates") or [])
    kept = candidates[: max(0, int(limit))]
    record = {
        "proposal_version": PROPOSAL_RECORD_VERSION,
        "matching_version": result.get("matching_version"),
        "generated_at_utc": now,
        "venues": list(result.get("venues") or []),
        "market_counts": dict(result.get("market_counts") or {}),
        "judged_count": result.get("judged_count"),
        "candidate_count": len(candidates),
        "candidates": kept,
        # No silent caps, the same rule the matcher's own near-miss list follows.
        "candidates_truncated": len(candidates) - len(kept),
        "near_miss_total": result.get("near_miss_total"),
        "boilerplate_only_count": result.get("boilerplate_only_count"),
        # Rates read during this run, so a candidate stays confirmable after the listing that
        # proposed it has rotated away. Only Binance needs it — Kalshi and Polymarket price
        # from a verified schedule — but the map is kept whole rather than venue-filtered,
        # because a schedule that stops being verified should not silently become a gap.
        # `generated_at_utc` above is how old any of it is.
        "fee_rate_bps": {str(k): float(v) for k, v in (result.get("fee_rate_bps") or {}).items()},
        # The open question, made countable. Rotation reads past each venue's busiest
        # markets on the theory that overlap also lives further down; nobody knows whether
        # it does. `candidates_from_tail` is the answer accumulating one run at a time — and
        # if it stays zero, rotation goes on evidence rather than on opinion.
        "rotation": result.get("rotation"),
        "candidates_from_tail": sum(
            1 for c in (result.get("candidates") or []) if c.get("discovery_slice") == "tail"
        ),
        # Why a venue contributed nothing: screened out, or unreadable. Kept apart, because
        # "the venue had no usable markets" and "the venue did not answer" would otherwise
        # both look like a quiet day.
        "screening": dict(result.get("screening") or {}),
        "venue_errors": dict(result.get("venue_errors") or {}),
        # Stated on the record itself, not only in this module's documentation.
        "confirms_nothing": True,
        "authorizes_trading": False,
    }
    record["proposal_id"] = integrity.short_id(
        "predmarket_proposal", {"at": now, "candidates": str(len(candidates))}
    )
    return record


def append_proposal(record: Mapping[str, Any], *, root: Path | None = None) -> int:
    """Append one discovery record, self-hashed. Returns how many landed (0 or 1)."""
    path = proposals_path(root)
    with locked(path.with_suffix(".lock"), code=PROPOSALS_LOCKED, label="predmarket proposals"):
        try:
            append_lines(
                path, [_stamp(dict(record))],
                write_code=PROPOSALS_WRITE_FAILED, label="predmarket proposal",
            )
        except PersistenceError as exc:
            raise ToolError(PROPOSALS_WRITE_FAILED, str(exc)) from exc
    return 1


def read_proposals(root: Path | None = None) -> list[dict[str, Any]]:
    """Every discovery record, hash-verified. Fails closed on corruption or tampering."""
    try:
        rows = read_objects(
            proposals_path(root), read_code=PROPOSALS_UNREADABLE, label="predmarket proposals"
        )
    except PersistenceError as exc:
        raise ToolError(PROPOSALS_UNREADABLE, str(exc)) from exc
    return [_verify(row) for row in rows if isinstance(row, Mapping)]


def new_candidates(
    record: Mapping[str, Any], previous: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The candidates in ``record`` that no earlier run proposed.

    The point of a cadence: after the first run, what matters is what *changed*. An operator
    re-reading forty unchanged pairings every six hours stops reading them, and the one new
    pairing arrives in a list they have learned to skip.
    """
    seen = {
        _candidate_key(c)
        for row in previous for c in (row.get("candidates") or [])
    }
    return [c for c in (record.get("candidates") or []) if _candidate_key(c) not in seen]


def _candidate_key(candidate: Mapping[str, Any]) -> str:
    return (
        f"{candidate.get('left_venue')}:{candidate.get('left_market_id')}"
        f"|{candidate.get('right_venue')}:{candidate.get('right_market_id')}"
    )


def proposal_status_line(record: Mapping[str, Any], *, new_count: int | None = None) -> str:
    """One ASCII line for the console and the scheduler status (Windows consoles are cp949)."""
    counts = record.get("market_counts") or {}
    venues = ", ".join(f"{v}={n}" for v, n in sorted(counts.items())) or "-"
    errors = record.get("venue_errors") or {}
    degraded = f" degraded={','.join(sorted(errors))}" if errors else ""
    fresh = "" if new_count is None else f", {new_count} new"
    truncated = record.get("candidates_truncated") or 0
    cut = f" ({truncated} not recorded)" if truncated else ""
    tail_count = record.get("candidates_from_tail") or 0
    from_tail = f", {tail_count} from the rotating tail" if tail_count else ""
    return (
        f"pm_scan discovery: {record.get('candidate_count')} candidate(s){cut}{fresh}{from_tail} "
        f"from {record.get('judged_count')} judged pairings [{venues}]{degraded}"
    )


__all__ = [
    "DEFAULT_PROPOSAL_LIMIT",
    "PROPOSALS_LOCKED",
    "PROPOSALS_TAMPERED",
    "PROPOSALS_UNREADABLE",
    "PROPOSALS_WRITE_FAILED",
    "PROPOSAL_RECORD_VERSION",
    "append_proposal",
    "build_proposal_record",
    "new_candidates",
    "proposal_status_line",
    "proposals_path",
    "read_proposals",
    "render_confirmation_sheet",
]


# --- the operator's reading material --------------------------------------------

def render_confirmation_sheet(result: Mapping[str, Any], *, now: str) -> str:
    """Candidates with both venues' settlement text side by side, and a runnable command.

    The bottleneck this exists for: the pipeline proposes thirty-odd pairings every six
    hours and **not one of them becomes data until a human decides two venues settle the
    same event the same way**. That decision needs the venues' own words, which arrive in
    payloads discovery already fetches and used to discard.

    What it deliberately does NOT do is compare them. Two venues can publish near-identical
    text and still settle differently on the same news — that is the risk the whole strategy
    turns on, and a similarity score over settlement prose would read as an answer while
    being exactly as blind as the title matcher. This puts the two texts next to each other;
    the judgement stays where it belongs.

    Markdown because the texts are paragraphs, and a console line wraps them into mush.
    """
    candidates = list(result.get("candidates") or [])
    counts = result.get("market_counts") or {}
    lines = [
        f"# PM1 confirmation sheet — {now}",
        "",
        f"{len(candidates)} candidate(s) from {result.get('judged_count')} judged pairings "
        f"({', '.join(f'{v}={n}' for v, n in sorted(counts.items())) or '-'}).",
        "",
        "**Nothing here is a group.** Each needs the one judgement no algorithm can make:",
        "*do these two venues resolve this event the same way?* The settlement text below is",
        "each venue's own, placed side by side — it is help with the reading, not a verdict.",
        "",
    ]
    errors = result.get("venue_errors") or {}
    if errors:
        lines += [f"> Degraded this run: {', '.join(f'{v} ({c})' for v, c in sorted(errors.items()))}",
                  "> — no candidates from those venues, which is not the same as none existing.", ""]

    for index, candidate in enumerate(candidates, 1):
        slice_note = " · from the rotating tail" if candidate.get("discovery_slice") == "tail" else ""
        lines += [
            f"## {index}. similarity {candidate.get('title_similarity')} · "
            f"close delta {candidate.get('close_delta_hours')}h{slice_note}",
            "",
        ]
        for side in ("left", "right"):
            venue = candidate.get(f"{side}_venue")
            market_id = candidate.get(f"{side}_market_id")
            rules = candidate.get(f"{side}_resolution_rules")
            lines += [
                f"**{venue}** `{market_id}`  ",
                f"> {candidate.get(f'{side}_title')}",
                "",
                f"*Resolves:* {rules or '(this venue published no settlement text)'}",
                "",
            ]
        shared = ", ".join(candidate.get("distinctive_shared_tokens") or []) or "-"
        lines += [
            f"*Matched on:* {shared}",
            "",
            "```bash",
            "docker exec thomas-scheduler python -m runtime.mvp_runtime.predmarket.pairs_cli confirm \\",
            f"  --leg {candidate.get('left_venue')}:{candidate.get('left_market_id')} \\",
            f"  --leg {candidate.get('right_venue')}:{candidate.get('right_market_id')} \\",
            '  --criteria "REPLACE: how both venues settle this, and where they could differ"',
            "```",
            "",
            "---",
            "",
        ]
    if not candidates:
        lines += ["No candidates this run. The screen counts above say whether that is a quiet",
                  "market or a read that saw nothing usable.", ""]
    return "\n".join(lines)
