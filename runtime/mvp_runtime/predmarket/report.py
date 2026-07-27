"""PM1's exit artifact — what two to four weeks of observation actually said.

The roadmap asks for three numbers per pairing: **how often** an opportunity appears, **how
large** it is net of fees, and **how long it lasts**. The third is the one the phase exists
to produce. PM3 puts minutes of human approval between seeing an edge and taking it, so an
edge that lives thirty seconds is unreachable however frequent or fat it is, and no amount of
PM2 paper trading would ever reveal that.

Three things decide whether this report means anything.

**A duration is an interval, never a point.** An opportunity seen in one scan did not last
zero seconds — it lasted somewhere between an instant and the gap to the scans on either
side. Reporting the point estimate would conclude "opportunities never persist" from a signal
that says nothing of the kind. So every episode carries ``[lower, upper]``: the lower bound
is what was actually witnessed, the upper is what the neighbouring observations still permit.
**The lower bound is the one PM3 must clear** — it is the part that is not a guess.

**A gap in observation ends nothing; it only ends knowledge.** When consecutive scans of a
pairing are further apart than the tolerance — a venue outage, a restarted scheduler — the
episode is cut there and marked, because "we saw it at 09:00 and again at 11:00" is not
evidence it was there at 10:00. Stitching across the gap would inflate exactly the number the
whole phase turns on.

**The denominator is readings, not scans.** "How often" divides by the observations that
produced a number at all; the ones that could not price the pairing are reported separately
as coverage. A report whose coverage is 20% is not a measurement of a quiet market, and
collapsing the two would let an outage read as an absence of opportunity.

Nothing here trades, confirms, or authorizes. It reads the append-only observation store and
returns a dict.
"""

from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .. import timeutil
from . import observations as obs

REPORT_VERSION = "predmarket_pm1_report.v0.1"

# The watch scan fires every 2 minutes. Two missed scans is noise; beyond that the pairing
# genuinely was not being watched, and an episode may not be stitched across the hole.
DEFAULT_MAX_GAP_SECONDS = 360

# The roadmap's window. A report over an afternoon is a progress check, not the artifact that
# decides whether PM2 is worth starting, and it has to say which one it is.
EXIT_ARTIFACT_MIN_DAYS = 14

SUFFICIENT = "SUFFICIENT"
INSUFFICIENT_WINDOW = "INSUFFICIENT_WINDOW"
INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
NO_OBSERVATIONS = "NO_OBSERVATIONS"

# Below this share of readable observations the report describes the scanner, not the market.
MIN_COVERAGE = 0.5


def pairing_key(row: Mapping[str, Any]) -> str:
    """A stable id for the pairing a row is about: the event plus its two legs, order-free.

    Order-free because the matcher orders legs canonically but a group's stored legs need
    not, and two spellings of one pairing would split its episodes in half — halving every
    duration in the report.
    """
    legs = row.get("legs") or []
    names = sorted(
        f"{leg.get('venue')}:{leg.get('market_id')}" for leg in legs if isinstance(leg, Mapping)
    )
    return f"{row.get('event_id')}|" + "|".join(names)


def _seconds(earlier: Any, later: Any) -> float | None:
    try:
        return (timeutil.parse_iso(str(later)) - timeutil.parse_iso(str(earlier))).total_seconds()
    except (TypeError, ValueError):
        return None


def episodes(
    rows: Sequence[Mapping[str, Any]], *, max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS
) -> list[dict[str, Any]]:
    """One pairing's observations, in time order, as opportunity episodes.

    An episode is a run of consecutive observations where the pairing was an opportunity.
    It ends at the first observation that was not one, at a gap wider than the tolerance, or
    at the end of the record — and which of those it was is recorded, because they mean
    different things:

    - ``ended_observed`` — a later scan priced the pairing and it was gone. The duration's
      upper bound is real.
    - ``ended_gap`` — the next scan came too late to say. Upper bound unknown.
    - ``ended_open`` — the record ends while the opportunity is still there. Right-censored:
      the true duration is longer than anything measured here.

    The same three apply at the start (``started_open`` when the very first observation of
    the record is already an opportunity), because an edge that was running before we looked
    is not an edge that began when we looked.
    """
    ordered = sorted(rows, key=lambda r: str(r.get("observed_at_utc") or ""))
    out: list[dict[str, Any]] = []
    current: list[Mapping[str, Any]] = []
    previous: Mapping[str, Any] | None = None

    def close(reason: str, neighbour: Mapping[str, Any] | None, started_open: bool) -> None:
        if not current:
            return
        first, last = current[0], current[-1]
        witnessed = _seconds(first.get("observed_at_utc"), last.get("observed_at_utc")) or 0.0
        # The upper bound reaches to the neighbouring reading on each side: everything
        # between two scans is unobserved, and the opportunity may have filled it.
        after = _seconds(last.get("observed_at_utc"), neighbour.get("observed_at_utc")) if neighbour else None
        upper = witnessed + (after or 0.0)
        edges = [r.get("net_edge") for r in current if isinstance(r.get("net_edge"), (int, float))]
        out.append({
            "observation_count": len(current),
            "first_seen_utc": first.get("observed_at_utc"),
            "last_seen_utc": last.get("observed_at_utc"),
            # Witnessed. A one-observation episode is 0.0 here and that is honest: nothing
            # was seen to elapse. It is never the answer on its own — read it with `upper`.
            "duration_lower_seconds": round(witnessed, 3),
            # None when the record simply stops: an open episode has no known upper bound,
            # and filling one in would be the report inventing its own most important number.
            "duration_upper_seconds": round(upper, 3) if neighbour is not None else None,
            "ended": reason,
            "started_open": started_open,
            "max_net_edge": max(edges) if edges else None,
            "median_net_edge": median(edges) if edges else None,
        })
        current.clear()

    started_open = bool(ordered and ordered[0].get("is_opportunity"))
    for row in ordered:
        gap = _seconds(previous.get("observed_at_utc"), row.get("observed_at_utc")) if previous else None
        if current and gap is not None and gap > max_gap_seconds:
            close("ended_gap", None, started_open)
            started_open = False
        if row.get("is_opportunity"):
            current.append(row)
        elif current:
            close("ended_observed", row, started_open)
            started_open = False
        previous = row
    close("ended_open", None, started_open)
    return out


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"count": 0, "median": None, "max": None, "min": None}
    return {
        "count": len(values),
        "median": round(median(values), 6),
        "max": round(max(values), 6),
        "min": round(min(values), 6),
    }


