"""PM1 — the confirmed event pairs. An operator decision, recorded and tamper-evident.

``matching.py`` proposes; this is where a human says yes. Nothing observes, compares, or
(later) trades a pairing that is not in this store, and nothing but an operator action puts
one here — no score, however high, promotes itself.

**Why confirmation is human, permanently.** A wrong pair does not fail; it produces a stable
price gap between two different questions, forever, and every downstream number stays
internally consistent while being about nothing. The check that catches it is not a better
similarity score — it is a person reading both venues' rules.

**And the rules are what they must read.** The strategy's real risk is not that the questions
*sound* different; it is that they *settle* differently — same wording, same date, different
resolution source. No text comparison sees that, so confirming a pair **requires a note
saying the resolution criteria were compared**. The note is stored with the pair and is the
evidence a later mismatch gets investigated against.

**One market, at most one pair.** Confirming a market that already sits in another confirmed
pair is refused: two pairs sharing a leg would double-count one exposure and quietly claim
that two different Polymarket questions are the same Kalshi event.

State lives at ``.runtime_governance_state/predmarket/pairs.jsonl`` — local, per-machine,
gitignored like every other runtime state file. Each record is self-hashed, so a hand-edited
pair is detected rather than trusted; a tampered store fails closed rather than degrading,
because the alternative is observing against a pairing nobody approved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import PersistenceError, ToolError
from ..filelock import locked
from ..jsonl import append_lines, read_objects, write_objects
from ..paths import repo_root as _repo_root
from .market_data import KALSHI, POLYMARKET

PAIR_RECORD_VERSION = "predmarket_pair.v0.1"

PREDMARKET_DIRNAME = "predmarket"
PAIRS_FILENAME = "pairs.jsonl"

# Statuses. A pair is only ever CONFIRMED by an operator, and RETIRED by one — there is no
# automatic transition in either direction.
CONFIRMED = "CONFIRMED"
RETIRED = "RETIRED"

PAIRS_UNREADABLE = "PREDMARKET_PAIRS_UNREADABLE"
PAIRS_TAMPERED = "PREDMARKET_PAIRS_TAMPERED"
PAIRS_WRITE_FAILED = "PREDMARKET_PAIRS_WRITE_FAILED"
PAIRS_LOCKED = "PREDMARKET_PAIRS_LOCKED"

MISSING_CRITERIA_NOTE = "PREDMARKET_MISSING_CRITERIA_NOTE"
MARKET_ALREADY_PAIRED = "PREDMARKET_MARKET_ALREADY_PAIRED"
PAIR_NOT_FOUND = "PREDMARKET_PAIR_NOT_FOUND"

# Short enough that nobody writes it to satisfy the check, long enough to be a sentence.
MIN_CRITERIA_NOTE_CHARS = 12


def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / ".runtime_governance_state" / PREDMARKET_DIRNAME


def pairs_path(root: Path | None = None) -> Path:
    return state_dir(root) / PAIRS_FILENAME


def pair_id(kalshi_market_id: str, polymarket_market_id: str) -> str:
    """Deterministic id for one pairing, so the same pair is the same row on every machine."""
    return integrity.short_id(
        "predmarket_pair", {"kalshi": kalshi_market_id, "polymarket": polymarket_market_id}
    )


def build_pair_record(
    *,
    kalshi_market_id: str,
    polymarket_market_id: str,
    kalshi_title: str,
    polymarket_title: str,
    criteria_note: str,
    confirmed_by: str,
    now: str,
    match_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One CONFIRMED pair, self-hashed. Pure — persisting it is the store's job.

    ``criteria_note`` is required and is the operator's statement that they compared how the
    two venues *resolve* the event. It is the one input no algorithm can supply, so an empty
    or token note is refused rather than accepted as a formality.

    ``match_evidence`` carries the matcher's breakdown at confirmation time (similarity,
    close delta, which tokens matched). It is evidence, not authority: it explains why this
    was proposed and gives a later mismatch investigation something to read.
    """
    for label, value in (("kalshi", kalshi_market_id), ("polymarket", polymarket_market_id)):
        if not (isinstance(value, str) and value.strip()):
            raise ToolError("MISSING_MARKET_ID", f"a pair needs a {label} market id")
    note = (criteria_note or "").strip()
    if len(note) < MIN_CRITERIA_NOTE_CHARS:
        raise ToolError(
            MISSING_CRITERIA_NOTE,
            "confirming a pair requires a note stating how the two venues resolve this event "
            f"(at least {MIN_CRITERIA_NOTE_CHARS} characters) — it is the only check no "
            "algorithm can make, and a wrong pair fakes arbitrage forever",
        )

    body: dict[str, Any] = {
        "pair_record_version": PAIR_RECORD_VERSION,
        "pair_id": pair_id(kalshi_market_id, polymarket_market_id),
        "status": CONFIRMED,
        "kalshi_market_id": kalshi_market_id,
        "polymarket_market_id": polymarket_market_id,
        "kalshi_title": kalshi_title,
        "polymarket_title": polymarket_title,
        "resolution_criteria_note": note,
        "confirmed_by": confirmed_by,
        "confirmed_at_utc": now,
        "match_evidence": dict(match_evidence) if isinstance(match_evidence, Mapping) else None,
        # Stated on the record so no consumer has to infer it: a confirmed pair authorizes
        # observation and (later) paper comparison. It is not a trading authorization.
        "authorizes_trading": False,
    }
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def _verify(record: Mapping[str, Any]) -> dict[str, Any]:
    stored = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(
            PAIRS_TAMPERED,
            "a stored pair does not match its own hash; refusing to observe against a "
            "pairing nobody approved in this form",
        )
    return dict(record)


