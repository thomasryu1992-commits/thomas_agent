"""PM1 — the observation record and the scan that produces it. The data PM1 exists to collect.

Everything before this decided *what* to compare and *what it costs*. This is the part that
actually accumulates: one scan reads the venues, prices every pairing inside every confirmed
event group, and appends the result. Repeated for two to four weeks, those rows answer the
three questions the whole track turns on — **how often, how large, and how long they last.**

**Persistence is the load-bearing one.** PM3 puts minutes of approval latency between seeing
an opportunity and taking it, so an edge that lasts thirty seconds is unreachable however
frequent or fat it is. That number decides whether PM3 is worth building at all, and it can
only be measured by scanning faster than the thing being measured — which is why the watch
scan is 2 minutes rather than 15 (decision #1, 2026-07-26).

**A non-reading is a row.** A scan that could not price a group still appends, with the
reason. Silently omitting those would make the report claim opportunities were observable
during hours when nobody was looking, and "how often" is a ratio whose denominator is exactly
those attempts.

**Nothing here trades, and no scan confirms anything.** A group is observed because an
operator confirmed it; a scan can never add one. The store is append-only and self-hashed:
the report is evidence for a decision about real money, so a row that cannot prove itself is
refused rather than averaged in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from runtime.read_only_kernel import integrity

from .. import timeutil
from ..errors import PersistenceError, ToolError
from ..filelock import locked
from ..jsonl import append_lines, read_objects
from . import opportunity, pairs
from .market_data import (
    PREDMARKET_DEGRADED,
    PredMarket,
    VenueQuote,
    collect_pred_markets,
    degraded_pred_market_record,
    select_pred_market_collector,
)

OBSERVATION_RECORD_VERSION = "predmarket_observation.v0.1"
SCAN_RECORD_VERSION = "predmarket_scan.v0.1"

OBSERVATIONS_FILENAME = "observations.jsonl"

OBSERVATIONS_UNREADABLE = "PREDMARKET_OBSERVATIONS_UNREADABLE"
OBSERVATIONS_TAMPERED = "PREDMARKET_OBSERVATIONS_TAMPERED"
OBSERVATIONS_WRITE_FAILED = "PREDMARKET_OBSERVATIONS_WRITE_FAILED"
OBSERVATIONS_LOCKED = "PREDMARKET_OBSERVATIONS_LOCKED"

# Why a group produced no reading. Each is a different fact about the scan, and the report
# separates them: a venue outage is not an unconfirmed setup, and neither is a quiet book.
VENUE_UNREADABLE = "VENUE_UNREADABLE"
MARKET_NOT_LISTED = "MARKET_NOT_LISTED"


def observations_path(root: Path | None = None) -> Path:
    return pairs.state_dir(root) / OBSERVATIONS_FILENAME


def _stamp(record: dict[str, Any]) -> dict[str, Any]:
    record["record_sha256"] = integrity.sha256_record(record)
    return record


def _verify(record: Mapping[str, Any]) -> dict[str, Any]:
    stored = record.get("record_sha256")
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
        raise ToolError(
            OBSERVATIONS_TAMPERED,
            "a stored observation does not match its own hash; the PM1 report is evidence "
            "for a decision about real money and will not average in a row it cannot verify",
        )
    return dict(record)


def append_observations(
    records: Iterable[Mapping[str, Any]], *, root: Path | None = None
) -> int:
    """Append observations, self-hashing each. Returns how many landed."""
    rows = [_stamp(dict(r)) for r in records]
    if not rows:
        return 0
    path = observations_path(root)
    with locked(path.with_suffix(".lock"), code=OBSERVATIONS_LOCKED, label="predmarket observations"):
        try:
            append_lines(path, rows, write_code=OBSERVATIONS_WRITE_FAILED, label="predmarket observation")
        except PersistenceError as exc:
            raise ToolError(OBSERVATIONS_WRITE_FAILED, str(exc)) from exc
    return len(rows)


def read_observations(root: Path | None = None) -> list[dict[str, Any]]:
    """Every observation, hash-verified. Fails closed on corruption or tampering."""
    try:
        rows = read_objects(
            observations_path(root), read_code=OBSERVATIONS_UNREADABLE, label="predmarket observations"
        )
    except PersistenceError as exc:
        raise ToolError(OBSERVATIONS_UNREADABLE, str(exc)) from exc
    return [_verify(row) for row in rows if isinstance(row, Mapping)]


# --- the scan -------------------------------------------------------------------

def _markets_by_key(snapshot: Mapping[str, Any]) -> dict[str, PredMarket]:
    """Rebuild typed markets from a collection snapshot, keyed ``venue:market_id``."""
    rebuilt: dict[str, PredMarket] = {}
    for row in snapshot.get("markets") or []:
        quote = row.get("quote") or {}
        market = PredMarket(
            venue=row.get("venue"),
            market_id=row.get("market_id"),
            group_id=row.get("group_id"),
            title=row.get("title") or "",
            close_time=row.get("close_time"),
            status=row.get("status"),
            category=row.get("category"),
            fee_rate_bps=row.get("fee_rate_bps"),
            quote=VenueQuote(
                yes_bid=quote.get("yes_bid"), yes_ask=quote.get("yes_ask"),
                yes_bid_size=quote.get("yes_bid_size"), yes_ask_size=quote.get("yes_ask_size"),
            ),
        )
        rebuilt[f"{market.venue}:{market.market_id}"] = market
    return rebuilt


def run_watch_scan(
    *,
    now: str,
    root: Path | None = None,
    limit: int = 100,
    contracts: float = 1.0,
    persist: bool = True,
) -> dict[str, Any]:
    """One watch scan: read the venues the confirmed groups need, price every pairing, record.

    Reads **only the venues that appear in a confirmed group** — a scan is measurement, not
    discovery, and asking a venue nobody paired would spend a call to learn nothing.

    Degrades per venue: one venue being unreadable leaves the pairings that involve it as
    recorded non-readings and prices the rest. A scan with one venue down is still a scan, and
    saying so is what keeps the report's denominator honest.

    Returns the scan record. ``persist=False`` runs the whole thing and writes nothing, which
    is what the CLI's dry run and the tests use.
    """
    groups = pairs.read_groups(root)
    needed = sorted({leg["venue"] for g in groups for leg in g.get("legs") or []})

    markets: dict[str, PredMarket] = {}
    venue_errors: dict[str, str] = {}
    for venue in needed:
        collector = select_pred_market_collector(venue, now=now, root=root)
        try:
            snapshot, _record = collect_pred_markets(
                venue, collector=collector, now=now, limit=limit
            )
        except ToolError as exc:
            # Recorded, never silent — the crypto MARKET_DATA_DEGRADED posture.
            degraded_pred_market_record(collector, venue, PREDMARKET_DEGRADED, now=now)
            venue_errors[venue] = exc.reason_code
            continue
        markets.update(_markets_by_key(snapshot))

    observations: list[dict[str, Any]] = []
    for group in groups:
        for left_leg, right_leg in pairs.pairings_of(group):
            left_key = f"{left_leg['venue']}:{left_leg['market_id']}"
            right_key = f"{right_leg['venue']}:{right_leg['market_id']}"
            left, right = markets.get(left_key), markets.get(right_key)
            if left is None or right is None:
                # A missing leg has two possible causes and they are different findings:
                # the venue did not answer at all, or it answered and no longer lists this
                # market (resolved, delisted, or the group is stale).
                missing = [k for k, m in ((left_key, left), (right_key, right)) if m is None]
                reasons = [
                    VENUE_UNREADABLE if key.split(":", 1)[0] in venue_errors else MARKET_NOT_LISTED
                    for key in missing
                ]
                observations.append({
                    "observation_version": OBSERVATION_RECORD_VERSION,
                    "event_id": group.get("event_id"),
                    "legs": [
                        {"venue": left_leg["venue"], "market_id": left_leg["market_id"]},
                        {"venue": right_leg["venue"], "market_id": right_leg["market_id"]},
                    ],
                    "gross_edge": None,
                    "net_edge": None,
                    "is_opportunity": False,
                    "reasons": sorted(set(reasons)),
                    "missing_legs": missing,
                    "observed_at_utc": now,
                    "authorizes_trading": False,
                })
                continue
            priced = opportunity.evaluate_pairing(
                left, right, contracts=contracts, now=now, event_id=group.get("event_id")
            )
            priced["observation_version"] = OBSERVATION_RECORD_VERSION
            observations.append(priced)

    written = append_observations(observations, root=root) if persist else 0
    opportunities = [o for o in observations if o.get("is_opportunity")]
    scan = {
        "scan_version": SCAN_RECORD_VERSION,
        "scan_kind": "watch",
        "groups_observed": len(groups),
        "venues_read": [v for v in needed if v not in venue_errors],
        "venue_errors": venue_errors,
        "observation_count": len(observations),
        # The denominator of "how often": attempts that produced a number at all.
        "readable_count": sum(1 for o in observations if o.get("net_edge") is not None),
        "opportunity_count": len(opportunities),
        "persisted_count": written,
        "observed_at_utc": now,
        "authorizes_trading": False,
    }
    scan["scan_id"] = integrity.short_id(
        "predmarket_scan", {"at": now, "groups": str(len(groups))}
    )
    return scan


def scan_status_line(scan: Mapping[str, Any]) -> str:
    """One ASCII line for the console (Windows consoles are cp949)."""
    errors = scan.get("venue_errors") or {}
    tail = f" degraded={','.join(sorted(errors))}" if errors else ""
    return (
        f"pm_scan {scan.get('scan_kind')}: {scan.get('opportunity_count')} opportunity/ies "
        f"from {scan.get('readable_count')}/{scan.get('observation_count')} readable "
        f"across {scan.get('groups_observed')} group(s){tail}"
    )


def now_iso() -> str:
    return timeutil.utc_now_iso()


__all__ = [
    "MARKET_NOT_LISTED",
    "OBSERVATIONS_LOCKED",
    "OBSERVATIONS_TAMPERED",
    "OBSERVATIONS_UNREADABLE",
    "OBSERVATIONS_WRITE_FAILED",
    "OBSERVATION_RECORD_VERSION",
    "SCAN_RECORD_VERSION",
    "VENUE_UNREADABLE",
    "append_observations",
    "now_iso",
    "observations_path",
    "read_observations",
    "run_watch_scan",
    "scan_status_line",
]