def build_pm1_report(
    rows: Iterable[Mapping[str, Any]] | None = None,
    *,
    now: str,
    root: Path | None = None,
    max_gap_seconds: float = DEFAULT_MAX_GAP_SECONDS,
) -> dict[str, Any]:
    """The three questions, answered per pairing and overall. Reads; writes nothing."""
    observed = list(rows) if rows is not None else obs.read_observations(root)

    by_pairing: dict[str, list[Mapping[str, Any]]] = {}
    for row in observed:
        if isinstance(row, Mapping):
            by_pairing.setdefault(pairing_key(row), []).append(row)

    stamps = sorted(str(r.get("observed_at_utc") or "") for r in observed if r.get("observed_at_utc"))
    window_seconds = _seconds(stamps[0], stamps[-1]) if len(stamps) >= 2 else 0.0
    readable = [r for r in observed if r.get("net_edge") is not None]
    coverage = len(readable) / len(observed) if observed else 0.0

    pairings: list[dict[str, Any]] = []
    for key, group in sorted(by_pairing.items()):
        group_readable = [r for r in group if r.get("net_edge") is not None]
        wins = [r for r in group_readable if r.get("is_opportunity")]
        eps = episodes(group, max_gap_seconds=max_gap_seconds)
        lowers = [e["duration_lower_seconds"] for e in eps]
        bounded = [e["duration_upper_seconds"] for e in eps if e["duration_upper_seconds"] is not None]
        pairings.append({
            "pairing_key": key,
            "event_id": (group[0].get("event_id") if group else None),
            "legs": (group[0].get("legs") if group else None),
            "observation_count": len(group),
            "readable_count": len(group_readable),
            # The denominator is readings. Dividing by attempts would let an outage look
            # like a market with no opportunities in it.
            "opportunity_count": len(wins),
            "opportunity_rate": round(len(wins) / len(group_readable), 6) if group_readable else None,
            "net_edge": _quantiles([r["net_edge"] for r in group_readable
                                    if isinstance(r.get("net_edge"), (int, float))]),
            "opportunity_net_edge": _quantiles([r["net_edge"] for r in wins
                                                if isinstance(r.get("net_edge"), (int, float))]),
            "episode_count": len(eps),
            "duration_lower_seconds": _quantiles(lowers),
            "duration_upper_seconds": _quantiles(bounded),
            # The three ways an episode's duration is not the whole truth. A report where
            # most episodes are censored is a report about the scanner's uptime.
            "episodes_open_ended": sum(1 for e in eps if e["ended"] == "ended_open"),
            "episodes_gap_ended": sum(1 for e in eps if e["ended"] == "ended_gap"),
            "episodes_open_started": sum(1 for e in eps if e["started_open"]),
            "episodes": eps,
        })

    # Pair-mismatch and outage incidents, which the roadmap asks for by name. A group whose
    # market stopped being listed is a stale or resolved pairing — an operator action, not a
    # scanner bug — and it must not be filed under "venue was down".
    #
    # SOURCE_SYNTHETIC is the third: the gate was closed and the mock answered, so the runtime
    # never reached the venue at all. It has to be tallied here or a report can show coverage
    # collapsing to INSUFFICIENT_COVERAGE with both other counts at zero — the number with no
    # explanation next to it.
    incidents = {
        code: sum(1 for r in observed if code in (r.get("reasons") or []))
        for code in (obs.MARKET_NOT_LISTED, obs.VENUE_UNREADABLE, obs.SOURCE_SYNTHETIC)
    }

    days = (window_seconds or 0.0) / 86400.0
    if not observed:
        verdict = NO_OBSERVATIONS
    elif coverage < MIN_COVERAGE:
        verdict = INSUFFICIENT_COVERAGE
    elif days < EXIT_ARTIFACT_MIN_DAYS:
        verdict = INSUFFICIENT_WINDOW
    else:
        verdict = SUFFICIENT

    all_lowers = [e["duration_lower_seconds"] for p in pairings for e in p["episodes"]]
    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": now,
        "window": {
            "first_observation_utc": stamps[0] if stamps else None,
            "last_observation_utc": stamps[-1] if stamps else None,
            "span_days": round(days, 4),
            "required_days": EXIT_ARTIFACT_MIN_DAYS,
        },
        "observation_count": len(observed),
        "readable_count": len(readable),
        # Reported beside every rate, never folded into one: a low-coverage report describes
        # the scanner rather than the market.
        "coverage": round(coverage, 6),
        "pairing_count": len(pairings),
        "opportunity_count": sum(p["opportunity_count"] for p in pairings),
        "episode_count": sum(p["episode_count"] for p in pairings),
        "duration_lower_seconds": _quantiles(all_lowers),
        "incidents": incidents,
        "pairings": pairings,
        # Whether this is the exit artifact or a progress check. Stated, so a two-hour run
        # can never be mistaken for the thing that decides PM2.
        "verdict": verdict,
        "is_exit_artifact": verdict == SUFFICIENT,
        "authorizes_trading": False,
    }


