"""PM1 — the confirmed event groups. An operator decision, recorded and tamper-evident.

``matching.py`` proposes; this is where a human says yes. Nothing observes, compares, or
(later) trades a market that is not in a confirmed group here, and nothing but an operator
action puts one here — no similarity score, however high, promotes itself.

**Why the unit is a group and not a pair.** One real-world event can be quoted by any number
of venues. Keyed pairwise, three venues on one event means three separate confirmations
(K-P, K-B, P-B) and a fourth venue means six — the operator's work grows with the *square* of
the venue count, for one event they already recognised. Keyed as a group it grows linearly: a
new venue adds one leg to an existing group. The detector then enumerates the pairings inside
the group itself, which is arithmetic, not judgement.

The second reason is exposure. The same Kalshi market appearing in two confirmed pairs is two
claims on one position; grouping makes "how much of this event do we hold" a question about
the event, which is the only level at which it has an answer.

**Why confirmation is human, permanently — and how it gets cheaper.** A wrong grouping does
not fail. It produces a stable price gap between two different questions, forever, while every
number downstream stays internally consistent. It is the one decision whose errors the system
cannot detect afterwards, which is why a person makes it. What *can* be automated is the
**work**: presenting both venues' resolution rules side by side, and (a later increment)
recording whether a resolved group actually settled the same way on every leg. That
agreement history is what could eventually earn family-level pre-approval — the programization
pattern — rather than removing the check by assertion.

**The rules are what the operator must read.** The strategy's real risk is not that the
questions *sound* different; it is that they *settle* differently — same wording, same date,
different resolution source. No text comparison sees that, so confirming a group **requires a
note comparing the resolution criteria**. It is stored with the group and is the evidence a
later mismatch gets investigated against.

State lives at ``.runtime_governance_state/predmarket/events.jsonl`` — local, per-machine,
gitignored like every other runtime state file. Each record is self-hashed, so a hand-edited
group is detected rather than trusted; a tampered store fails closed, because the alternative
is observing against a grouping nobody approved.
"""

from __future__ import annotations

from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import PersistenceError, ToolError
from ..filelock import locked
from ..jsonl import append_lines, read_objects, write_objects
from ..paths import repo_root as _repo_root
from .market_data import require_venue

EVENT_GROUP_VERSION = "predmarket_event_group.v0.1"

PREDMARKET_DIRNAME = "predmarket"
EVENTS_FILENAME = "events.jsonl"

# Statuses. CONFIRMED only ever by an operator, RETIRED only ever by one — no automatic
# transition in either direction.
CONFIRMED = "CONFIRMED"
RETIRED = "RETIRED"

EVENTS_UNREADABLE = "PREDMARKET_EVENTS_UNREADABLE"
EVENTS_TAMPERED = "PREDMARKET_EVENTS_TAMPERED"
EVENTS_WRITE_FAILED = "PREDMARKET_EVENTS_WRITE_FAILED"
EVENTS_LOCKED = "PREDMARKET_EVENTS_LOCKED"

MISSING_CRITERIA_NOTE = "PREDMARKET_MISSING_CRITERIA_NOTE"
MARKET_ALREADY_GROUPED = "PREDMARKET_MARKET_ALREADY_GROUPED"
GROUP_NOT_FOUND = "PREDMARKET_GROUP_NOT_FOUND"
TOO_FEW_LEGS = "PREDMARKET_TOO_FEW_LEGS"
DUPLICATE_VENUE = "PREDMARKET_DUPLICATE_VENUE_IN_GROUP"

# Short enough that nobody writes it to satisfy the check, long enough to be a sentence.
MIN_CRITERIA_NOTE_CHARS = 12
# A comparison needs two sides. One leg is a market, not a grouping.
MIN_LEGS = 2


