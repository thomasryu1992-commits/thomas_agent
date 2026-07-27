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
    tail = f" degraded={','.join(sorted(errors))}" if errors else ""
    fresh = "" if new_count is None else f", {new_count} new"
    truncated = record.get("candidates_truncated") or 0
    cut = f" ({truncated} not recorded)" if truncated else ""
    return (
        f"pm_scan discovery: {record.get('candidate_count')} candidate(s){cut}{fresh} "
        f"from {record.get('judged_count')} judged pairings [{venues}]{tail}"
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
]