def _secs(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "?"
    if value < 120:
        return f"{value:.0f}s"
    if value < 7200:
        return f"{value / 60:.1f}m"
    return f"{value / 3600:.1f}h"


def render_pm1_report_text(report: Mapping[str, Any]) -> str:
    """The report as ASCII (Windows consoles are cp949)."""
    window = report.get("window") or {}
    lines = [
        f"PM1 report {report.get('report_version')}  generated {report.get('generated_at_utc')}",
        f"verdict: {report.get('verdict')}"
        f"  (exit artifact: {'yes' if report.get('is_exit_artifact') else 'NO'})",
        f"window : {window.get('first_observation_utc')} .. {window.get('last_observation_utc')}"
        f"  = {window.get('span_days')} of {window.get('required_days')} days required",
        f"reads  : {report.get('readable_count')}/{report.get('observation_count')} readable"
        f"  (coverage {report.get('coverage')})",
        f"totals : {report.get('pairing_count')} pairing(s), "
        f"{report.get('opportunity_count')} opportunity reading(s), "
        f"{report.get('episode_count')} episode(s)",
    ]
    incidents = report.get("incidents") or {}
    if any(incidents.values()):
        lines.append("incidents: " + ", ".join(f"{k}={v}" for k, v in sorted(incidents.items()) if v))

    for pairing in report.get("pairings") or []:
        legs = ", ".join(
            f"{leg.get('venue')}:{leg.get('market_id')}" for leg in (pairing.get("legs") or [])
        )
        lower, upper = pairing.get("duration_lower_seconds") or {}, pairing.get("duration_upper_seconds") or {}
        lines += [
            "",
            f"  {pairing.get('event_id')}  [{legs}]",
            f"    frequency : {pairing.get('opportunity_count')}/{pairing.get('readable_count')} readings"
            f"  rate={pairing.get('opportunity_rate')}",
            f"    net edge  : median={(pairing.get('opportunity_net_edge') or {}).get('median')}"
            f"  max={(pairing.get('opportunity_net_edge') or {}).get('max')}",
            f"    duration  : {pairing.get('episode_count')} episode(s), "
            f"witnessed median={_secs(lower.get('median'))} max={_secs(lower.get('max'))}"
            f"  / at most median={_secs(upper.get('median'))}",
        ]
        censored = [
            f"{pairing.get('episodes_open_ended')} still open" if pairing.get("episodes_open_ended") else "",
            f"{pairing.get('episodes_gap_ended')} cut by a gap" if pairing.get("episodes_gap_ended") else "",
            f"{pairing.get('episodes_open_started')} already running" if pairing.get("episodes_open_started") else "",
        ]
        censored = [c for c in censored if c]
        if censored:
            lines.append(f"    censored  : {', '.join(censored)} (durations are lower bounds)")
    lines.append("")
    lines.append("Observation only. This report authorizes no position and no order.")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MAX_GAP_SECONDS",
    "EXIT_ARTIFACT_MIN_DAYS",
    "INSUFFICIENT_COVERAGE",
    "INSUFFICIENT_WINDOW",
    "MIN_COVERAGE",
    "NO_OBSERVATIONS",
    "REPORT_VERSION",
    "SUFFICIENT",
    "build_pm1_report",
    "episodes",
    "pairing_key",
    "render_pm1_report_text",
]