def read_pairs(root: Path | None = None, *, include_retired: bool = False) -> list[dict[str, Any]]:
    """Every pair in the store, hash-verified. Fails closed on corruption or tampering.

    Unreadable is not empty: an empty store honestly means "no pairs confirmed yet", which
    is a normal state, while an unreadable one means the operator's decisions cannot be
    recovered — and observing without them would compare markets nobody paired.
    """
    try:
        rows = read_objects(pairs_path(root), read_code=PAIRS_UNREADABLE, label="predmarket pairs")
    except PersistenceError as exc:
        raise ToolError(PAIRS_UNREADABLE, str(exc)) from exc
    verified = [_verify(row) for row in rows if isinstance(row, Mapping)]
    if include_retired:
        return verified
    return [row for row in verified if row.get("status") == CONFIRMED]


def confirmed_market_ids(pairs: list[Mapping[str, Any]]) -> tuple[set[str], set[str]]:
    """The Kalshi and Polymarket ids already spoken for, for the one-pair-per-market rule."""
    kalshi = {str(p.get("kalshi_market_id")) for p in pairs if p.get("status") == CONFIRMED}
    poly = {str(p.get("polymarket_market_id")) for p in pairs if p.get("status") == CONFIRMED}
    return kalshi, poly


def confirm_pair(record: Mapping[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    """Append one operator-confirmed pair. Refuses a duplicate or a re-used market.

    Locked, because the CLI and a future scheduled reader can touch this file at the same
    time and a half-appended pair is a pairing nobody wrote.
    """
    path = pairs_path(root)
    with locked(path.with_suffix(".lock"), code=PAIRS_LOCKED, label="predmarket pairs"):
        existing = read_pairs(root, include_retired=True)
        active = [p for p in existing if p.get("status") == CONFIRMED]
        if any(p.get("pair_id") == record.get("pair_id") for p in active):
            raise ToolError(
                MARKET_ALREADY_PAIRED, f"pair {record.get('pair_id')} is already confirmed"
            )
        kalshi_taken, poly_taken = confirmed_market_ids(active)
        if str(record.get("kalshi_market_id")) in kalshi_taken:
            raise ToolError(
                MARKET_ALREADY_PAIRED,
                f"{record.get('kalshi_market_id')} is already in a confirmed pair; one market "
                "belongs to at most one pair, or one exposure would be counted twice",
            )
        if str(record.get("polymarket_market_id")) in poly_taken:
            raise ToolError(
                MARKET_ALREADY_PAIRED,
                f"{record.get('polymarket_market_id')} is already in a confirmed pair; one "
                "market belongs to at most one pair, or one exposure would be counted twice",
            )
        try:
            append_lines(path, [record], write_code=PAIRS_WRITE_FAILED, label="predmarket pair")
        except PersistenceError as exc:
            raise ToolError(PAIRS_WRITE_FAILED, str(exc)) from exc
    return dict(record)


def retire_pair(
    target_pair_id: str, *, reason: str, retired_by: str, now: str, root: Path | None = None
) -> dict[str, Any]:
    """Retire a confirmed pair — the correction path when a pairing turns out to be wrong.

    Rewrites the row rather than deleting it: the record that a pair was once confirmed, by
    whom, and why it was withdrawn is exactly what a resolution-mismatch investigation needs.
    A retired pair frees both markets to be paired again.
    """
    path = pairs_path(root)
    with locked(path.with_suffix(".lock"), code=PAIRS_LOCKED, label="predmarket pairs"):
        rows = read_pairs(root, include_retired=True)
        updated: list[dict[str, Any]] = []
        found: dict[str, Any] | None = None
        for row in rows:
            if row.get("pair_id") == target_pair_id and row.get("status") == CONFIRMED:
                body = {k: v for k, v in row.items() if k != "record_sha256"}
                body.update({
                    "status": RETIRED,
                    "retired_by": retired_by,
                    "retired_at_utc": now,
                    "retired_reason": reason,
                })
                body["record_sha256"] = integrity.sha256_record(body)
                found = body
                updated.append(body)
            else:
                updated.append(dict(row))
        if found is None:
            raise ToolError(PAIR_NOT_FOUND, f"no confirmed pair with id {target_pair_id}")
        try:
            write_objects(path, updated, write_code=PAIRS_WRITE_FAILED, label="predmarket pairs")
        except PersistenceError as exc:
            raise ToolError(PAIRS_WRITE_FAILED, str(exc)) from exc
    return found


def pair_status_line(record: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    return (
        f"{record.get('pair_id')} [{record.get('status')}] "
        f"{KALSHI}:{record.get('kalshi_market_id')} <-> "
        f"{POLYMARKET}:{record.get('polymarket_market_id')}"
    )


def now_iso() -> str:
    return timeutil.utc_now_iso()


__all__ = [
    "CONFIRMED",
    "MARKET_ALREADY_PAIRED",
    "MIN_CRITERIA_NOTE_CHARS",
    "MISSING_CRITERIA_NOTE",
    "PAIRS_TAMPERED",
    "PAIRS_UNREADABLE",
    "PAIRS_WRITE_FAILED",
    "PAIR_NOT_FOUND",
    "PAIR_RECORD_VERSION",
    "RETIRED",
    "build_pair_record",
    "confirm_pair",
    "confirmed_market_ids",
    "now_iso",
    "pair_id",
    "pair_status_line",
    "pairs_path",
    "read_pairs",
    "retire_pair",
    "state_dir",
]