def state_dir(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / ".runtime_governance_state" / PREDMARKET_DIRNAME


def events_path(root: Path | None = None) -> Path:
    return state_dir(root) / EVENTS_FILENAME


def normalize_legs(legs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and canonically order the legs of one group.

    Sorted by ``(venue, market_id)`` so a group is the same record however the operator
    happened to list it — otherwise ``K,P`` and ``P,K`` would be two groups for one event,
    and the one-market-one-group rule would not catch it.

    One leg per venue: two markets on the same venue are not two views of one event, they are
    two markets, and pairing them is intra-venue structural arbitrage — a different strategy
    with a different risk, deliberately out of scope here.
    """
    cleaned: list[dict[str, Any]] = []
    seen_venues: set[str] = set()
    for leg in legs or []:
        if not isinstance(leg, Mapping):
            raise ToolError("MALFORMED_LEG", "each leg must be a mapping of venue/market_id/title")
        venue = require_venue(leg.get("venue"))
        market_id = leg.get("market_id")
        if not (isinstance(market_id, str) and market_id.strip()):
            raise ToolError("MISSING_MARKET_ID", f"the {venue} leg needs a market id")
        if venue in seen_venues:
            raise ToolError(
                DUPLICATE_VENUE,
                f"two {venue} markets in one group; a group holds one leg per venue "
                "(same-venue pairings are intra-venue arbitrage, out of scope)",
            )
        seen_venues.add(venue)
        cleaned.append({
            "venue": venue,
            "market_id": market_id.strip(),
            "title": str(leg.get("title") or ""),
        })
    if len(cleaned) < MIN_LEGS:
        raise ToolError(TOO_FEW_LEGS, f"a group needs at least {MIN_LEGS} legs, got {len(cleaned)}")
    return sorted(cleaned, key=lambda leg: (leg["venue"], leg["market_id"]))


def event_id(legs: Sequence[Mapping[str, Any]]) -> str:
    """Deterministic id over the canonically ordered legs — same group, same id, any machine."""
    return integrity.short_id(
        "predmarket_event_group",
        {"legs": [f"{leg['venue']}:{leg['market_id']}" for leg in legs]},
    )


def pairings_of(group: Mapping[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Every leg pairing inside one group — what the detector compares.

    This is the arithmetic the group replaced judgement with: confirming an event once yields
    ``n(n-1)/2`` comparisons for free, and adding a venue later adds its comparisons without
    another confirmation.
    """
    legs = [dict(leg) for leg in (group.get("legs") or [])]
    return [(a, b) for a, b in combinations(legs, 2)]


def build_event_group(
    *,
    legs: Iterable[Mapping[str, Any]],
    criteria_note: str,
    confirmed_by: str,
    now: str,
    match_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One CONFIRMED event group, self-hashed. Pure — persisting it is the store's job.

    ``criteria_note`` is required and is the operator's statement that they compared how the
    venues *resolve* this event. It is the one input no algorithm can supply, so an empty or
    token note is refused rather than accepted as a formality.

    ``match_evidence`` carries the matcher's breakdown at confirmation time. It is evidence,
    not authority: it explains why this was proposed and gives a later mismatch investigation
    something to read.
    """
    normalized = normalize_legs(legs)
    note = (criteria_note or "").strip()
    if len(note) < MIN_CRITERIA_NOTE_CHARS:
        raise ToolError(
            MISSING_CRITERIA_NOTE,
            "confirming a group requires a note stating how the venues resolve this event "
            f"(at least {MIN_CRITERIA_NOTE_CHARS} characters) — it is the only check no "
            "algorithm can make, and a wrong grouping fakes arbitrage forever",
        )

    body: dict[str, Any] = {
        "event_group_version": EVENT_GROUP_VERSION,
        "event_id": event_id(normalized),
        "status": CONFIRMED,
        "legs": normalized,
        "venues": [leg["venue"] for leg in normalized],
        "resolution_criteria_note": note,
        "confirmed_by": confirmed_by,
        "confirmed_at_utc": now,
        "match_evidence": dict(match_evidence) if isinstance(match_evidence, Mapping) else None,
        # Stated on the record so no consumer has to infer it: a confirmed group authorizes
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
            EVENTS_TAMPERED,
            "a stored event group does not match its own hash; refusing to observe against a "
            "grouping nobody approved in this form",
        )
    return dict(record)


def read_groups(root: Path | None = None, *, include_retired: bool = False) -> list[dict[str, Any]]:
    """Every group in the store, hash-verified. Fails closed on corruption or tampering.

    Unreadable is not empty: an empty store honestly means "nothing confirmed yet", a normal
    state, while an unreadable one means the operator's decisions cannot be recovered — and
    observing without them would compare markets nobody grouped.
    """
    try:
        rows = read_objects(events_path(root), read_code=EVENTS_UNREADABLE, label="predmarket events")
    except PersistenceError as exc:
        raise ToolError(EVENTS_UNREADABLE, str(exc)) from exc
    verified = [_verify(row) for row in rows if isinstance(row, Mapping)]
    if include_retired:
        return verified
    return [row for row in verified if row.get("status") == CONFIRMED]


def grouped_market_keys(groups: Iterable[Mapping[str, Any]]) -> set[str]:
    """The ``venue:market_id`` keys already spoken for, for the one-market-one-group rule."""
    keys: set[str] = set()
    for group in groups:
        if group.get("status") != CONFIRMED:
            continue
        for leg in group.get("legs") or []:
            keys.add(f"{leg.get('venue')}:{leg.get('market_id')}")
    return keys


def confirm_group(record: Mapping[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    """Append one operator-confirmed group. Refuses a duplicate or a re-used market.

    Locked, because the CLI and a scheduled reader can touch this file at the same time and a
    half-appended group is a grouping nobody wrote.
    """
    path = events_path(root)
    with locked(path.with_suffix(".lock"), code=EVENTS_LOCKED, label="predmarket events"):
        active = [g for g in read_groups(root, include_retired=True) if g.get("status") == CONFIRMED]
        if any(g.get("event_id") == record.get("event_id") for g in active):
            raise ToolError(
                MARKET_ALREADY_GROUPED, f"event group {record.get('event_id')} is already confirmed"
            )
        taken = grouped_market_keys(active)
        for leg in record.get("legs") or []:
            key = f"{leg.get('venue')}:{leg.get('market_id')}"
            if key in taken:
                raise ToolError(
                    MARKET_ALREADY_GROUPED,
                    f"{key} is already in a confirmed group; one market belongs to at most one "
                    "event, or one exposure would be counted twice. Retire that group, or add "
                    "this venue's leg to it instead of creating a second one",
                )
        try:
            append_lines(path, [record], write_code=EVENTS_WRITE_FAILED, label="predmarket event group")
        except PersistenceError as exc:
            raise ToolError(EVENTS_WRITE_FAILED, str(exc)) from exc
    return dict(record)


def add_leg(
    target_event_id: str,
    leg: Mapping[str, Any],
    *,
    criteria_note: str,
    added_by: str,
    now: str,
    root: Path | None = None,
) -> dict[str, Any]:
    """Add one venue's leg to a confirmed group — the linear-cost path for a new venue.

    This is the whole point of grouping: a third venue quoting an event the operator already
    recognised costs **one** review, not one per existing leg. It still costs that one, and
    for the same reason as the original: the new venue's resolution rules are its own, so the
    note is required again and describes the leg being added.

    The group's ``event_id`` changes, because the id is derived from its legs — the record is
    rewritten rather than mutated in place, and the previous id is kept as
    ``superseded_event_ids`` so an observation recorded under the old id stays traceable.
    """
    path = events_path(root)
    with locked(path.with_suffix(".lock"), code=EVENTS_LOCKED, label="predmarket events"):
        rows = read_groups(root, include_retired=True)
        target = next(
            (r for r in rows if r.get("event_id") == target_event_id and r.get("status") == CONFIRMED),
            None,
        )
        if target is None:
            raise ToolError(GROUP_NOT_FOUND, f"no confirmed event group with id {target_event_id}")
        note = (criteria_note or "").strip()
        if len(note) < MIN_CRITERIA_NOTE_CHARS:
            raise ToolError(
                MISSING_CRITERIA_NOTE,
                "adding a leg requires a note comparing how THIS venue resolves the event",
            )
        taken = grouped_market_keys(r for r in rows if r.get("event_id") != target_event_id)
        legs = normalize_legs([*target.get("legs", []), leg])
        for candidate in legs:
            key = f"{candidate['venue']}:{candidate['market_id']}"
            if key in taken:
                raise ToolError(MARKET_ALREADY_GROUPED, f"{key} is already in another confirmed group")

        body = {k: v for k, v in target.items() if k != "record_sha256"}
        history = list(body.get("superseded_event_ids") or [])
        history.append(target_event_id)
        body.update({
            "legs": legs,
            "venues": [item["venue"] for item in legs],
            "event_id": event_id(legs),
            "superseded_event_ids": history,
            # The added leg's own criteria note, appended rather than replacing: the original
            # comparison stays readable next to the new one.
            "resolution_criteria_note": f"{body.get('resolution_criteria_note')}\n[+{leg.get('venue')}] {note}",
            "last_leg_added_by": added_by,
            "last_leg_added_at_utc": now,
        })
        body["record_sha256"] = integrity.sha256_record(body)
        updated = [body if r.get("event_id") == target_event_id else dict(r) for r in rows]
        try:
            write_objects(path, updated, write_code=EVENTS_WRITE_FAILED, label="predmarket events")
        except PersistenceError as exc:
            raise ToolError(EVENTS_WRITE_FAILED, str(exc)) from exc
    return body


def retire_group(
    target_event_id: str, *, reason: str, retired_by: str, now: str, root: Path | None = None
) -> dict[str, Any]:
    """Retire a confirmed group — the correction path when a grouping turns out to be wrong.

    Rewrites the row rather than deleting it: that a group was once confirmed, by whom, and
    why it was withdrawn is exactly what a resolution-mismatch investigation needs. Retiring
    frees every leg to be grouped again.
    """
    path = events_path(root)
    with locked(path.with_suffix(".lock"), code=EVENTS_LOCKED, label="predmarket events"):
        rows = read_groups(root, include_retired=True)
        found: dict[str, Any] | None = None
        updated: list[dict[str, Any]] = []
        for row in rows:
            if row.get("event_id") == target_event_id and row.get("status") == CONFIRMED:
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
            raise ToolError(GROUP_NOT_FOUND, f"no confirmed event group with id {target_event_id}")
        try:
            write_objects(path, updated, write_code=EVENTS_WRITE_FAILED, label="predmarket events")
        except PersistenceError as exc:
            raise ToolError(EVENTS_WRITE_FAILED, str(exc)) from exc
    return found


def group_status_line(record: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    legs = " <-> ".join(
        f"{leg.get('venue')}:{leg.get('market_id')}" for leg in record.get("legs") or []
    )
    return f"{record.get('event_id')} [{record.get('status')}] {legs}"


def now_iso() -> str:
    return timeutil.utc_now_iso()


__all__ = [
    "CONFIRMED",
    "DUPLICATE_VENUE",
    "EVENTS_TAMPERED",
    "EVENTS_UNREADABLE",
    "EVENTS_WRITE_FAILED",
    "EVENT_GROUP_VERSION",
    "GROUP_NOT_FOUND",
    "MARKET_ALREADY_GROUPED",
    "MIN_CRITERIA_NOTE_CHARS",
    "MIN_LEGS",
    "MISSING_CRITERIA_NOTE",
    "RETIRED",
    "TOO_FEW_LEGS",
    "add_leg",
    "build_event_group",
    "confirm_group",
    "event_id",
    "events_path",
    "group_status_line",
    "grouped_market_keys",
    "normalize_legs",
    "now_iso",
    "pairings_of",
    "read_groups",
    "retire_group",
    "state_dir",
]
